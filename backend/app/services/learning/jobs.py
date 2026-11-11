import logging
import threading
from datetime import timedelta
from time import perf_counter

from fastapi import HTTPException
from sqlalchemy import case, or_, update

from app.models import (
    LearningAttempt,
    LearningHintEvent,
    LearningJob,
    LearningTask,
    LearningWorkspace,
    User,
    new_id,
    utcnow,
)
from app.services.corpus import CorpusScope
from app.services.learning import general, planning
from app.services.learning.assessment import assess
from app.services.learning.model import ModelUnavailable
from app.services.learning.progression import choose_next, recompute
from app.services.learning.sources import check_sources

logger = logging.getLogger(__name__)


def ready_condition():
    return (LearningJob.available_at <= utcnow()) & or_(
        LearningJob.status == "queued", (LearningJob.status == "running") & (LearningJob.lease_expires_at < utcnow())
    )


def process_learning(database, settings, job_id):
    if not settings.learning_enabled:
        return
    token, ttl = new_id(), settings.model_timeout * 4 + 180
    started = perf_counter()
    kind = "unknown"
    with database.session() as db:
        changed = db.execute(
            update(LearningJob)
            .where(LearningJob.id == job_id, ready_condition())
            .values(
                status="running",
                stage=case(
                    (LearningJob.kind == "plan", "检索资料与核验目标"),
                    (LearningJob.kind == general.JOB_KIND, "复核原文与准备练习"),
                    else_="分析作答",
                ),
                attempts=LearningJob.attempts + 1,
                lease_token=token,
                lease_expires_at=utcnow() + timedelta(seconds=ttl),
            )
        )
        db.commit()
        if not changed.rowcount:
            return
    done = threading.Event()

    def heartbeat():
        while not done.wait(20):
            with database.session() as db:
                db.execute(
                    update(LearningJob)
                    .where(LearningJob.id == job_id, LearningJob.lease_token == token)
                    .values(lease_expires_at=utcnow() + timedelta(seconds=ttl))
                )
                db.commit()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with database.session() as db:
            job = db.get(LearningJob, job_id)
            kind = job.kind
            workspace = db.get(LearningWorkspace, job.workspace_id)
            user = db.get(User, job.user_id)
            if not workspace or not user or user.disabled:
                raise ValueError("专题不存在或账户不可用")
            if workspace.state_version != job.input_version:
                raise ValueError("学习状态已变化，请刷新后重试")
            use_general = workspace.plan_version == general.GENERAL_PLAN
            if job.kind == "plan" and use_general:
                scope, _ = general.scoped_documents(db, user, workspace, require_same=True)
                candidates = general.draft_candidates(scope, workspace.goal_text)
            elif job.kind == "plan":
                fingerprint, proposals, candidates = planning.prepare(db, user, workspace)
            elif job.kind == general.JOB_KIND and use_general:
                general.scoped_documents(db, user, workspace, require_same=True)
                capability_ids = (job.input_payload or {}).get("capability_ids", [])
                selected = general.checked_capabilities(db, workspace, capability_ids)
            elif job.kind == "assessment":
                attempt = db.get(LearningAttempt, job.target_id)
                task = db.get(LearningTask, attempt.task_id)
                sources = check_sources(db, workspace, task.source_ids)
            else:
                raise ValueError("未知学习作业类型")
            db.expunge_all()
        if job.kind == "plan" and use_general:
            result = general.generate_goals(database, settings, user.id, workspace.goal_text, candidates)
        elif job.kind == "plan":
            result = planning.generate(database, settings, user.id, workspace.goal_text, proposals, candidates)
        elif job.kind == general.JOB_KIND:
            result = general.generate_practice(database, settings, user.id, selected)
        else:
            result = assess(database, settings, user.id, task, attempt, sources)
        with database.session() as db:
            # Fence stale workers and serialize against API edits and source deletion.
            if use_general:
                if job.kind in {"plan", general.JOB_KIND}:
                    general.scoped_documents(db, user, workspace, require_same=True, lock=True)
                else:
                    check_sources(db, workspace, task.source_ids, lock=True)
            updated = db.execute(
                update(LearningWorkspace)
                .where(LearningWorkspace.id == workspace.id, LearningWorkspace.state_version == job.input_version)
                .values(updated_at=utcnow())
            )
            if not updated.rowcount:
                raise ValueError("学习状态已变化，结果未应用")
            owned = db.execute(
                update(LearningJob)
                .where(LearningJob.id == job_id, LearningJob.lease_token == token, LearningJob.status == "running")
                .values(stage="保存验证结果")
            )
            if not owned.rowcount:
                return
            job = db.get(LearningJob, job_id)
            workspace, user = db.get(LearningWorkspace, job.workspace_id), db.get(User, job.user_id)
            if not user or user.disabled:
                raise ValueError("账户不可用")
            if job.kind == "plan" and use_general:
                general.persist_goals(db, workspace, result, candidates)
                workspace.status, workspace.state_version = "draft", workspace.state_version + 1
            elif job.kind == "plan":
                if (
                    CorpusScope(db, user, [workspace.knowledge_base_id], workspace.selected_document_ids).fingerprint()
                    != fingerprint
                ):
                    raise ValueError("资料在生成过程中发生变化，请重试")
                planning.persist(db, workspace, result, candidates)
                workspace.status, workspace.state_version = "draft", workspace.state_version + 1
            elif job.kind == general.JOB_KIND:
                general.checked_capabilities(db, workspace, capability_ids)
                general.publish_practice(db, workspace, result)
                choose_next(db, workspace)
            else:
                attempt = db.get(LearningAttempt, job.target_id)
                task = db.get(LearningTask, attempt.task_id)
                check_sources(db, workspace, task.source_ids)
                attempt.assessment, attempt.assessment_status = result, "completed"
                if use_general:
                    attempt.assessment_version = "assessment-v2"
                if result["reveals_solution"]:
                    db.add(
                        LearningHintEvent(
                            workspace_id=workspace.id, task_id=task.id, request_key=f"feedback:{attempt.id}", level=4
                        )
                    )
                task.status = "completed"
                db.flush()
                recompute(db, workspace)
                choose_next(db, workspace, attempt)
            job.status, job.stage, job.result_ref = "completed", "已完成", job.target_id
            job.lease_token = job.lease_expires_at = None
            job.error = None
            db.commit()
            logger.info("Learning job %s completed kind=%s elapsed_ms=%d", job_id, kind, (perf_counter() - started) * 1000)
    except Exception as exc:
        logger.warning("Learning job %s failed kind=%s error_type=%s elapsed_ms=%d",
                       job_id, kind, type(exc).__name__, (perf_counter() - started) * 1000)
        with database.session() as db:
            owned = db.execute(
                update(LearningJob).where(LearningJob.id == job_id, LearningJob.lease_token == token,
                                          LearningJob.status == "running").values(stage="处理作业结果")
            )
            if owned.rowcount:
                job = db.get(LearningJob, job_id)
                waiting = isinstance(exc, HTTPException) and exc.status_code == 429
                transient = not isinstance(exc, (ValueError, HTTPException))
                retry = waiting or (transient and job.attempts < settings.worker_max_attempts)
                job.status = "queued" if retry else "failed"
                job.stage = "等待模型配额" if waiting else "等待重试" if retry else "未完成，可重试"
                job.available_at = utcnow() + timedelta(seconds=60 if waiting else min(60, 2**job.attempts))
                job.error = (
                    (str(exc.detail) if isinstance(exc, HTTPException) else str(exc))[:500]
                    if isinstance(exc, (ValueError, HTTPException, ModelUnavailable))
                    else "模型服务暂不可用，请稍后重试"
                )
                job.lease_token = job.lease_expires_at = None
                if isinstance(exc, general.CapabilityRejected):
                    job.result_ref = exc.capability_id
                if job.kind == "assessment":
                    attempt = db.get(LearningAttempt, job.target_id)
                    if attempt:
                        attempt.assessment_status = "queued" if retry else "failed"
                else:
                    workspace = db.get(LearningWorkspace, job.workspace_id)
                    if workspace and workspace.state_version == job.input_version:
                        if job.kind == general.JOB_KIND:
                            workspace.status = "preparing" if retry else "draft"
                        else:
                            workspace.status = "generating" if retry else "failed"
                db.commit()
    finally:
        done.set()
        thread.join(timeout=1)
