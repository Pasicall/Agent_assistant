"""Personal learning workspaces. SQL records are the source of learning state."""

import hashlib
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DB, CurrentUser
from app.models import (
    LearningAttempt,
    LearningCapability,
    LearningDecision,
    LearningHintEvent,
    LearningJob,
    LearningObstacle,
    LearningSourceSnapshot,
    LearningTask,
    LearningWorkspace,
    User,
    utcnow,
)
from app.schemas_learning import (
    AttemptRequest,
    ConfirmRequest,
    CorrectionRequest,
    HintRequest,
    LearningCreate,
    NextRequest,
    PlanEdit,
    PracticeRequest,
)
from app.services.answering import llm_ready
from app.services.corpus import CorpusScope
from app.services.learning import general
from app.services.learning.planning import templates
from app.services.learning.progression import choose_next, recompute
from app.services.learning.sources import check_sources, source_state


def enabled(request: Request):
    if not request.app.state.settings.learning_enabled:
        raise HTTPException(404, "专题学习模块未启用")


router = APIRouter(prefix="/api/learning", tags=["learning"], dependencies=[Depends(enabled)])


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def own(db, user, wid):
    workspace = db.scalar(
        select(LearningWorkspace).where(LearningWorkspace.id == wid, LearningWorkspace.user_id == user.id)
    )
    if workspace is None:
        raise HTTPException(404, "学习专题不存在")
    return workspace


def resource(db, user, model, rid):
    row = db.get(model, rid)
    if row is None:
        raise HTTPException(404, "学习记录不存在")
    return row, own(db, user, row.workspace_id)


def lock(db, workspace, version=None):
    # Even without an expected version this serializes hint exposure versus submissions in SQLite and PostgreSQL.
    stmt = update(LearningWorkspace).where(LearningWorkspace.id == workspace.id)
    if version is not None:
        stmt = stmt.where(LearningWorkspace.state_version == version)
    if not db.execute(stmt.values(updated_at=utcnow())).rowcount:
        raise HTTPException(409, "学习状态已更新，请刷新后重试")
    db.refresh(workspace)


def not_busy(db, workspace):
    if db.scalar(
        select(LearningJob.id).where(
            LearningJob.workspace_id == workspace.id, LearningJob.status.in_(["queued", "running"])
        )
    ):
        raise HTTPException(409, "当前专题仍在生成或评价，请等待完成")


def job_payload(job):
    payload = {
        key: getattr(job, key)
        for key in ["id", "workspace_id", "kind", "target_id", "status", "stage", "error", "result_ref", "created_at"]
    }
    if job.kind == general.JOB_KIND:
        payload["capability_ids"] = (job.input_payload or {}).get("capability_ids", [])
        payload["input_version"] = job.input_version
        payload["failed_capability_id"] = job.result_ref if job.status == "failed" else None
    return payload


def attempt_payload(db, workspace, row):
    task = db.get(LearningTask, row.task_id)
    try:
        check_sources(db, workspace, task.source_ids, for_work=False)
        assessment = row.assessment
    except HTTPException:
        assessment = {}
    return {
        **{
            key: getattr(row, key)
            for key in [
                "id",
                "task_id",
                "answer_text",
                "submitted_at",
                "hint_level_at_submit",
                "assessment_status",
                "invalidated",
            ]
        },
        "assessment": assessment,
    }


