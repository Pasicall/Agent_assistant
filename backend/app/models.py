import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    # Naive UTC is deliberate: SQLite and PostgreSQL share the same comparisons.
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    disabled: Mapped[bool] = mapped_column(default=False)


class KnowledgeBase(Base):
    """
    一组个人资料及变更代次
    """
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Legacy compatibility only: never set through the personal API; non-null bases stay inaccessible.
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    # Retained for the existing schema, without sharing or role semantics in the application.
    visibility: Mapped[str] = mapped_column(String(20), default="organization")


class Document(Base):
    """
    文档可用状态
    """
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "sha256", name="uq_document_kb_sha"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(Text, default="upload")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    revision_id: Mapped[str | None] = mapped_column(String(36), index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revision_id: Mapped[str | None] = mapped_column(String(36))
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(120))
    processed_pages: Mapped[int] = mapped_column(Integer, default=0)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="completed")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_base_ids: Mapped[list] = mapped_column(JSON, default=list)
    cases: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    use_ragas: Mapped[bool] = mapped_column(default=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))


class Organization(Base):
    """Legacy FK target only; no organization business or queries. Historical columns stay in SQL."""
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(default=False)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChunkTerm(Base):
    __tablename__ = "chunk_terms"
    term: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    frequency: Mapped[int] = mapped_column(Integer)


class CleanupTask(Base):
    __tablename__ = "cleanup_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_id: Mapped[str | None] = mapped_column(String(36))
    paths: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    retained_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class AdmissionSlot(Base):
    __tablename__ = "admission_slots"
    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ChatRequestRun(Base):
    __tablename__ = "chat_request_runs"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_chat_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


Index("ix_job_recovery", IngestionJob.status, IngestionJob.lease_expires_at)


class LearningWorkspace(Base):
    __tablename__ = "learning_workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="SET NULL"))
    goal_text: Mapped[str] = mapped_column(Text)
    selected_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="generating")
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
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
    coverage_details: Mapped[dict | None] = mapped_column(JSON)
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
    generation_details: Mapped[dict | None] = mapped_column(JSON)
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
    input_payload: Mapped[dict | None] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
