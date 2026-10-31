"""Frozen learning schema for migration 007; do not import runtime models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData()


def new_id():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


# Existing FK targets are placeholders and are never created by this migration.
Table("users", Base.metadata, Column("id", String(36), primary_key=True))
Table("knowledge_bases", Base.metadata, Column("id", String(36), primary_key=True))


class LearningWorkspace(Base):
    __tablename__ = "learning_workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="SET NULL"))
    goal_text: Mapped[str] = mapped_column(Text)
    selected_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="generating")
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    current_task_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningCapability(Base):
    __tablename__ = "learning_capabilities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    observable_outcome: Mapped[str] = mapped_column(Text)
    prerequisite_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    coverage_status: Mapped[str] = mapped_column(String(24), default="partial")
    state: Mapped[str] = mapped_column(String(32), default="unverified")
    state_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    ordinal: Mapped[int] = mapped_column(Integer)


class LearningSourceSnapshot(Base):
    __tablename__ = "learning_source_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(36))
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_id: Mapped[str] = mapped_column(String(36))
    chunk_id: Mapped[str] = mapped_column(String(36))
    context_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    document_name: Mapped[str] = mapped_column(String(255))
    page: Mapped[int | None] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningTask(Base):
    __tablename__ = "learning_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("learning_capabilities.id", ondelete="CASCADE"))
    template_id: Mapped[str] = mapped_column(String(20))
    template_version: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    task_family: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    hints: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    ordinal: Mapped[int] = mapped_column(Integer)


class LearningAttempt(Base):
    __tablename__ = "learning_attempts"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_learning_attempt_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("learning_tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    answer_text: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    hint_level_at_submit: Mapped[int] = mapped_column(Integer, default=0)
    assessment_status: Mapped[str] = mapped_column(String(24), default="queued")
    assessment: Mapped[dict] = mapped_column(JSON, default=dict)
    assessment_version: Mapped[str] = mapped_column(String(64), default="assessment-v1")
    invalidated: Mapped[bool] = mapped_column(default=False)


class LearningHintEvent(Base):
    __tablename__ = "learning_hint_events"
    __table_args__ = (UniqueConstraint("workspace_id", "request_key", name="uq_learning_hint_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("learning_tasks.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer)
    revealed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningObstacle(Base):
    __tablename__ = "learning_obstacles"
    __table_args__ = (UniqueConstraint("workspace_id", "capability_id", "obstacle_code", name="uq_learning_obstacle"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("learning_capabilities.id", ondelete="CASCADE"))
    obstacle_code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="suspected")
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    counter_evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningDecision(Base):
    __tablename__ = "learning_decisions"
    __table_args__ = (UniqueConstraint("workspace_id", "request_key", name="uq_learning_decision_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    trigger_attempt_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(64), default="learning-rules-v1")
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    before_state: Mapped[dict] = mapped_column(JSON, default=dict)
    after_state: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="applied")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningJob(Base):
    __tablename__ = "learning_jobs"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_learning_job_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("learning_workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(120), default="等待执行")
    input_version: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
