"""Actual deep paths from ODM exposed a cleanup bug; no historical session deletion."""
from types import SimpleNamespace

from forestry_agent.resources import native_path
from forestry_agent.sessions import Sessions


def test_owned_tree_removes_long_descendants_even_when_root_is_short(tmp_path):
    root = (tmp_path/'runtime').resolve()
    owned = root/'tasks'/'owned-fixture'
    file = owned/('cache-'+'x'*70)/('mesh-'+'y'*70)/('texture-'+'z'*70)/'result.bin'
    native_path(file.parent,force=True).mkdir(parents=True)
    native_path(file,force=True).write_bytes(b'cleanup-only fixture, no scientific product')
    external = tmp_path/'original.bin'
    external.write_bytes(b'keep')
    sessions = Sessions(SimpleNamespace(store=None, resources=SimpleNamespace(workspace=root)))
    sessions._remove_path(str(owned))
    assert not owned.exists()
    assert external.read_bytes() == b'keep' and root.exists()