def detail(db, workspace, retries=0):
    sources = db.scalars(
        select(LearningSourceSnapshot).where(LearningSourceSnapshot.workspace_id == workspace.id)
    ).all()
    source_states = {s.id: source_state(db, workspace.user_id, s) for s in sources}
    restricted = "unavailable" in source_states.values()
    warning = ""
    if restricted:
        warning = "部分来源已删除，相关内容已隐藏"
    elif "expired" in source_states.values():
        warning = "包含过期来源，请补充资料后新建专题"
    elif workspace.plan_version == general.GENERAL_PLAN and workspace.status in {"draft", "preparing"}:
        user = db.get(User, workspace.user_id)
        try:
            general.scoped_documents(db, user, workspace, require_same=True)
        except HTTPException as exc:
            warning = exc.detail
    caps = db.scalars(
        select(LearningCapability)
        .where(LearningCapability.workspace_id == workspace.id)
        .order_by(LearningCapability.ordinal)
    ).all()
    payload = {
        **{
            key: getattr(workspace, key)
            for key in [
                "id",
                "goal_text",
                "knowledge_base_id",
                "selected_document_ids",
                "status",
                "state_version",
                "plan_version",
                "current_task_id",
                "created_at",
                "updated_at",
            ]
        },
        "source_warning": warning,
        "capabilities": [
            {
                **{
                    key: getattr(c, key)
                    for key in [
                        "id",
                        "title",
                        "observable_outcome",
                        "prerequisite_ids",
                        "source_ids",
                        "coverage_status",
                        "state",
                        "state_evidence_ids",
                        "ordinal",
                    ]
                },
                **({"title": "来源不可用", "observable_outcome": ""} if restricted else {}),
                **({"coverage_details": None if restricted else c.coverage_details}
                   if workspace.plan_version == general.GENERAL_PLAN else {}),
            }
            for c in caps
        ],
        "sources": [
            {
                "id": s.id,
                "document_name": s.document_name if not restricted else "来源不可用",
                "document_id": s.document_id,
                "page": s.page,
                "revision_id": s.revision_id,
                "state": source_states[s.id],
                "excerpt": s.excerpt if workspace.status in {"draft", "preparing"} and not restricted else "",
            }
            for s in sources
        ],
        "tasks": [
            {"id": t.id, "template_id": t.template_id, "capability_id": t.capability_id, "status": t.status}
            for t in db.scalars(
                select(LearningTask).where(LearningTask.workspace_id == workspace.id).order_by(LearningTask.ordinal)
            )
        ],
        "jobs": [
            {**job_payload(j), **({"error": None} if restricted else {})}
            for j in db.scalars(
                select(LearningJob)
                .where(LearningJob.workspace_id == workspace.id)
                .order_by(LearningJob.created_at.desc())
                .limit(10)
            )
        ],
        "obstacles": [
            {
                key: getattr(o, key)
                for key in ["id", "capability_id", "obstacle_code", "status", "evidence_refs", "counter_evidence_refs"]
            }
            for o in db.scalars(select(LearningObstacle).where(LearningObstacle.workspace_id == workspace.id))
        ],
    }
    # SQLite's deferred SELECTs may span a worker commit. Never pair a completed
    # job with the workspace version read before its result was applied.
    db.refresh(workspace)
    if payload["state_version"] != workspace.state_version or payload["status"] != workspace.status:
        if retries >= 2:
            raise HTTPException(409, "学习状态正在更新，请稍后刷新")
        db.expire_all()
        return detail(db, workspace, retries + 1)
    return payload


@router.post("/workspaces", status_code=202)
def create(body: LearningCreate, db: DB, user: CurrentUser, request: Request):
    value_hash = digest(body.model_dump(exclude={"request_key"}))
    existing = db.scalar(
        select(LearningJob).where(LearningJob.user_id == user.id, LearningJob.request_key == body.request_key)
    )
    if existing:
        if existing.body_hash != value_hash or existing.kind != "plan":
            raise HTTPException(409, "同一请求标识不能用于不同内容")
        return {"workspace_id": existing.workspace_id, "job": job_payload(existing)}
    if len(set(body.document_ids)) != len(body.document_ids):
        raise HTTPException(422, "资料列表包含重复文档")
    settings = request.app.state.settings
    use_general = settings.learning_general_enabled
    if not use_general and not any(word in body.goal_text.lower() for word in ["事务", "transaction"]):
        raise HTTPException(422, "这一版仅支持数据库事务专题，请在目标中明确事务学习目标")
    if use_general and len(body.goal_text.strip()) < 10:
        raise HTTPException(422, "请填写至少10字的具体学习目标")
    count = db.scalar(
        select(func.count())
        .select_from(LearningJob)
        .where(LearningJob.user_id == user.id, LearningJob.status.in_(["queued", "running"]))
    )
    if count >= settings.max_pending_jobs:
        raise HTTPException(429, "待处理的学习任务过多，请稍后再试")
    workspace = LearningWorkspace(
        user_id=user.id,
        knowledge_base_id=body.knowledge_base_id,
        selected_document_ids=body.document_ids,
        goal_text=body.goal_text,
        plan_version=general.GENERAL_PLAN if use_general else 1,
    )
    if use_general:
        _, workspace.source_fingerprint = general.scoped_documents(db, user, workspace)
        if not llm_ready():
            raise HTTPException(503, "通用专题需要配置生成模型，当前学习目标与资料可保留后重试")
    else:
        CorpusScope(db, user, [body.knowledge_base_id], body.document_ids)
    db.add(workspace)
    db.flush()
    job = LearningJob(
        workspace_id=workspace.id,
        user_id=user.id,
        request_key=body.request_key,
        body_hash=value_hash,
        kind="plan",
        target_id=workspace.id,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(LearningJob).where(LearningJob.user_id == user.id, LearningJob.request_key == body.request_key))
        if existing and existing.body_hash == value_hash and existing.kind == "plan":
            return {"workspace_id": existing.workspace_id, "job": job_payload(existing)}
        raise HTTPException(409, "相同请求正在登记，请使用原请求标识重试") from None
    return {"workspace_id": workspace.id, "job": job_payload(job)}


