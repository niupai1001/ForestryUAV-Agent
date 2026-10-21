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

    def message_input(self, task_id, prompt, attachments, *, enabled, original_prompt):
        """A batch is a bounded index, never silently truncated image content."""
        from .conversation import IMAGE_SUFFIXES
        from .store import encode
        # TIFFs are scientific files: inspect/process via Jobs unless explicitly
        # requested as a bounded visual preview. Never auto-decode a GeoTIFF.
        scientific=[a for a in attachments if Path(a['name']).suffix.lower() in {'.tif', '.tiff', '.dng'}]
        images=[a for a in attachments if Path(a['name']).suffix.lower() in IMAGE_SUFFIXES and a not in scientific]
        if scientific:
            listing=[{k:a[k] for k in ('resource_id','name','kind')} for a in attachments[:8]]
            return (prompt+'\n科学影像以文件引用处理，未自动读取像素：'+encode(listing)+
                '\n使用list_inputs分页取得完整清单；元数据/拼接/反演交给process_imagery。'
                '普通图片可用view_image显式查看。不能把原始DN当反射率，不能猜波段。')
        if len(images)>1 or len(attachments)>8:
            listing=[{k:a[k] for k in ('resource_id','name','kind')} for a in attachments[:8]]
            return (original_prompt+'\n本条消息有'+str(len(attachments))+'个附件，其中'+str(len(images))+
                '张图片。这里只是前8项文件清单，尚未读取任何图像：'+encode(listing)+
                '\n使用list_inputs按offset分页取得全部来源，再按任务需要逐张或小批view_image。'
                '没有读到的图片不能声称看过。大量航片交给领域作业，不将整批像素持续放入对话。')
        if images and enabled:
            metadata,picture=self.prepare(task_id,resource_id=images[0]['resource_id'])
            return [prompt,'实际图像：'+encode(metadata),picture]
        return prompt

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
        if task_id:
            cache = self.resources.workspace / '.forestry' / 'image-previews' / task_id / sha / 'preview.png'
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
    tokens = 0
    for message in messages[start:]:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part,UserPromptPart) and isinstance(part.content,(list,tuple)):
                    for item in part.content:
                        if not isinstance(item,BinaryContent) or not item.media_type.startswith('image/'):
                            continue
                        bound = 4096
                        try:
                            with Image.open(io.BytesIO(item.data)) as image:
                                width,height = image.size
                                if 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE:
                                    # Qwen3.5's 16px patches merge2x2 before entering the
                                    # language context: one token per padded32px cell.
                                    # Keep a minimum and128 tokens of framing headroom.
                                    bound = max(256, ((width+31)//32)*((height+31)//32)+128)
                        except (OSError, ValueError, Image.DecompressionBombError):
                            pass
                        tokens += bound
    return tokens
