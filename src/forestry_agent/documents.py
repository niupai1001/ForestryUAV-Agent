from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader, apply_configuration
from pypdf.errors import PyPdfError

from .contracts import Failure
from .resources import Resources, file_hash, version


class Documents:
    """Bounded format readers return source citations, never user authorizations."""

    def __init__(self, resources: Resources):
        self.resources = resources

    def read(self, task_id: str, resource_id: str, relative_path: str = "", page: int = 0):
        try:
            return self._read(task_id, resource_id, relative_path, page)
        except (PyPdfError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as error:
            raise Failure("PRECONDITION_MISSING", "文档格式损坏或提取受限：" + str(error)[:1000], 422) from error

    def _read(self, task_id: str, resource_id: str, relative_path: str, page: int):
        if page < 0:
            raise Failure("SCHEMA_INVALID", "页码必须从 0 开始")
        path = self.resources.resolve(resource_id, relative_path, task_id)
        if not path.is_file():
            raise Failure("PRECONDITION_MISSING", "文档目标须为文件", 422)
        before = version(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise Failure("RESOURCE_EXHAUSTED", "当前文档读取器上限为 64 MiB；大文件须流式专用作业", 422)
        suffix = path.suffix.lower()
        source_hash = file_hash(path)
        if suffix == ".pdf":
            with apply_configuration(maximum_declared_stream_length=16 * 1024 * 1024,
                    array_based_stream_maximum_output_length=16 * 1024 * 1024,
                    zlib_maximum_output_length=16 * 1024 * 1024,
                    lzw_maximum_output_length=16 * 1024 * 1024,
                    run_length_maximum_output_length=16 * 1024 * 1024,
                    page_tree_maximum_entries=5000, xform_maximum_invocations_per_extraction=100,
                    jbig2dec_binary=None, disable_legacy_handling=True), path.open("rb") as stream:
                reader = PdfReader(stream, root_object_recovery_limit=1000)
                if reader.is_encrypted:
                    raise Failure("PRECONDITION_MISSING", "文档加密，需要解密后的授权副本", 422)
                total = len(reader.pages)
                if page >= total:
                    raise Failure("PRECONDITION_MISSING", "页码超出文档范围", 422)
                text = reader.pages[page].extract_text() or ""
            if not text.strip():
                raise Failure("PRECONDITION_MISSING", "该页没有可提取文本；OCR 尚未启用，不能编造内容", 422)
            unit = f"page:{page + 1}"
            has_more = page + 1 < total
        elif suffix == ".docx":
            if page:
                raise Failure("PRECONDITION_MISSING", "DOCX 读取器返回整体逻辑文本，没有可靠版面页码", 422)
            with zipfile.ZipFile(path) as archive:
                member = archive.getinfo("word/document.xml")
                if member.file_size > 8 * 1024 * 1024 or member.compress_size and member.file_size / member.compress_size > 200:
                    raise Failure("RESOURCE_EXHAUSTED", "文档 XML 或压缩倍率超限", 422)
                xml = archive.read(member)
            if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                raise Failure("PERMISSION_DENIED", "拒绝文档实体声明", 403)
            tree = ElementTree.fromstring(xml)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join("".join(t.text or "" for t in node.findall(".//w:t", namespace))
                             for node in tree.findall(".//w:p", namespace))
            unit, total, has_more = "document_text", None, False
        elif suffix in {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".xml"}:
            result = self.resources.read(resource_id, relative_path, page * 12000, task_id=task_id)
            if version(path) != before or file_hash(path) != source_hash:
                raise Failure("VERSION_CONFLICT", "提取期间源文档发生变化", 409)
            return {**result, "citation": {"resource_id": resource_id, "relative_path": relative_path,
                "source_version": result["version"], "source_sha256": source_hash,
                "unit": f"bytes:{result['offset']}:{result['next']}"},
                "next_page": page + 1 if result["has_more"] else None}
        else:
            raise Failure("PRECONDITION_MISSING", "格式尚无已验证读取器", 422)
        if version(path) != before or file_hash(path) != source_hash:
            raise Failure("VERSION_CONFLICT", "提取期间源文档发生变化", 409)
        if len(text) > 60000:
            raise Failure("RESOURCE_EXHAUSTED", "单页/逻辑文档文本超出上限，需分块读取器", 422)
        return {"text": text, "page": page, "page_count": total, "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "citation": {"resource_id": resource_id, "relative_path": relative_path,
                         "source_version": before, "source_sha256": source_hash, "unit": unit}}


def tokens(text: str):
    # Stable Chinese bigrams plus individual Han characters support short queries.
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(sequence)
        terms.extend(sequence[i:i + 2] for i in range(len(sequence) - 1))
    return " ".join(terms)
