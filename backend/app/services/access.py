"""One ownership policy for personal knowledge bases and all their resources."""
from fastapi import HTTPException
from sqlalchemy import select

from app.models import KnowledgeBase


def allowed_kbs(user_id):
    # Legacy organization data stays in storage until ownership is explicitly migrated.
    return (KnowledgeBase.user_id == user_id) & KnowledgeBase.organization_id.is_(None)


def require_kb(db, user, kb_id):
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id, allowed_kbs(user.id)))
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    return kb
