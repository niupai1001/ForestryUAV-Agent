from __future__ import annotations

import hashlib
import json
import threading

from .contracts import Failure
from .resources import Resources
from .store import Store, encode, now, uid


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Delivery:
    """Candidate -> checks -> explicit acceptance -> sealed manifest -> terminal.

    User attestation is labelled as such and never becomes scientific accuracy evidence.
    The manifest lists hashes of outputs but does not hash itself.
    """

    def __init__(self, store: Store, resources: Resources):
        self.store, self.resources = store, resources
        self.lock = threading.RLock()

    def candidate(self, task_id):
        state = self.store.task(task_id)
        if state["status"] != "waiting_user":
            raise Failure("PRECONDITION_MISSING", "仅完整回答且无未决作业的任务可以验收", 422)
        with self.store.transaction() as db:
            pending = db.execute("SELECT 1 FROM actions WHERE task_id=? AND status='pending'", (task_id,)).fetchone()
            active = db.execute("SELECT 1 FROM jobs WHERE task_id=? AND status NOT IN ('succeeded','failed','cancelled','timed_out')", (task_id,)).fetchone()
        if pending or active:
            raise Failure("RECONCILIATION_REQUIRED", "副作用或作业未确认结束，不能验收", 409)
        latest = next((m for m in reversed(state["messages"]) if m["role"] == "assistant"), None)
        if not latest or latest["spec_revision"] != state["spec_revision"]:
            raise Failure("VERSION_CONFLICT", "没有当前规范修订的回答", 409)
        artifacts = []
        for artifact_id in latest["artifact_ids"]:
            artifact = self.resources.artifact(artifact_id)
            if artifact["task_id"] != task_id:
                raise Failure("PERMISSION_DENIED", "候选引用不属于当前任务", 403)
            artifacts.append({k: artifact[k] for k in ["artifact_id", "name", "relative_path",
                "sha256", "size_bytes", "media_type", "producer_action_id"]})
        value = {"schema_version": 1, "task_id": task_id, "spec_revision": state["spec_revision"],
            "objective": state["objective"], "turn_id": latest["turn_id"], "answer": latest["text"],
            "limitations": latest["limitations"], "outputs": artifacts,
            "config_snapshot": state["config_snapshot"], "file_checks": "passed",
            "semantic_check": "awaiting_explicit_user_attestation", "scientific_accuracy": "not_assessed"}
        return {"candidate": value, "candidate_sha256": digest(value), "state_version": state["state_version"]}

    def accept(self, task_id: str, payload: dict):
        with self.lock:
            return self._accept(task_id, payload)

    def _accept(self, task_id: str, payload: dict):
        request = {"kind": "task.acceptance", "task_id": task_id, **payload}
        with self.store.transaction() as db:
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            prior = db.execute("SELECT payload FROM deliveries WHERE request_id=?", (payload["request_id"],)).fetchone()
        if prior:
            record = json.loads(prior["payload"])
            if record["request"] != request:
                raise Failure("VERSION_CONFLICT", "验收请求 ID 的载荷变化", 409)
            return self.publish(record)
        candidate = self.candidate(task_id)
        if candidate["candidate_sha256"] != payload["candidate_sha256"]:
            raise Failure("VERSION_CONFLICT", "候选产物或回答已变化，请刷新后验收", 409)
        with self.store.transaction() as db:
            state = self.store.read_task(db, task_id)
            if state["state_version"] != payload["expected_state_version"] or state["spec_revision"] != payload["expected_spec_revision"]:
                raise Failure("VERSION_CONFLICT", "状态或规范版本已变化", 409)
            if state["status"] != "waiting_user":
                raise Failure("VERSION_CONFLICT", "任务已不处于可验收状态", 409)
            user_event = self.store.user_event(db, "task_acceptance", request)
            state["acceptance"] = {"status": "user_confirmed" if payload["verdict"] == "accept" else "changes_requested",
                "user_event_id": user_event, "candidate_sha256": candidate["candidate_sha256"],
                "file_checks": "passed", "semantic_check": "user_attested" if payload["verdict"] == "accept" else "rejected",
                "scientific_accuracy": "not_assessed", "comment": payload["comment"], "at": now()}
            if payload["verdict"] == "request_changes":
                state.update(status="paused", pause_requested=True, blockers=[{
                    "code": "CHANGES_REQUESTED", "message": payload["comment"] or "用户要求修改"}])
                self.store.save_task(db, state, state["state_version"])
                self.store.event(db, task_id, "acceptance.changes_requested", state["acceptance"])
                return self.store.receipt(db, payload["request_id"], request, state)
            record = {"delivery_id": uid(), "task_id": task_id, "spec_revision": state["spec_revision"],
                "request": request, "candidate": candidate["candidate"], "acceptance": state["acceptance"],
                "manifest_artifact_id": None}
            db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?)", (record["delivery_id"], task_id,
                state["spec_revision"], payload["request_id"], encode(record), "sealing"))
            state.update(status="publishing", blockers=[])
            self.store.save_task(db, state, state["state_version"])
            self.store.event(db, task_id, "delivery.seal_intent", {"delivery_id": record["delivery_id"]})
        return self.publish(record)

    def publish(self, record):
        task_id = record["task_id"]
        # Recheck immutable bytes before publication, including restart recovery.
        for output in record["candidate"]["outputs"]:
            artifact = self.resources.artifact(output["artifact_id"])
            if artifact["sha256"] != output["sha256"]:
                raise Failure("VERIFICATION_FAILED", "封存候选发生变化", 422)
        manifest = {"schema_version": 1, "delivery_id": record["delivery_id"],
            "candidate": record["candidate"], "candidate_sha256": digest(record["candidate"]),
            "acceptance": record["acceptance"], "publication": "sealed",
            "manifest_hash_policy": "Manifest checksum is stored in the artifact registry; manifest does not include itself."}
        directory = self.resources.workspace / "tasks" / task_id / "delivery" / record["delivery_id"]
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "sealed-manifest.json"
        source.write_text(encode(manifest), encoding="utf-8")
        artifact = self.resources.import_file(task_id, record["delivery_id"], source,
                                               "delivery/" + record["delivery_id"] + "/sealed-manifest.json")
        record["manifest_artifact_id"] = artifact["artifact_id"]
        with self.store.transaction() as db:
            state = self.store.read_task(db, task_id)
            if state["status"] != "publishing" or state["spec_revision"] != record["spec_revision"]:
                raise Failure("VERSION_CONFLICT", "封存任务状态已变化，需要对账", 409)
            state.update(status="completed", blockers=[], delivery={
                "delivery_id": record["delivery_id"], "manifest_artifact_id": artifact["artifact_id"],
                "output_artifact_ids": [a["artifact_id"] for a in record["candidate"]["outputs"]]})
            self.store.save_task(db, state, state["state_version"])
            db.execute("UPDATE deliveries SET payload=?,status='published' WHERE id=?", (encode(record), record["delivery_id"]))
            self.store.event(db, task_id, "delivery.published", state["delivery"])
            self.store.event(db, task_id, "task.terminal", {"status": "completed"})
            return self.store.receipt(db, record["request"]["request_id"], record["request"], state)

    def recover(self):
        with self.store.transaction() as db:
            rows = db.execute("SELECT payload FROM deliveries WHERE status='sealing'").fetchall()
        for row in rows:
            record = json.loads(row["payload"])
            try:
                self.publish(record)
            except (Failure, OSError) as error:
                with self.store.transaction() as db:
                    self.store.event(db, record["task_id"], "delivery.blocked", {
                        "code": getattr(error, "code", "PUBLICATION_FAILED"), "message": str(error)})
