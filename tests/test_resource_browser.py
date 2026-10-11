from forestry_agent.contracts import Failure


def test_source_preview_is_bounded_readonly_and_rejects_mixed_versions(client, app, tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    code = source / "中文.py"
    original = ("# 样本来源\n" * 1800 + "print('source end')\n").encode("utf-8")
    code.write_bytes(original)
    resource = client.post("/resources/register", json={"request_id": "preview-root", "path": str(source)}).json()
    url = f"/resources/{resource['resource_id']}/preview"
    first = client.get(url, params={"relative_path": code.name}).json()
    assert first["has_more"] and first["next_page"] == 1 and len(first["text"].encode()) <= 12003
    extracted, window = first["text"], first
    while window["has_more"]:
        window = client.get(url, params={"relative_path": code.name, "page": window["next_page"],
                                         "expected_version": first["source_version"]}).json()
        extracted += window["text"]
    assert extracted.encode() == original
    assert code.read_bytes() == original and app.state.store.tasks() == []
    code.write_bytes(b"changed")
    assert client.get(url, params={"relative_path": code.name, "page": 1,
                                  "expected_version": first["source_version"]}).status_code == 409
    assert client.get(url, params={"relative_path": "../outside.txt"}).status_code == 403
    assert client.get("/resources/unregistered/preview").status_code == 403
    (source / "binary.bin").write_bytes(b"\0\0binary")
    assert client.get(url, params={"relative_path": "binary.bin"}).status_code == 422


def test_child_file_grant_inherits_classification_and_does_not_amend_tasks(client, app, tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    file = source / "ledger.csv"
    file.write_bytes(b"name,value\na,2\n")
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"private secret")
    parent = client.post("/resources/register", json={"request_id": "file-root", "path": str(source),
                                                     "classification": "private"}).json()
    task = client.post("/tasks", json={"request_id": "unamended", "objective": "Existing task",
                                      "input_refs": [parent["resource_id"]]}).json()
    url = f"/resources/{parent['resource_id']}/files"
    payload = {"request_id": "child-file", "relative_path": file.name}
    response = client.post(url, json=payload)
    assert response.status_code == 200
    child = response.json()
    assert child["classification"] == "private" and child["read_only"] is True and child["kind"] == "file"
    assert client.post(url, json=payload).json() == child
    assert client.get(f"/tasks/{task['task_id']}").json()["input_refs"] == [parent["resource_id"]]
    assert client.post(url, json={"request_id": "escape", "relative_path": "../outside.csv"}).status_code == 403
    assert client.post(url, json={"request_id": "downgrade", "relative_path": file.name,
                                  "classification": "public"}).status_code == 400
    assert client.post(url, json={"request_id": "directory", "relative_path": "."}).status_code == 422
    assert file.read_bytes() == b"name,value\na,2\n"
    try:
        app.state.resources.resolve(child["resource_id"], "../outside.csv")
    except Failure as error:
        assert error.code == "PERMISSION_DENIED"
    else:
        raise AssertionError("Single-file grant expanded")
