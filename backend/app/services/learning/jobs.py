import logging
import threading
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select, update

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
from app.services.learning import planning
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
    with database.session() as db:
        changed = db.execute(
            update(LearningJob)
            .where(LearningJob.id == job_id, ready_condition())
            .values(
                status="running",
                stage="检索资料 / 分析作答",
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
            workspace = db.get(LearningWorkspace, job.workspace_id)
            user = db.get(User, job.user_id)
            if not workspace or not user or user.disabled:
                raise ValueError("专题不存在或账户不可用")
            if workspace.state_version != job.input_version:
                raise ValueError("学习状态已变化，请刷新后重试")
            if job.kind == "plan":
                fingerprint, proposals, candidates = planning.prepare(db, user, workspace)
                goal = workspace.goal_text
                db.expunge_all()
            else:
                attempt = db.get(LearningAttempt, job.target_id)
                task = db.get(LearningTask, attempt.task_id)
                sources = check_sources(db, workspace, task.source_ids)
                db.expunge_all()
        if job.kind == "plan":
            result = planning.generate(database, settings, user.id, goal, proposals, candidates)
        else:
            result = assess(database, settings, user.id, task, attempt, sources)
        with database.session() as db:
            # Fence stale workers and serialize against API edits and source deletion.
            owned = db.execute(
                update(LearningJob)
                .where(LearningJob.id == job_id, LearningJob.lease_token == token, LearningJob.status == "running")
                .values(stage="保存验证结果")
            )
            if not owned.rowcount:
                return
            job = db.get(LearningJob, job_id)
            updated = db.execute(
                update(LearningWorkspace)
                .where(LearningWorkspace.id == job.workspace_id, LearningWorkspace.state_version == job.input_version)
                .values(updated_at=utcnow())
            )
            if not updated.rowcount:
                raise ValueError("学习状态已变化，结果未应用")
            workspace, user = db.get(LearningWorkspace, job.workspace_id), db.get(User, job.user_id)
            if not user or user.disabled:
                raise ValueError("账户不可用")
            if job.kind == "plan":
                if (
                    CorpusScope(db, user, [workspace.knowledge_base_id], workspace.selected_document_ids).fingerprint()
                    != fingerprint
                ):
                    raise ValueError("资料在生成过程中发生变化，请重试")
                planning.persist(db, workspace, result, candidates)
                workspace.status, workspace.state_version = "draft", workspace.state_version + 1
            else:
                attempt = db.get(LearningAttempt, job.target_id)
                task = db.get(LearningTask, attempt.task_id)
                check_sources(db, workspace, task.source_ids)
                attempt.assessment, attempt.assessment_status = result, "completed"
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
    except Exception as exc:
        logger.warning("Learning job %s failed: %s", job_id, type(exc).__name__)
        with database.session() as db:
            job = db.scalar(select(LearningJob).where(LearningJob.id == job_id, LearningJob.lease_token == token))
            if job:
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
                if job.kind == "assessment":
                    attempt = db.get(LearningAttempt, job.target_id)
                    if attempt:
                        attempt.assessment_status = "queued" if retry else "failed"
                else:
                    workspace = db.get(LearningWorkspace, job.workspace_id)
                    if workspace:
                        workspace.status = "generating" if retry else "failed"
                db.commit()
    finally:
        done.set()
        thread.join(timeout=1)
