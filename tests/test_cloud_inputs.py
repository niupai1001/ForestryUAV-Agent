"""New input inspection: unknown science conditions stay unknown, unsafe headers fail."""
import hashlib
import struct

import laspy
import pytest
from pyproj import CRS

from forestry_agent.cloud_inputs import CloudDeclaration, inspect_cloud
from forestry_agent.contracts import Failure


def cloud(tmp_path, *, crs=None, oracle=False):
    header = laspy.LasHeader(version="1.2", point_format=3)
    if crs:
        header.add_crs(CRS.from_epsg(crs))
    if oracle:
        header.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type="uint32"))
    data = laspy.LasData(header)
    data.x, data.y, data.z = [100, 101, 102], [200, 201, 202], [150, 154, 160]
    data.classification = [2, 2, 5]
    target = tmp_path / "cloud.las"
    data.write(target)
    return target


def test_no_crs_or_height_evidence_is_invented_and_source_is_unchanged(tmp_path):
    path = cloud(tmp_path)
    before = path.read_bytes()
    result = inspect_cloud(path, hash_file=True)
    assert result["observed"]["horizontal_crs"] is None
    assert result["observed"]["z_semantics"] == "unknown"
    assert result["observed"]["z_unit"] == "unknown"
    assert result["prerequisites"]["ground_support"] == "not_assessed"
    assert not result["prerequisites"]["declared_elevation_metres"]
    assert result["source"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert path.read_bytes() == before


def test_conflicting_publisher_claim_and_oracle_dimensions_are_visible(tmp_path):
    path = cloud(tmp_path, crs=2154, oracle=True)
    declaration = CloudDeclaration(source_note="publisher assertion, independently unverified",
                                   horizontal_crs="EPSG:32631", z_semantics="elevation", z_unit="m")
    result = inspect_cloud(path, declaration)
    assert result["prerequisites"]["crs_conflict"]
    assert result["observed"]["horizontal_crs"] == "EPSG:2154"
    assert result["observed"]["extra_dimensions"] == ["treeID"]
    assert result["prerequisites"]["ground_support"] == "not_assessed"
    assert result["observed"]["z_unit"] == "unknown"
    assert result["declared"]["z_unit"] == "m"


def test_truncated_point_records_are_rejected_before_sampling(tmp_path):
    path = cloud(tmp_path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(Failure) as error:
        inspect_cloud(path)
    assert error.value.code == "CLOUD_INVALID"


def test_forged_large_point_offset_cannot_allocate_unbounded_metadata(tmp_path):
    path = cloud(tmp_path)
    content = bytearray(path.read_bytes())
    struct.pack_into("<I", content, 96, 0x7fffffff)
    path.write_bytes(content)
    with pytest.raises(Failure) as error:
        inspect_cloud(path)
    assert error.value.code == "INPUT_LIMIT"


def test_invalid_declared_crs_returns_contract_error(tmp_path):
    declaration = CloudDeclaration(source_note="explicit invalid operator assertion",
                                   horizontal_crs="EPSG:not-a-code")
    with pytest.raises(Failure) as error:
        inspect_cloud(cloud(tmp_path), declaration)
    assert error.value.code == "SCHEMA_INVALID"
