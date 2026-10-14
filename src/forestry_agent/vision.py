"""Local, scoped image input with bounded decoded size and preserved source bytes."""
from __future__ import annotations

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic_ai import BinaryContent

from .contracts import Failure
from .resources import file_hash, native_path, version

MAX_BYTES = 20 * 1024**2
MAX_PIXELS = 25_000_000
MAX_EDGE = 1024


class Vision:
    def __init__(self, resources):
        self.resources = resources

    def prepare(self, task_id, *, resource_id=None, artifact_id=None, relative_path=''):
        if bool(resource_id) == bool(artifact_id):
            raise Failure('SCHEMA_INVALID', '请选择一个来源图片或本会话图片产物')
        if artifact_id:
            artifact = self.resources.artifact(artifact_id, task_id)
            path = Path(artifact['locator'])
        else:
            path = self.resources.resolve(resource_id, relative_path, task_id)
        before = version(path)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise Failure('INPUT_LIMIT', '直接读图支持20 MiB以内图片；更大影像请提供有界预览或分块', 422)
        sha = file_hash(path)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(native_path(path)) as opened:
                    if opened.width * opened.height > MAX_PIXELS:
                        raise Failure('INPUT_LIMIT', '图片超过2500万像素，请提供缩小预览或分块', 422)
                    original_size = [opened.width, opened.height]
                    image = ImageOps.exif_transpose(opened).convert('RGB')
                    image.thumbnail((MAX_EDGE, MAX_EDGE))
                    out = io.BytesIO()
                    image.save(out, format='PNG')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise Failure('PRECONDITION_MISSING', '图片无法安全解码，请提供PNG、JPEG或WebP图片', 422) from error
        if version(path) != before or file_hash(path) != sha:
            raise Failure('VERSION_CONFLICT', '读图期间文件发生变化，请重新发送', 409)
        data = out.getvalue()
        cache = self.resources.workspace / '.forestry' / 'image-previews' / sha / 'preview.png'
        native_path(cache.parent).mkdir(parents=True, exist_ok=True)
        native_path(cache).write_bytes(data)
        metadata = {'name':path.name, 'resource_id':resource_id, 'artifact_id':artifact_id,
                    'relative_path':relative_path, 'source_sha256':sha, 'source_version':before,
                    'original_size':original_size, 'preview_size':list(image.size), 'preview_media_type':'image/png',
                    'preview_policy':'first_frame_exif_orientation_max_edge_1024; original_unchanged'}
        return metadata, BinaryContent(data=data, media_type='image/png', identifier=resource_id or artifact_id)


def visual_token_bound(messages):
    """Only images after the most recent measured response are unpaid."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
    start = max((i+1 for i,m in enumerate(messages) if isinstance(m,ModelResponse) and m.usage.input_tokens>0), default=0)
    count = 0
    for message in messages[start:]:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part,UserPromptPart) and isinstance(part.content,(list,tuple)):
                    count += sum(isinstance(item,BinaryContent) and item.media_type.startswith('image/') for item in part.content)
    # Fixed previews are at most1024², on the observed local qwen35 vision backend.
    return count * 4096
