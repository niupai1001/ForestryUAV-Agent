import hashlib
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_core import task

from forestry_agent.contracts import Failure


def grant(client, path, request_id="source"):
    response = client.post("/resources/register", json={"request_id": request_id, "path": str(path)})
    assert response.status_code == 200
    return response.json()["resource_id"]


def test_chinese_retrieval_cites_current_authorized_source_only(client, app, tmp_path):
    source = tmp_path / "forest.md"
    source.write_text("冠层覆盖率使用有效空间支持范围计算。冠幅是树冠的横向尺度。", encoding="utf-8")
    ref = grant(client, source)
    state, _ = task(client, input_refs=[ref])
    knowledge = app.state.engine.knowledge
    first = knowledge.index(state["task_id"], ref)
    assert knowledge.index(state["task_id"], ref)["chunk_ids"] == first["chunk_ids"]
    result = knowledge.search(state["task_id"], "冠层覆盖率")
    assert result["matches"] and "空间支持" in result["matches"][0]["text"]
    pointer = result["matches"][0]["citation"]
    assert pointer["resource_id"] == ref
    assert pointer["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert knowledge.search(state["task_id"], '" OR impossible_term --')["matches"] == []
    other, _ = task(client, request_id="other-task")
    assert knowledge.search(other["task_id"], "冠层覆盖率")["matches"] == []
    source.write_text("输入文件已修改，需要重新索引。", encoding="utf-8")
    assert knowledge.search(state["task_id"], "冠层覆盖率")["matches"] == []


def test_docx_logical_text_and_malformed_document(client, app, tmp_path):
    source = tmp_path / "report.docx"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>林业</w:t></w:r><w:r><w:t>报告</w:t></w:r></w:p></w:body></w:document>')
    ref = grant(client, source)
    state, _ = task(client, input_refs=[ref])
    result = app.state.engine.documents.read(state["task_id"], ref)
    assert result["text"] == "林业报告"
    assert result["citation"]["unit"] == "document_text" and result["page_count"] is None
    with pytest.raises(Failure):
        app.state.engine.documents.read(state["task_id"], ref, page=1)
    source.write_bytes(b"not a docx archive")
    with pytest.raises(Failure, match="格式损坏"):
        app.state.engine.documents.read(state["task_id"], ref)


def test_real_pdf_text_page_and_blank_page_does_not_fake_ocr(client, app, tmp_path):
    source = tmp_path / "fixture.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 200 Td (Canopy height 12.5 m) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(300, 300)
    writer.write(source)
    ref = grant(client, source)
    state, _ = task(client, input_refs=[ref])
    result = app.state.engine.documents.read(state["task_id"], ref)
    assert "Canopy height 12.5 m" in result["text"]
    assert result["page_count"] == 2 and result["next_page"] == 1
    assert result["citation"]["unit"] == "page:1"
    with pytest.raises(Failure, match="OCR 尚未启用"):
        app.state.engine.documents.read(state["task_id"], ref, page=1)


def test_memory_requires_explicit_user_save_and_can_be_excluded(client, app):
    payload = {"request_id": "save-note", "text": "报告使用中文，单位使用米。", "tags": ["偏好"]}
    assert client.post("/memory", json={**payload, "user_event_id": "forged"}).status_code == 400
    note = client.post("/memory", json=payload).json()
    assert client.post("/memory", json=payload).json() == note
    assert client.post("/memory", json={**payload, "text": "changed"}).status_code == 409
    assert client.get("/memory").json()["notes"] == [note]
    delete = {"request_id": "forget-note"}
    reply = client.request("DELETE", "/memory/" + note["memory_id"], json=delete)
    assert reply.json()["status"] == "forgotten"
    assert client.request("DELETE", "/memory/" + note["memory_id"], json=delete).json() == reply.json()
    assert client.get("/memory").json()["notes"] == []
    with app.state.store.transaction() as db:
        assert db.execute("SELECT kind FROM user_events WHERE id=(SELECT event_id FROM memories LIMIT 1)").fetchone() is None
        assert db.execute("SELECT COUNT(*) FROM user_events WHERE kind='explicit_memory_save'").fetchone()[0] == 1