@router.get("/workspaces")
def listing(db: DB, user: CurrentUser, offset: int = 0, limit: int = 30):
    return [
        {key: getattr(w, key) for key in ["id", "goal_text", "status", "updated_at", "current_task_id", "plan_version"]}
        for w in db.scalars(
            select(LearningWorkspace)
            .where(LearningWorkspace.user_id == user.id)
            .order_by(LearningWorkspace.updated_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
        )
    ]


@router.get("/workspaces/{wid}")
def workspace_detail(wid: str, db: DB, user: CurrentUser):
    return detail(db, own(db, user, wid))


@router.patch("/workspaces/{wid}/plan")
def edit(wid: str, body: PlanEdit, db: DB, user: CurrentUser):
    workspace = own(db, user, wid)
    lock(db, workspace, body.state_version)
    if workspace.status != "draft":
        raise HTTPException(409, "确认后的能力已冻结；请新建专题草稿修改目标")
    caps = {c.id: c for c in db.scalars(select(LearningCapability).where(LearningCapability.workspace_id == wid))}
    if set(caps) != {c.id for c in body.capabilities} or len(caps) != len(body.capabilities):
        raise HTTPException(422, "必须保留当前草稿的实际能力，且每项只能提交一次")
    use_general = workspace.plan_version == general.GENERAL_PLAN
    if use_general:
        general.scoped_documents(db, user, workspace, require_same=True)
    edges = {c.id: c.prerequisite_ids for c in body.capabilities}
    try:
        general.validate_prerequisites(edges)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    for item in body.capabilities:
        if item.source_ids:
            check_sources(db, workspace, item.source_ids, require_current=use_general)
        if item.coverage_status == "covered" and not item.source_ids:
            raise HTTPException(422, "资料已覆盖的能力必须绑定原文依据")
        if not use_general and item.coverage_status == "pending_review":
            raise HTTPException(422, "旧版事务专题不支持待复核状态")
        cap = caps[item.id]
        fields = item.model_dump(exclude={"id"})
        changed = any(fields[key] != getattr(cap, key) for key in ("title", "observable_outcome", "source_ids", "prerequisite_ids"))
        if use_general and (changed or item.coverage_status != cap.coverage_status):
            if not item.source_ids:
                fields["coverage_status"] = "missing"
            elif changed or item.coverage_status in {"covered", "pending_review"}:
                fields["coverage_status"] = "pending_review"
            cap.coverage_details = {
                "gap": "目标或依据已变更，准备练习前需要复核" if item.source_ids else general.MISSING_EVIDENCE,
                "evidence": [],
            }
        for key, value in fields.items():
            setattr(cap, key, value)
    workspace.state_version += 1
    db.commit()
    return detail(db, workspace)


def confirm_general(db, user, workspace, body, settings):
    if not body.capability_ids or not body.request_key or len(set(body.capability_ids)) != len(body.capability_ids):
        raise HTTPException(422, "请提交已核对能力及请求标识")
    # Acquire the workspace lock before replay lookup so overlapping confirms see the same job.
    lock(db, workspace)
    value_hash = digest([workspace.id, body.state_version, sorted(body.capability_ids)])
    existing = db.scalar(select(LearningJob).where(LearningJob.user_id == user.id, LearningJob.request_key == body.request_key))
    if existing:
        if existing.kind != general.JOB_KIND or existing.body_hash != value_hash:
            raise HTTPException(409, "同一请求标识不能用于不同内容")
        return {**detail(db, workspace), "job": job_payload(existing)}
    if workspace.state_version != body.state_version or workspace.status != "draft":
        raise HTTPException(409, "学习状态已更新，请刷新后重试")
    if not llm_ready():
        raise HTTPException(503, "请配置生成模型后再准备练习，草稿已保留")
    not_busy(db, workspace)
    general.scoped_documents(db, user, workspace, require_same=True)
    general.checked_capabilities(db, workspace, body.capability_ids)
    count = db.scalar(select(func.count()).select_from(LearningJob).where(
        LearningJob.user_id == user.id, LearningJob.status.in_(["queued", "running"])
    ))
    if count >= settings.max_pending_jobs:
        raise HTTPException(429, "待处理的学习任务过多，请稍后再试")
    workspace.status = "preparing"
    workspace.state_version += 1
    job = LearningJob(
        workspace_id=workspace.id, user_id=user.id, request_key=body.request_key,
        body_hash=value_hash, kind=general.JOB_KIND, target_id=workspace.id,
        input_version=workspace.state_version, input_payload={"capability_ids": body.capability_ids},
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "请求标识已被其他操作使用") from None
    return {**detail(db, workspace), "job": job_payload(job)}


@router.post("/workspaces/{wid}/confirm")
def confirm(wid: str, body: ConfirmRequest, db: DB, user: CurrentUser, request: Request, response: Response):
    workspace = own(db, user, wid)
    if workspace.plan_version == general.GENERAL_PLAN:
        response.status_code = 202
        return confirm_general(db, user, workspace, body, request.app.state.settings)
    lock(db, workspace, body.state_version)
    if workspace.status != "draft":
        raise HTTPException(409, "只有草稿可以确认")
    caps = db.scalars(
        select(LearningCapability).where(LearningCapability.workspace_id == wid).order_by(LearningCapability.ordinal)
    ).all()
    covered = {c.id for c in caps if c.coverage_status == "covered"}
    if not covered:
        raise HTTPException(422, "请先核对原文，并将至少一项能力标记为资料已覆盖")
    for cap in caps:
        if cap.id in covered:
            check_sources(db, workspace, cap.source_ids)
            if not set(cap.prerequisite_ids) <= covered:
                raise HTTPException(422, "已覆盖能力的先修目标也需要有资料依据")
    data = templates()
    for index, template in enumerate(data["tasks"]):
        cap = caps[template["capability"]]
        if cap.id not in covered:
            continue
        db.add(
            LearningTask(
                workspace_id=wid,
                capability_id=cap.id,
                template_id=template["id"],
                template_version=data["version"],
                kind=template["kind"],
                task_family=template["task_family"],
                prompt=template["prompt"],
                rubric=template["rubric"],
                source_ids=cap.source_ids,
                hints=template["hints"],
                ordinal=index,
            )
        )
    db.flush()
    choose_next(db, workspace)
    db.commit()
    return detail(db, workspace)


@router.get("/tasks/{tid}")
def task_detail(tid: str, db: DB, user: CurrentUser):
    task, workspace = resource(db, user, LearningTask, tid)
    restricted = False
    try:
        check_sources(db, workspace, task.source_ids, for_work=False)
    except HTTPException:
        restricted = True
    rubric = task.rubric
    if task.template_version == general.TASK_VERSION:
        rubric = [{key: item[key] for key in ("id", "description", "source_ids")} for item in rubric]
    return {
        "id": task.id,
        "workspace_id": workspace.id,
        "template_id": task.template_id,
        "capability_id": task.capability_id,
        "status": task.status,
        "kind": task.kind,
        "prompt": task.prompt if not restricted else "来源不可用，题面已隐藏",
        "rubric": rubric if not restricted else [],
        "source_ids": task.source_ids,
        "restricted": restricted,
        "attempts": [
            attempt_payload(db, workspace, a)
            for a in db.scalars(
                select(LearningAttempt).where(LearningAttempt.task_id == tid).order_by(LearningAttempt.submitted_at)
            )
        ],
        "hint_level": db.scalar(select(func.max(LearningHintEvent.level)).where(LearningHintEvent.task_id == tid)) or 0,
    }


@router.post("/tasks/{tid}/hints")
def hint(tid: str, body: HintRequest, db: DB, user: CurrentUser):
    task, workspace = resource(db, user, LearningTask, tid)
    lock(db, workspace)
    sources = check_sources(db, workspace, task.source_ids)
    if task.id != workspace.current_task_id and task.status != "completed":
        raise HTTPException(409, "请先完成当前学习任务")
    old = db.scalar(
        select(LearningHintEvent).where(
            LearningHintEvent.workspace_id == workspace.id, LearningHintEvent.request_key == body.request_key
        )
    )
    if old and (old.task_id != tid or old.level != body.level):
        raise HTTPException(409, "提示请求标识已用于其他内容")
    if not old:
        db.add(
            LearningHintEvent(workspace_id=workspace.id, task_id=tid, request_key=body.request_key, level=body.level)
        )
    db.commit()
    return {
        "level": body.level,
        "text": task.hints[body.level - 1],
        "sources": [
            {
                "id": s.id,
                "document_name": s.document_name,
                "page": s.page,
                "excerpt": s.excerpt,
                "document_id": s.document_id,
                "revision_id": s.revision_id,
            }
            for s in sources
        ]
        if body.level >= 2
        else [],
    }


@router.post("/tasks/{tid}/attempts", status_code=202)
def submit(tid: str, body: AttemptRequest, db: DB, user: CurrentUser):
    task, workspace = resource(db, user, LearningTask, tid)
    lock(db, workspace)
    value_hash = digest({"task_id": tid, "answer_text": body.answer_text})
    old = db.scalar(
        select(LearningAttempt).where(
            LearningAttempt.user_id == user.id, LearningAttempt.request_key == body.request_key
        )
    )
    if old:
        if old.body_hash != value_hash:
            raise HTTPException(409, "同一提交标识不能用于不同作答")
        return attempt_payload(db, workspace, old)
    not_busy(db, workspace)
    if task.id != workspace.current_task_id and task.status != "completed":
        raise HTTPException(409, "这份任务尚未解锁")
    check_sources(db, workspace, task.source_ids)
    level = db.scalar(select(func.max(LearningHintEvent.level)).where(LearningHintEvent.task_id == tid)) or 0
    attempt = LearningAttempt(
        workspace_id=workspace.id,
        task_id=tid,
        user_id=user.id,
        request_key=body.request_key,
        body_hash=value_hash,
        answer_text=body.answer_text,
        hint_level_at_submit=level,
    )
    db.add(attempt)
    db.flush()
    workspace.current_task_id = task.id
    workspace.status = "active"
    workspace.state_version += 1
    db.add(
        LearningJob(
            workspace_id=workspace.id,
            user_id=user.id,
            request_key=f"assess:{attempt.id}",
            body_hash=value_hash,
            kind="assessment",
            target_id=attempt.id,
            input_version=workspace.state_version,
        )
    )
    db.commit()
    return attempt_payload(db, workspace, attempt)


@router.get("/attempts/{aid}")
def attempt_detail(aid: str, db: DB, user: CurrentUser):
    row, workspace = resource(db, user, LearningAttempt, aid)
    return attempt_payload(db, workspace, row)


@router.get("/jobs/{jid}")
def job_detail(jid: str, db: DB, user: CurrentUser):
    job, workspace = resource(db, user, LearningJob, jid)
    payload = job_payload(job)
    if workspace.plan_version == general.GENERAL_PLAN and job.error:
        if any(source_state(db, user.id, source) == "unavailable" for source in db.scalars(
            select(LearningSourceSnapshot).where(LearningSourceSnapshot.workspace_id == workspace.id)
        )):
            payload["error"] = "来源已删除，相关内容已隐藏"
    return payload


@router.post("/jobs/{jid}/retry")
def retry(jid: str, db: DB, user: CurrentUser):
    job, workspace = resource(db, user, LearningJob, jid)
    lock(db, workspace)
    db.refresh(job)
    if job.status != "failed":
        return job_payload(job)
    not_busy(db, workspace)
    if job.kind == "assessment":
        attempt = db.get(LearningAttempt, job.target_id)
        if attempt.invalidated:
            raise HTTPException(409, "已纠正的记录不能重新应用，请重新作答")
        if db.scalar(
            select(LearningAttempt.id)
            .where(
                LearningAttempt.workspace_id == workspace.id,
                LearningAttempt.submitted_at > attempt.submitted_at,
            )
            .limit(1)
        ):
            raise HTTPException(409, "已有更新的作答，不能再应用旧评价，请基于最新任务重新提交")
        task = db.get(LearningTask, attempt.task_id)
        check_sources(db, workspace, task.source_ids)
        attempt.assessment_status = "queued"
    elif job.kind == general.JOB_KIND:
        replenish = bool((job.input_payload or {}).get("replenish"))
        expected = "needs_practice" if replenish else "draft"
        if workspace.status != expected or workspace.state_version != job.input_version:
            raise HTTPException(409, "专题状态已变化，请补充练习前重新核对当前进度")
        if not llm_ready():
            raise HTTPException(503, "请配置生成模型后再重试练习")
        general.scoped_documents(db, user, workspace, require_same=True)
        general.checked_capabilities(db, workspace, (job.input_payload or {}).get("capability_ids", []))
        workspace.status = "preparing"
    elif job.kind == "plan" and workspace.plan_version == general.GENERAL_PLAN:
        if not llm_ready():
            raise HTTPException(503, "请配置生成模型后再重试草稿")
        _, workspace.source_fingerprint = general.scoped_documents(db, user, workspace)
        workspace.status = "generating"
    elif job.kind == "plan":
        CorpusScope(db, user, [workspace.knowledge_base_id], workspace.selected_document_ids)
        workspace.status = "generating"
    else:
        raise HTTPException(409, "未知学习作业类型")
    job.status, job.error, job.attempts = "queued", None, 0
    job.stage, job.result_ref = "等待执行", None
    job.input_version, job.available_at = workspace.state_version, utcnow() + timedelta(milliseconds=100)
    db.commit()
    return job_payload(job)


@router.get("/workspaces/{wid}/history")
def history(wid: str, db: DB, user: CurrentUser, offset: int = 0):
    workspace = own(db, user, wid)
    attempts = db.scalars(
        select(LearningAttempt)
        .where(LearningAttempt.workspace_id == wid)
        .order_by(LearningAttempt.submitted_at.desc())
        .offset(max(offset, 0))
        .limit(50)
    ).all()
    restricted = any(
        source_state(db, user.id, s) == "unavailable"
        for s in db.scalars(select(LearningSourceSnapshot).where(LearningSourceSnapshot.workspace_id == wid))
    )
    return {
        "attempts": [attempt_payload(db, workspace, a) for a in attempts],
        "decisions": [
            {
                **{
                    key: getattr(d, key)
                    for key in [
                        "id",
                        "kind",
                        "trigger_attempt_id",
                        "rule_version",
                        "evidence_refs",
                        "before_state",
                        "after_state",
                        "status",
                        "created_at",
                    ]
                },
                "reason": "来源已删除，调整解释已隐藏" if restricted else d.reason,
            }
            for d in db.scalars(
                select(LearningDecision)
                .where(LearningDecision.workspace_id == wid)
                .order_by(LearningDecision.created_at.desc())
                .offset(max(offset, 0))
                .limit(50)
            )
        ],
        "hints": [
            {key: getattr(h, key) for key in ["id", "task_id", "level", "revealed_at"]}
            for h in db.scalars(
                select(LearningHintEvent)
                .where(LearningHintEvent.workspace_id == wid)
                .order_by(LearningHintEvent.revealed_at.desc())
                .offset(max(offset, 0))
                .limit(50)
            )
        ],
    }


@router.post("/workspaces/{wid}/corrections")
def correct(wid: str, body: CorrectionRequest, db: DB, user: CurrentUser):
    workspace = own(db, user, wid)
    old = db.scalar(
        select(LearningDecision).where(
            LearningDecision.workspace_id == wid, LearningDecision.request_key == body.request_key
        )
    )
    if old:
        if old.kind != "correction" or old.trigger_attempt_id != body.attempt_id or old.reason != body.reason:
            raise HTTPException(409, "纠正请求标识已被使用")
        return detail(db, workspace)
    lock(db, workspace, body.state_version)
    not_busy(db, workspace)
    attempt, _ = resource(db, user, LearningAttempt, body.attempt_id)
    if attempt.workspace_id != wid or attempt.assessment_status != "completed":
        raise HTTPException(422, "只能纠正本专题已完成的评价")
    if attempt.invalidated:
        raise HTTPException(409, "这次评价已暂停使用，请通过新的作答补充证据")
    attempt.invalidated = True
    db.add(
        LearningDecision(
            workspace_id=wid,
            request_key=body.request_key,
            trigger_attempt_id=attempt.id,
            kind="correction",
            evidence_refs=[attempt.id],
            before_state={"invalidated": False},
            after_state={"invalidated": True},
            reason=body.reason,
        )
    )
    for decision in db.scalars(
        select(LearningDecision).where(
            LearningDecision.workspace_id == wid,
            LearningDecision.trigger_attempt_id == attempt.id,
            LearningDecision.kind == "next",
        )
    ):
        decision.status = "superseded"
    # A new submission can clarify the disputed reasoning; no prior evidence is silently rewritten.
    task = db.get(LearningTask, attempt.task_id)
    task.status, workspace.current_task_id, workspace.status = "pending", task.id, "active"
    recompute(db, workspace)
    workspace.state_version += 1
    db.commit()
    return detail(db, workspace)


@router.post("/workspaces/{wid}/next")
def next_task(wid: str, body: NextRequest, db: DB, user: CurrentUser):
    workspace = own(db, user, wid)
    old = db.scalar(
        select(LearningDecision).where(
            LearningDecision.workspace_id == wid, LearningDecision.request_key == body.request_key
        )
    )
    if old:
        if old.kind != ("skip" if body.skip else "next"):
            raise HTTPException(409, "请求标识已用于其他操作")
        return detail(db, workspace)
    lock(db, workspace, body.state_version)
    not_busy(db, workspace)
    if workspace.status not in {"active", "needs_practice"}:
        raise HTTPException(409, "请先确认学习目标")
    if workspace.current_task_id and not body.skip:
        return detail(db, workspace)
    choose_next(db, workspace, skip=body.skip, request_key=body.request_key)
    db.commit()
    return detail(db, workspace)


@router.post("/workspaces/{wid}/practice", status_code=202)
def replenish_practice(wid: str, body: PracticeRequest, db: DB, user: CurrentUser):
    """Generate a new bounded batch for capabilities that still lack independent evidence."""
    workspace = own(db, user, wid)
    if workspace.plan_version != general.GENERAL_PLAN:
        raise HTTPException(409, "固定题库版本不能再生成练习；请新建专题")
    lock(db, workspace)
    # The command has no payload beyond its workspace, so a retried key is always the same intent.
    value_hash = digest([wid])
    existing = db.scalar(
        select(LearningJob).where(LearningJob.user_id == user.id, LearningJob.request_key == body.request_key)
    )
    if existing:
        if existing.workspace_id != wid or not (existing.input_payload or {}).get("replenish") \
                or existing.body_hash != value_hash:
            raise HTTPException(409, "同一请求标识不能用于其他操作")
        return {**detail(db, workspace), "job": job_payload(existing)}
    if workspace.state_version != body.state_version:
        raise HTTPException(409, "学习状态已更新，请刷新后重试")
    if workspace.current_task_id:
        raise HTTPException(409, "当前还有待完成的题目，请先作答或跳过")
    if workspace.status not in {"active", "needs_practice"}:
        raise HTTPException(409, "当前专题状态不能再补充练习")
    not_busy(db, workspace)
    if not llm_ready():
        raise HTTPException(503, "请配置生成模型后再补充练习")
    batches = sum(
        1 for job in db.scalars(
            select(LearningJob).where(
                LearningJob.workspace_id == wid, LearningJob.kind == general.JOB_KIND
            )
        ) if (job.input_payload or {}).get("replenish")
    )
    if batches >= general.MAX_PRACTICE_BATCHES:
        raise HTTPException(429, "本专题已补充练习多次，请补充资料或新建专题")
    general.scoped_documents(db, user, workspace, require_same=True)
    capability_ids = general.replenish_capabilities(db, workspace)
    workspace.status = "preparing"
    workspace.state_version += 1
    job = LearningJob(
        workspace_id=wid, user_id=user.id, request_key=body.request_key, body_hash=value_hash,
        kind=general.JOB_KIND, target_id=wid, input_version=workspace.state_version,
        input_payload={"capability_ids": capability_ids, "replenish": True},
    )
    db.add(job)
    db.commit()
    return {**detail(db, workspace), "job": job_payload(job)}


@router.delete("/workspaces/{wid}", status_code=204)
def delete(wid: str, db: DB, user: CurrentUser):
    workspace = own(db, user, wid)
    lock(db, workspace)
    db.delete(workspace)
    db.commit()
