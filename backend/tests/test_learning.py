"""Learning boundaries and complete flows; all model outputs are explicit test fixtures."""

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.models import (
    Chunk,
    Document,
    LearningAttempt,
    LearningJob,
    LearningSourceSnapshot,
    User,
    new_id,
    utcnow,
)
from app.services.corpus import CorpusScope
from app.services.learning.assessment import validate_assessment
from app.services.learning.jobs import process_learning
from app.services.learning.planning import templates
from sqlalchemy import delete, select, update
from test_api import create_kb, register, upload, wait_job

MATERIAL = """事务原子性保证全部操作完成或者回滚。隔离性控制并发事务相互影响。
并发交错执行时，两个请求读取库存1，各自根据旧值写入0并提交订单成功，可能丢失更新并超卖。
隔离级别、正确的锁和条件更新可以控制并发风险。条件扣减后检查影响行数，订单和扣减在同一事务提交。
重复请求还需要业务幂等约束；发生冲突可能需要重试。"""


def wait_learning(client, headers, wid, kind="plan"):
    for _ in range(250):
        w = client.get(f"/api/learning/workspaces/{wid}", headers=headers).json()
        jobs = [j for j in w["jobs"] if j["kind"] == kind]
        if jobs and jobs[0]["status"] in {"completed", "failed"}:
            return w, jobs[0]
        time.sleep(0.02)
    pytest.fail("learning job did not complete")


def draft(client, headers):
    kb = create_kb(client, headers, "学习资料")
    doc = upload(client, headers, kb, MATERIAL.encode(), "transactions.txt")
    wait_job(client, headers, doc["job_id"])
    body = dict(
        knowledge_base_id=kb,
        document_ids=[doc["document"]["id"]],
        goal_text="我想理解数据库事务，并分析并发异常",
        request_key=new_id(),
    )
    result = client.post("/api/learning/workspaces", headers=headers, json=body)
    assert result.status_code == 202, result.text
    w, job = wait_learning(client, headers, result.json()["workspace_id"])
    assert job["status"] == "completed", job
    return w, body


