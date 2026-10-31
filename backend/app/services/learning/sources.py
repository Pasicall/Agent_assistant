import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from app.models import (
    Document,
    KnowledgeBase,
    LearningAttempt,
    LearningCapability,
    LearningDecision,
    LearningSourceSnapshot,
    LearningTask,
    utcnow,
)
from app.services.access import allowed_kbs


def source_state(db, user_id, source):
    doc = db.scalar(
        select(Document)
        .join(KnowledgeBase)
        .where(Document.id == source.document_id, allowed_kbs(user_id), Document.status != "deleting")
    )
    if not doc or not source.excerpt:
        return "unavailable"
    if doc.expires_at and doc.expires_at <= utcnow():
        return "expired"
    return "historical" if doc.active_revision_id != source.revision_id else "current"


def check_sources(db, workspace, source_ids, for_work=True):
    rows = db.scalars(
        select(LearningSourceSnapshot).where(
            LearningSourceSnapshot.workspace_id == workspace.id, LearningSourceSnapshot.id.in_(source_ids)
        )
    ).all()
    if not rows or len(rows) != len(set(source_ids)):
        raise HTTPException(409, "任务缺少可用的来源依据，请补充资料并新建专题草稿")
    if any(
        source_state(db, workspace.user_id, s) in ({"expired", "unavailable"} if for_work else {"unavailable"})
        for s in rows
    ):
        raise HTTPException(409, "来源已删除、过期或不再可访问，请补充资料并新建专题草稿")
    return rows


def snapshot(db, workspace, candidate):
    text = candidate["text"][:5000]
    row = LearningSourceSnapshot(
        workspace_id=workspace.id,
        knowledge_base_id=candidate["knowledge_base_id"],
        document_id=candidate["document_id"],
        revision_id=candidate["revision_id"],
        chunk_id=candidate["id"],
        context_chunk_ids=candidate.get("context_chunk_ids", [candidate["id"]]),
        document_name=candidate["document_name"],
        page=candidate.get("page"),
        ordinal=candidate["ordinal"],
        excerpt=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def redact_document(db, document_id):
    """Deletion also removes copied material and derived model text, in the same transaction."""
    sources = db.scalars(select(LearningSourceSnapshot).where(LearningSourceSnapshot.document_id == document_id)).all()
    workspace_ids = {s.workspace_id for s in sources}
    for source in sources:
        source.excerpt, source.document_name = "", "来源已删除"
    for wid in workspace_ids:
        # Conservative whole-workspace redaction avoids leaking indirect paraphrases.
        for source in db.scalars(select(LearningSourceSnapshot).where(LearningSourceSnapshot.workspace_id == wid)):
            source.excerpt = ""
        for cap in db.scalars(select(LearningCapability).where(LearningCapability.workspace_id == wid)):
            cap.title, cap.observable_outcome, cap.coverage_status = "来源已删除的能力", "", "missing"
        for task in db.scalars(select(LearningTask).where(LearningTask.workspace_id == wid)):
            task.prompt, task.hints, task.rubric = "", [], []
        for attempt in db.scalars(select(LearningAttempt).where(LearningAttempt.workspace_id == wid)):
            attempt.assessment = {}
        for decision in db.scalars(select(LearningDecision).where(LearningDecision.workspace_id == wid)):
            decision.reason = "来源已删除，派生反馈已清除"
