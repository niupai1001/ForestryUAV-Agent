"""Read-only UAV image-set audit with no photogrammetry backend."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from PIL import Image

from ...exec.paths import is_within
from ...storage import AssetError


IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.tif', '.tiff', '.dng'}
GEOSPATIAL_SUFFIXES = {'.tif', '.tiff', '.vrt'}
PRODUCT_TOKENS = {
    'orthomosaic': ('orthomosaic', 'orthophoto', '正射影像', '正射', 'dom'),
    'dsm': ('dsm', 'digital_surface', '数字表面'),
    'dtm': ('dtm', 'digital_terrain', '数字地形'),
    'chm': ('chm', 'canopy_height', '冠层高度'),
    'ndvi': ('ndvi',),
}
XMP_ATTRIBUTE = re.compile(
    rb'(?:drone-dji|Camera|MicaSense|Sentera|FLIR|DLS):'
    rb'([A-Za-z0-9_-]+)="([^"]*)"'
)


@dataclass(frozen=True)
class InputImage:
    name: str
    path: Path


def _read_xmp(path: Path) -> dict[str, str]:
    # JPEG XMP lives in an APP1 segment near the header. Keep inspection
    # bounded so one metadata check never loads a multi-gigabyte UAV image.
    chunks = []
    remaining = 512 * 1024
    with path.open('rb') as handle:
        while remaining:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    raw = b''.join(chunks)
    return {
        key.decode('ascii'): value.decode('utf-8', errors='replace')
        for key, value in XMP_ATTRIBUTE.findall(raw)
    }


def _read_exif(path: Path) -> dict[str, str | bool | None]:
    with Image.open(path) as image:
        exif = image.getexif()
        make = exif.get(271)
        model = exif.get(272)
        captured = None
        try:
            captured = exif.get_ifd(34665).get(36867)
        except Exception:
            pass
        captured = captured or exif.get(306)
        try:
            gps = exif.get_ifd(34853)
            has_gps = bool(gps.get(2) and gps.get(4))
        except Exception:
            has_gps = False
    return {
        'make': str(make).strip().strip('\x00') if make else None,
        'model': str(model).strip().strip('\x00') if model else None,
        'captured_at': str(captured).strip() if captured else None,
        'has_gps': has_gps,
    }


def inspect_images(
    images: list[InputImage],
    *,
    source_type: str,
    source_label: str,
) -> dict[str, Any]:
    """Inspect image metadata without modifying source files."""
    if not images:
        raise AssetError('没有找到可检查的 JPG、TIF、TIFF 或 DNG 影像')
    duplicate_names = sorted(
        name for name, count in Counter(i.name.lower() for i in images).items()
        if count > 1
    )
    makes: set[str] = set()
    models: set[str] = set()
    captured_times: list[str] = []
    band_counts: Counter[str] = Counter()
    capture_groups: dict[str, list[str]] = defaultdict(list)
    capture_id_sources: Counter[str] = Counter()
    gps_count = 0
    band_metadata_count = 0
    irradiance_count = 0
    radiometric_count = 0
    vignetting_count = 0
    distortion_count = 0
    rtk_codes: Counter[str] = Counter()
    read_errors: list[str] = []

    digest = hashlib.sha256()
    for item in sorted(images, key=lambda value: value.name.lower()):
        try:
            stat = item.path.stat()
            digest.update(item.name.lower().encode('utf-8'))
            digest.update(str(stat.st_size).encode('ascii'))
            exif = _read_exif(item.path)
            xmp = _read_xmp(item.path)
        except Exception as exc:
            read_errors.append(f'{item.name}: {type(exc).__name__}')
            continue
        if exif['make']:
            makes.add(str(exif['make']))
        if exif['model']:
            models.add(str(exif['model']))
        if exif['captured_at']:
            captured_times.append(str(exif['captured_at']))
        band_name = xmp.get('BandName')
        if band_name:
            band_counts[band_name] += 1
            band_metadata_count += 1
            if any(xmp.get(key) for key in (
                'HorizontalIrradiance', 'SpectralIrradiance',
                'Irradiance', 'SunSensor',
            )):
                irradiance_count += 1
            if any(xmp.get(key) for key in (
                'RadiometricCalibration', 'SensorGain',
                'SensorGainAdjustment', 'BlackLevel',
            )):
                radiometric_count += 1
            if any(xmp.get(key) for key in (
                'VignettingData', 'VignettingPolynomial',
                'VignettingCenter',
            )):
                vignetting_count += 1
            if any(xmp.get(key) for key in (
                'DewarpData', 'DewarpHMatrix',
                'CalibratedFocalLength',
            )):
                distortion_count += 1
        capture_id = None
        for key in ('CaptureUUID', 'CaptureId', 'ImageUniqueID'):
            if xmp.get(key):
                capture_id = xmp[key]
                capture_id_sources[key] += 1
                break
        if capture_id:
            capture_groups[capture_id].append(item.name)
        latitude = xmp.get('GpsLatitude') or xmp.get('GPSLatitude')
        longitude = xmp.get('GpsLongitude') or xmp.get('GPSLongitude')
        if (latitude and longitude) or exif['has_gps']:
            gps_count += 1
        if xmp.get('RtkFlag'):
            rtk_codes[xmp['RtkFlag']] += 1

    mode = 'multispectral' if len(band_counts) >= 2 else 'rgb'
    capture_count = len(capture_groups) or len(images)
    problems: list[str] = []
    warnings: list[str] = []
    if read_errors:
        problems.append(f'{len(read_errors)} 张影像无法读取元数据')
    if duplicate_names:
        warnings.append('不同子目录存在同名影像；交给外部摄影测量后端前需保持批次边界')
    if gps_count == 0:
        problems.append('没有影像包含可识别的 GPS 坐标')
    elif gps_count != len(images):
        warnings.append(f'仅 {gps_count}/{len(images)} 个文件含可识别 GPS')
    if mode == 'multispectral':
        counts = list(band_counts.values())
        if len(set(counts)) > 1:
            problems.append('多光谱波段影像数量不一致')
        if capture_groups and any(count != len(capture_groups) for count in counts):
            problems.append('波段数量与元数据航片组数量不一致')
    radiometric_ready = bool(
        mode == 'multispectral'
        and band_metadata_count
        and irradiance_count == band_metadata_count
        and radiometric_count == band_metadata_count
    )
    if mode == 'multispectral' and not radiometric_ready:
        warnings.append('多光谱影像的辐照度或相机校正元数据不完整')

    return {
        'ready': not problems,
        'assessment_scope': 'basic_metadata_for_georeferenced_photogrammetry',
        'ready_meaning': (
            '基本文件、元数据、GPS和波段一致性检查未发现阻断项；'
            '不代表外部摄影测量一定成功，也不代表历史正射成果不存在。'
        ),
        'source_type': source_type,
        'source': source_label,
        'fingerprint': digest.hexdigest(),
        'image_file_count': len(images),
        'total_size_bytes': sum(item.path.stat().st_size for item in images),
        'capture_count': capture_count,
        'capture_count_source': (
            capture_id_sources.most_common(1)[0][0]
            if capture_id_sources else 'image_count_fallback'
        ),
        'processing_mode': mode,
        'camera_makes': sorted(makes),
        'camera_models': sorted(models),
        'band_names': sorted(band_counts),
        'band_image_counts': dict(sorted(band_counts.items())),
        'gps_file_count': gps_count,
        'metadata_coverage': {
            'band_metadata_files': band_metadata_count,
            'irradiance_files': irradiance_count,
            'radiometric_calibration_files': radiometric_count,
            'vignetting_files': vignetting_count,
            'distortion_files': distortion_count,
            'rtk_metadata_files': sum(rtk_codes.values()),
        },
        'rtk_status_interpretation': (
            f'{sum(rtk_codes.values())}个文件含未分类RTK元数据；当前检查器'
            '没有厂商状态码定义，不返回原始码，也不得据此推断RTK定位成功、'
            '固定解或定位精度。'
        ),
        'radiometric_calibration_ready': radiometric_ready,
        'recommended_radiometric_calibration': (
            'camera' if radiometric_ready else 'none'
        ),
        'capture_start': min(captured_times) if captured_times else None,
        'capture_end': max(captured_times) if captured_times else None,
        'problems': problems,
        'warnings': warnings,
        'read_error_examples': read_errors[:5],
        'duplicate_name_examples': duplicate_names[:5],
    }


def geospatial_product_role(path: Path) -> str | None:
    """Classify named geospatial deliverables without guessing spectral bands."""
    normalized = path.as_posix().casefold()
    for role, tokens in PRODUCT_TOKENS.items():
        if any(token.casefold() in normalized for token in tokens):
            return role
    return None


def dataset_image_role(relative: Path) -> str:
    normalized = relative.as_posix().casefold()
    if geospatial_product_role(relative):
        return 'geospatial_product'
    if any(token in normalized for token in ('起飞前', 'before_flight', 'preflight')):
        return 'reference_panel_before'
    if any(token in normalized for token in ('起飞后', 'after_flight', 'postflight')):
        return 'reference_panel_after'
    if any(token in normalized for token in ('参考板', 'reflectance_panel', 'calibration_panel')):
        return 'reference_panel_unspecified'
    return 'flight_imagery'


class InputPathMapper:
    def __init__(self, container_input_root: Path, host_input_root: str):
        self.input_root = container_input_root.resolve()
        self.host_input_root = host_input_root.strip()

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        return is_within(path, root)

    def input_path(self, supplied: str) -> Path:
        value = str(supplied or '').strip()
        if not value:
            raise AssetError('folder_path 不能为空')
        if re.match(r'^[A-Za-z]:[\\/]', value):
            if not re.match(r'^[A-Za-z]:[\\/]', self.host_input_root):
                raise AssetError('当前部署没有配置 Windows 宿主机输入根目录')
            try:
                relative = PureWindowsPath(value).relative_to(
                    PureWindowsPath(self.host_input_root)
                )
            except ValueError as exc:
                raise AssetError(
                    f'路径不在允许的数据根目录中：{self.host_input_root}'
                ) from exc
            candidate = self.input_root.joinpath(*relative.parts)
        else:
            normalized = value.replace('\\', '/')
            if normalized == str(self.input_root):
                candidate = self.input_root
            elif normalized.startswith(str(self.input_root) + '/'):
                candidate = Path(normalized)
            else:
                candidate = self.input_root.joinpath(*PurePosixPath(normalized).parts)
        resolved = candidate.resolve()
        if not self._within(resolved, self.input_root):
            raise AssetError('输入路径越过允许的数据根目录')
        if not resolved.is_dir():
            raise AssetError(f'影像文件夹不存在：{supplied}')
        return resolved


class UavInspectionService:
    def __init__(
        self,
        mapper: InputPathMapper | None = None,
        temp_root: Path | None = None,
    ):
        self.temp_root = Path(
            temp_root or os.getenv('DATA_ROOT', tempfile.gettempdir())
        ).resolve()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.mapper = mapper or InputPathMapper(
            Path(os.getenv('UAV_INPUT_ROOT', '/uav-input')),
            os.getenv('UAV_INPUT_HOST_ROOT', ''),
        )

    def _path_images(
        self, folder_path: str, recursive: bool
    ) -> tuple[Path, list[InputImage]]:
        directory = self.mapper.input_path(folder_path)
        candidates = directory.rglob('*') if recursive else directory.iterdir()
        images = [
            InputImage(path.name, path)
            for path in sorted(candidates)
            if (
                path.is_file()
                and not path.is_symlink()
                and self.mapper._within(path.resolve(), self.mapper.input_root)
                and path.suffix.lower() in IMAGE_SUFFIXES
            )
        ]
        return directory, images

    @staticmethod
    def _safe_archive_member(info: zipfile.ZipInfo) -> bool:
        path = PurePosixPath(info.filename.replace('\\', '/'))
        mode = info.external_attr >> 16
        return (
            not info.is_dir()
            and not info.flag_bits & 0x1
            and path.is_absolute() is False
            and '..' not in path.parts
            and not (mode and (mode & 0o170000) == 0o120000)
        )

    def _uploaded_images(
        self,
        assets: list[dict[str, Any]],
        archive_directory: Path,
    ) -> list[InputImage]:
        images: list[InputImage] = []
        names: set[str] = set()
        max_members = int(os.getenv('UAV_ARCHIVE_MAX_FILES', '1000'))
        max_unpacked = int(os.getenv('UAV_ARCHIVE_MAX_BYTES', '4294967296'))
        max_ratio = int(os.getenv('UAV_ARCHIVE_MAX_RATIO', '200'))
        unpacked = 0
        archive_directory.mkdir(parents=True, exist_ok=True)
        for asset in assets:
            name = str(asset['name'])
            path = Path(asset['path'])
            suffix = Path(name).suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                key = Path(name).name.lower()
                if key in names:
                    raise AssetError(f'上传文件存在重复名称：{name}')
                names.add(key)
                images.append(InputImage(Path(name).name, path))
            elif suffix == '.zip':
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > max_members:
                        raise AssetError(f'ZIP 条目超过限制 {max_members}')
                    for info in members:
                        member_suffix = PurePosixPath(
                            info.filename.replace('\\', '/')
                        ).suffix.lower()
                        if member_suffix not in IMAGE_SUFFIXES:
                            continue
                        if not self._safe_archive_member(info):
                            raise AssetError(f'ZIP 包含不安全条目：{info.filename}')
                        if info.file_size > max(1, info.compress_size) * max_ratio:
                            raise AssetError(f'ZIP 条目压缩比过高：{info.filename}')
                        unpacked += info.file_size
                        if unpacked > max_unpacked:
                            raise AssetError('ZIP 解压后的影像超过配置上限')
                        output_name = PurePosixPath(
                            info.filename.replace('\\', '/')
                        ).name
                        key = output_name.lower()
                        if key in names:
                            raise AssetError(f'ZIP 中存在重复影像名称：{output_name}')
                        names.add(key)
                        output = archive_directory / output_name
                        with archive.open(info) as source, output.open('wb') as target:
                            shutil.copyfileobj(source, target, 1024 * 1024)
                        images.append(InputImage(output_name, output))
        return images

    def inspect_folder(self, folder_path: str, recursive: bool = False) -> dict[str, Any]:
        directory, images = self._path_images(folder_path, recursive)
        return inspect_images(
            images,
            source_type='mounted_path',
            source_label=folder_path,
        ) | {'container_path': str(directory)}

    def inventory_dataset(
        self, folder_path: str, max_files: int = 5000,
    ) -> dict[str, Any]:
        """Build a bounded, read-only role manifest for one UAV dataset root."""
        directory = self.mapper.input_path(folder_path)
        files: list[Path] = []
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if not self.mapper._within(resolved, self.mapper.input_root):
                continue
            files.append(path)
            if len(files) > max_files:
                raise AssetError(
                    f'数据集文件数超过单次盘点上限 {max_files}；请选择更具体的目录'
                )

        role_paths: dict[str, list[str]] = defaultdict(list)
        group_images: dict[tuple[str, str], list[InputImage]] = defaultdict(list)
        product_roles: Counter[str] = Counter()
        ignored_suffixes: Counter[str] = Counter()
        for path in files:
            relative = path.relative_to(directory)
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                role = dataset_image_role(relative)
                role_paths[role].append(relative.as_posix())
                if role == 'geospatial_product':
                    product_roles[geospatial_product_role(relative) or 'unclassified'] += 1
                else:
                    parent = relative.parent.as_posix()
                    group_images[(role, parent)].append(
                        InputImage(relative.as_posix(), path)
                    )
            elif suffix in GEOSPATIAL_SUFFIXES:
                role = geospatial_product_role(relative)
                if role:
                    role_paths['geospatial_product'].append(relative.as_posix())
                    product_roles[role] += 1
                else:
                    role_paths['unclassified_raster'].append(relative.as_posix())
            else:
                ignored_suffixes[suffix or '<none>'] += 1

        groups = []
        for (role, parent), images in sorted(group_images.items()):
            inspection = inspect_images(
                images,
                source_type='dataset_group',
                source_label=parent or '.',
            )
            groups.append({
                'role': role,
                'relative_directory': parent or '.',
                'image_file_count': inspection['image_file_count'],
                'capture_count': inspection['capture_count'],
                'processing_mode': inspection['processing_mode'],
                'camera_models': inspection['camera_models'],
                'band_image_counts': inspection['band_image_counts'],
                'gps_file_count': inspection['gps_file_count'],
                'radiometric_calibration_ready': inspection[
                    'radiometric_calibration_ready'
                ],
                'problems': inspection['problems'],
                'warnings': inspection['warnings'],
            })

        roles = {}
        for role, paths in sorted(role_paths.items()):
            roles[role] = {
                'file_count': len(paths),
                'exact_relative_paths': paths[:100],
                'paths_truncated': len(paths) > 100,
            }
        return {
            'manifest_type': 'uav_dataset_manifest',
            'manifest_version': 1,
            'observation_complete': True,
            'recommended_next_action': 'answer_from_this_manifest',
            'source': folder_path,
            'container_path': str(directory),
            'total_file_count': len(files),
            'roles': roles,
            'acquisition_groups': groups,
            'geospatial_product_counts': dict(sorted(product_roles.items())),
            'ignored_file_counts_by_suffix': dict(
                ignored_suffixes.most_common(20)
            ),
            'separation_rule': (
                'flight_imagery、起飞前/起飞后参考板和地理成果保持独立；'
                '本清单不把它们合并为同一摄影测量输入。'
            ),
        }

    def inspect_uploads(self, assets: list[dict[str, Any]]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            images = self._uploaded_images(assets, Path(folder))
            return inspect_images(
                images,
                source_type='chat_uploads',
                source_label='当前聊天上传的影像或 ZIP',
            )