def activate(client, headers, w):
    caps = [
        {k: c[k] for k in ["id", "title", "observable_outcome", "prerequisite_ids", "source_ids", "coverage_status"]}
        for c in w["capabilities"]
    ]
    for c in caps:
        assert c["source_ids"]
        c["coverage_status"] = "covered"
    r = client.patch(
        f"/api/learning/workspaces/{w['id']}/plan",
        headers=headers,
        json={"state_version": w["state_version"], "capabilities": caps},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/learning/workspaces/{w['id']}/confirm",
        headers=headers,
        json={"state_version": r.json()["state_version"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def mock_evaluation(monkeypatch, verdict="met", code=None):
    def model(database, settings, user_id, instruction, payload, schema):
        return {
            "criteria": [
                {
                    "criterion_id": c["id"],
                    "verdict": verdict,
                    "student_quotes": [payload["answer"]],
                    "source_ids": [payload["sources"][0]["id"]],
                    "reason": "测试中按给定标准作出判定。",
                }
                for c in payload["rubric"]
            ],
            "obstacle_candidates": [
                {
                    "code": code,
                    "criterion_ids": [payload["rubric"][0]["id"]],
                    "alternative_explanation": "可能是表达遗漏，需要进一步追问。",
                }
            ]
            if code
            else [],
            "feedback": "请继续说明推理依据。",
            "reveals_solution": False,
        }, {"model": "fixture", "model_calls": 1}

    monkeypatch.setattr("app.services.learning.assessment.invoke_json", model)


def submit(client, headers, w, text="测试作答", key=None):
    r = client.post(
        f"/api/learning/tasks/{w['current_task_id']}/attempts",
        headers=headers,
        json={"answer_text": text, "request_key": key or new_id()},
    )
    assert r.status_code == 202, r.text
    w, job = wait_learning(client, headers, w["id"], "assessment")
    return w, job, r.json()


def test_complete_learning_cycle_and_correction(client, monkeypatch):
    h = register(client)
    w, body = draft(client, h)
    assert len(w["capabilities"]) == 3 and w["status"] == "draft"
    assert all(c["coverage_status"] != "covered" for c in w["capabilities"])
    repeated = client.post("/api/learning/workspaces", headers=h, json=body)
    assert repeated.json()["workspace_id"] == w["id"]
    w = activate(client, h, w)
    assert len(w["tasks"]) == 8
    mock_evaluation(monkeypatch)
    w, job, _ = submit(client, h, w)
    assert job["status"] == "completed", job
    assert w["capabilities"][0]["state"] == "independent"
    t = client.get("/api/learning/tasks/" + w["current_task_id"], headers=h).json()
    assert t["template_id"] == "T2" and "hints" not in t
    mock_evaluation(monkeypatch, "not_met", "stock_value_vs_order_count")
    w, _, _ = submit(client, h, w, "库存没负数，所以没有超卖")
    assert w["obstacles"][0]["status"] == "suspected"
    assert client.get("/api/learning/tasks/" + w["current_task_id"], headers=h).json()["template_id"] == "T3"
    w, _, _ = submit(client, h, w, "还是以库存字段是否负数判断")
    assert w["obstacles"][0]["status"] == "confirmed"
    assert client.get("/api/learning/tasks/" + w["current_task_id"], headers=h).json()["template_id"] == "T5"
    hint = client.post(
        "/api/learning/tasks/" + w["current_task_id"] + "/hints", headers=h, json={"level": 2, "request_key": new_id()}
    )
    assert hint.status_code == 200 and hint.json()["sources"][0]["excerpt"]
    mock_evaluation(monkeypatch)
    w, _, attempt = submit(client, h, w, "辅助后完成")
    a = client.get("/api/learning/attempts/" + attempt["id"], headers=h).json()
    assert a["hint_level_at_submit"] == 2
    assert w["capabilities"][1]["state"] != "independent"
    correction = {
        "state_version": w["state_version"],
        "request_key": new_id(),
        "attempt_id": a["id"],
        "reason": "原评价理解有误，请重新验证",
    }
    r = client.post(f"/api/learning/workspaces/{w['id']}/corrections", headers=h, json=correction)
    assert r.status_code == 200, r.text
    assert client.get("/api/learning/attempts/" + a["id"], headers=h).json()["invalidated"]
    assert client.post(f"/api/learning/workspaces/{w['id']}/corrections", headers=h, json=correction).status_code == 200
    history = client.get(f"/api/learning/workspaces/{w['id']}/history", headers=h).json()
    assert any(d["status"] == "superseded" for d in history["decisions"])
    assert any(d["kind"] == "correction" for d in history["decisions"])


def test_no_model_failure_keeps_submission_and_retry(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    key = new_id()
    w, job, attempt = submit(client, h, w, key=key)
    assert job["status"] == "failed"
    assert "未配置" in job["error"]
    assert all(c["state"] == "unverified" for c in w["capabilities"])
    replay = client.post(
        "/api/learning/tasks/" + attempt["task_id"] + "/attempts",
        headers=h,
        json={"answer_text": "测试作答", "request_key": key},
    )
    assert replay.json()["id"] == attempt["id"]
    assert (
        client.post(
            "/api/learning/tasks/" + attempt["task_id"] + "/attempts",
            headers=h,
            json={"answer_text": "不同答案", "request_key": key},
        ).status_code
        == 409
    )
    mock_evaluation(monkeypatch)
    assert client.post("/api/learning/jobs/" + job["id"] + "/retry", headers=h).status_code == 200
    w, job = wait_learning(client, h, w["id"], "assessment")
    assert job["status"] == "completed", job
    history = client.get(f"/api/learning/workspaces/{w['id']}/history", headers=h).json()
    assert len(history["attempts"]) == 1


def test_permissions_sources_delete_and_historical_chunks(client, monkeypatch):
    h, other = register(client), register(client, "other@example.com")
    w, body = draft(client, h)
    w = activate(client, h, w)
    mock_evaluation(monkeypatch)
    w, _, attempt = submit(client, h, w)
    aid = attempt["id"]
    sid = w["sources"][0]["id"]
    for path in [
        f"workspaces/{w['id']}",
        f"workspaces/{w['id']}/history",
        f"tasks/{attempt['task_id']}",
        f"attempts/{aid}",
        f"jobs/{w['jobs'][0]['id']}",
    ]:
        assert client.get("/api/learning/" + path, headers=other).status_code == 404
    with client.app.state.database.session() as db:
        # Simulate publication of a new revision and cleanup of the old chunks.
        db.get(Document, body["document_ids"][0]).active_revision_id = new_id()
        db.execute(delete(Chunk).where(Chunk.document_id == body["document_ids"][0]))
        db.commit()
    assert client.post(
        "/api/learning/tasks/" + w["current_task_id"] + "/hints", headers=h, json={"level": 2, "request_key": new_id()}
    ).json()["sources"][0]["excerpt"]
    assert client.delete("/api/documents/" + body["document_ids"][0], headers=h).status_code == 204
    payload = client.get(f"/api/learning/workspaces/{w['id']}", headers=h).json()
    assert payload["source_warning"] and all(s["excerpt"] == "" for s in payload["sources"])
    assert client.get("/api/learning/attempts/" + aid, headers=h).json()["assessment"] == {}
    assert client.get("/api/learning/attempts/" + aid, headers=h).json()["answer_text"] == "测试作答"
    assert client.get("/api/learning/tasks/" + attempt["task_id"], headers=h).json()["restricted"]
    with client.app.state.database.session() as db:
        assert db.get(LearningSourceSnapshot, sid).excerpt == ""
    assert client.delete("/api/knowledge-bases/" + body["knowledge_base_id"], headers=h).status_code == 204
    assert client.get(f"/api/learning/workspaces/{w['id']}", headers=h).json()["knowledge_base_id"] is None
    assert client.delete(f"/api/learning/workspaces/{w['id']}", headers=h).status_code == 204


def test_scope_document_whitelist_and_empty_scope(client):
    h = register(client)
    kb = create_kb(client, h)
    a = upload(client, h, kb, MATERIAL.encode(), "a.txt")
    wait_job(client, h, a["job_id"])
    b = upload(client, h, kb, "这是不应检索到的另一份事务资料".encode(), "b.txt")
    wait_job(client, h, b["job_id"])
    with client.app.state.database.session() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        scope = CorpusScope(db, user, [kb], [a["document"]["id"]])
        ids = db.scalars(select(Chunk.id)).all()
        assert all(c["document_id"] == a["document"]["id"] for c in scope.hydrate(ids).values())
        assert scope.dense_filter().must[1].match.any == [a["document"]["id"]]
        sparse, _ = scope.sparse("事务", 10)
        assert all(c["document_id"] == a["document"]["id"] for c in scope.hydrate(sparse).values())
        empty = CorpusScope(db, user, [kb], [])
        assert len(empty) == 0 and not empty.hydrate(ids)
        assert scope.fingerprint() != CorpusScope(db, user, [kb]).fingerprint()
    assert (
        client.post(
            "/api/learning/workspaces",
            headers=h,
            json={
                "knowledge_base_id": kb,
                "document_ids": [],
                "goal_text": "我要学习数据库事务的并发控制",
                "request_key": new_id(),
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("issue", ["quote", "source", "missing", "duplicate", "no_quote", "obstacle"])
def test_rejects_unreliable_assessment(issue):
    task = SimpleNamespace(rubric=[{"id": "K1"}], source_ids=["s1"])
    attempt = SimpleNamespace(answer_text="数据库库存与成功订单需要分别分析")
    result = {
        "criteria": [
            {
                "criterion_id": "K1",
                "verdict": "met",
                "student_quotes": ["成功订单"],
                "source_ids": ["s1"],
                "reason": "符合标准",
            }
        ],
        "obstacle_candidates": [],
        "feedback": "反馈",
        "reveals_solution": False,
    }
    if issue == "quote":
        result["criteria"][0]["student_quotes"] = ["不存在的句子"]
    if issue == "source":
        result["criteria"][0]["source_ids"] = ["other"]
    if issue == "missing":
        result["criteria"][0]["criterion_id"] = "K9"
    if issue == "duplicate":
        result["criteria"] *= 2
    if issue == "no_quote":
        result["criteria"][0]["student_quotes"] = []
    if issue == "obstacle":
        result["obstacle_candidates"] = [
            {"code": "interleaving", "criterion_ids": ["K1"], "alternative_explanation": "遗漏"}
        ]
    with pytest.raises(ValueError):
        validate_assessment(result, task, attempt)


def test_plan_cycle_conflict_source_gap_and_expiration(client):
    h = register(client)
    w, body = draft(client, h)
    caps = [
        {k: c[k] for k in ["id", "title", "observable_outcome", "prerequisite_ids", "source_ids", "coverage_status"]}
        for c in w["capabilities"]
    ]
    caps[0]["prerequisite_ids"] = [caps[1]["id"]]
    assert (
        client.patch(
            f"/api/learning/workspaces/{w['id']}/plan",
            headers=h,
            json={"state_version": w["state_version"], "capabilities": caps},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/learning/workspaces/{w['id']}/confirm", headers=h, json={"state_version": w["state_version"]}
        ).status_code
        == 422
    )
    w = activate(client, h, w)
    assert (
        client.post(
            f"/api/learning/workspaces/{w['id']}/next",
            headers=h,
            json={"state_version": 0, "request_key": new_id(), "skip": True},
        ).status_code
        == 409
    )
    with client.app.state.database.session() as db:
        db.execute(
            update(Document)
            .where(Document.id == body["document_ids"][0])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert (
        client.post(
            "/api/learning/tasks/" + w["current_task_id"] + "/attempts",
            headers=h,
            json={"answer_text": "解释", "request_key": new_id()},
        ).status_code
        == 409
    )
    assert client.get(f"/api/learning/workspaces/{w['id']}", headers=h).json()["source_warning"]


def test_hint_exposure_persists_for_resubmission(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    tid = w["current_task_id"]
    key = new_id()
    for _ in range(2):
        r = client.post("/api/learning/tasks/" + tid + "/hints", headers=h, json={"level": 4, "request_key": key})
        assert r.status_code == 200
    mock_evaluation(monkeypatch)
    w, _, a = submit(client, h, w)
    assert a["hint_level_at_submit"] == 4
    assert w["capabilities"][0]["state"] == "assisted"
    r = client.post(
        "/api/learning/tasks/" + tid + "/attempts", headers=h, json={"answer_text": "再回答", "request_key": new_id()}
    )
    assert r.json()["hint_level_at_submit"] == 4


def test_worker_old_lease_does_not_apply_and_recovers(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    # Pause background scans while testing the worker fence directly.
    client.app.state.worker_stop.set()
    client.app.state.worker_thread.join(timeout=3)
    r = client.post(
        "/api/learning/tasks/" + w["current_task_id"] + "/attempts",
        headers=h,
        json={"answer_text": "解释", "request_key": new_id()},
    )
    database, settings = client.app.state.database, client.app.state.settings
    with database.session() as db:
        job = db.scalar(select(LearningJob).where(LearningJob.target_id == r.json()["id"]))
        jid = job.id

    def replace_lease(*args):
        with database.session() as db:
            db.execute(
                update(LearningJob)
                .where(LearningJob.id == jid)
                .values(lease_token="successor", lease_expires_at=utcnow() - timedelta(seconds=1))
            )
            db.commit()
        return {"criteria": [], "obstacle_candidates": [], "feedback": "不应保存", "reveals_solution": False}

    monkeypatch.setattr("app.services.learning.jobs.assess", replace_lease)
    process_learning(database, settings, jid)
    with database.session() as db:
        assert db.get(LearningAttempt, r.json()["id"]).assessment == {}
        assert db.get(LearningJob, jid).lease_token == "successor"
    mock_evaluation(monkeypatch)
    from app.services.learning.assessment import assess

    monkeypatch.setattr("app.services.learning.jobs.assess", assess)
    process_learning(database, settings, jid)
    assert client.get("/api/learning/jobs/" + jid, headers=h).json()["status"] == "completed"


def test_fixed_templates_are_synchronized():
    import json
    from pathlib import Path

    assert templates() == json.loads(
        (Path(__file__).parents[2] / "examples/learning/database-transactions-v1.json").read_text()
    )
    assert {t["id"] for t in templates()["tasks"]} == {f"T{i}" for i in range(1, 9)}


def test_full_independent_path_finishes_without_redundant_followup(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    mock_evaluation(monkeypatch)
    seen = []
    for _ in range(8):
        if w["status"] == "completed":
            break
        seen.append(client.get("/api/learning/tasks/" + w["current_task_id"], headers=h).json()["template_id"])
        w, job, _ = submit(client, h, w)
        assert job["status"] == "completed"
    assert w["status"] == "completed"
    assert "T3" not in seen
    assert all(c["state"] == "transfer_verified" for c in w["capabilities"])


def test_cannot_replay_old_failed_assessment_after_newer_submission(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    w, old_job, _ = submit(client, h, w)
    assert old_job["status"] == "failed"
    mock_evaluation(monkeypatch)
    w, _, _ = submit(client, h, w, "新的答案")
    assert client.post("/api/learning/jobs/" + old_job["id"] + "/retry", headers=h).status_code == 409


def test_source_deleted_during_assessment_cannot_publish(client, monkeypatch):
    h = register(client)
    w, body = draft(client, h)
    w = activate(client, h, w)
    database = client.app.state.database

    def delete_source(*args):
        from app.services.learning.sources import redact_document

        with database.session() as db:
            redact_document(db, body["document_ids"][0])
            db.delete(db.get(Document, body["document_ids"][0]))
            db.commit()
        return {"criteria": [], "feedback": "不应发布的反馈", "obstacle_candidates": [], "reveals_solution": False}

    monkeypatch.setattr("app.services.learning.jobs.assess", delete_source)
    w, job, attempt = submit(client, h, w)
    assert job["status"] == "failed"
    assert client.get("/api/learning/attempts/" + attempt["id"], headers=h).json()["assessment"] == {}
    assert w["capabilities"][0]["state"] == "unverified"


def test_skipping_does_not_mark_ability_passed(client):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    r = client.post(
        f"/api/learning/workspaces/{w['id']}/next",
        headers=h,
        json={"state_version": w["state_version"], "request_key": new_id(), "skip": True},
    )
    assert r.status_code == 200
    assert all(c["state"] == "unverified" for c in r.json()["capabilities"])


def test_model_repairs_and_budgets_are_bounded(client, monkeypatch):
    from app.schemas_learning import AssessmentResult
    from app.services.learning.model import invoke_json
    from langchain_core.messages import AIMessage

    calls = []

    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return AIMessage(content="invalid-json")

    monkeypatch.setattr("app.services.learning.model.llm_ready", lambda: True)
    monkeypatch.setattr("app.services.learning.model.configured_llm", lambda: Model())
    register(client)
    with client.app.state.database.session() as db:
        uid = db.scalar(select(User.id))
    with pytest.raises(ValueError):
        invoke_json(
            client.app.state.database,
            client.app.state.settings,
            uid,
            "评价",
            {"answer": "答案", "sources": [{"id": "s", "text": "来源" * 3000}]},
            AssessmentResult,
        )
    assert len(calls) == 2
    # The input is clipped, while original answer and source id remain intact.
    import json

    payload = json.loads(calls[0][1].content)
    assert payload["answer"] == "答案" and payload["sources"][0]["id"] == "s"
    assert len(payload["sources"][0]["text"]) < 6000
    settings = client.app.state.settings.model_copy(update={"user_daily_model_requests": 2})
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        invoke_json(
            client.app.state.database, settings, uid, "评价", {"answer": "答案", "sources": []}, AssessmentResult
        )
    assert exc.value.status_code == 429
    assert len(calls) == 2


def test_interleaving_obstacle_prioritizes_timeline_instead_of_order_count(client, monkeypatch):
    h = register(client)
    w, _ = draft(client, h)
    w = activate(client, h, w)
    mock_evaluation(monkeypatch)
    w, _, _ = submit(client, h, w)
    mock_evaluation(monkeypatch, 'not_met', 'interleaving')
    w, _, _ = submit(client, h, w)
    t = client.get('/api/learning/tasks/' + w['current_task_id'], headers=h).json()
    assert t['template_id'] == 'T5'
