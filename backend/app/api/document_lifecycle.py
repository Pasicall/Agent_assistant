"""Document versions, metadata and configured source synchronization."""
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update

from app.api.dependencies import DB, CurrentUser
from app.models import Document, DocumentRevision, IngestionJob, KnowledgeBase
from app.schemas import DocumentMetadataRequest, SourceSyncRequest
from app.services.access import require_kb

router = APIRouter(prefix="/api")


def get_document(db, user, document_id):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    require_kb(db, user, doc.knowledge_base_id)
    return doc


@router.get("/documents/{document_id}/versions")
def versions(document_id: str, db: DB, user: CurrentUser):
    doc = get_document(db, user, document_id)
    return [{"id": rev.id, "name": rev.name, "sha256": rev.sha256, "size_bytes": rev.size_bytes,
        "status": rev.status, "active": rev.id == doc.active_revision_id, "created_at": rev.created_at}
        for rev in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == document_id)
        .order_by(DocumentRevision.created_at.desc()).limit(100))]


@router.get("/documents/{document_id}/versions/{revision_id}/download")
def download_revision(document_id: str, revision_id: str, request: Request, db: DB, user: CurrentUser):
    from pathlib import Path

    from fastapi.responses import FileResponse
    get_document(db, user, document_id)
    rev = db.get(DocumentRevision, revision_id)
    if not rev or rev.document_id != document_id:
        raise HTTPException(404, "版本不存在")
    path = Path(rev.stored_path).resolve()
    if not path.is_relative_to((request.app.state.settings.data_dir / "uploads").resolve()) or not path.is_file():
        raise HTTPException(404, "版本原文件不存在")
    return FileResponse(path, filename=rev.name, media_type="application/octet-stream")


@router.post("/documents/{document_id}/versions/{revision_id}/restore", status_code=202)
def restore_version(document_id: str, revision_id: str, request: Request, db: DB, user: CurrentUser):
    doc = get_document(db, user, document_id)
    from app.services.lifecycle import reserve_ingestion
    reserve_ingestion(db, user, doc.knowledge_base_id, request.app.state.settings)
    if doc.status not in {"ready", "failed"}:
        raise HTTPException(409, "已有更新任务正在处理")
    rev = db.get(DocumentRevision, revision_id)
    if not rev or rev.document_id != document_id:
        raise HTTPException(404, "版本不存在")
    copy = DocumentRevision(document_id=document_id, name=rev.name, stored_path=rev.stored_path,
        sha256=rev.sha256, size_bytes=rev.size_bytes)
    db.add(copy)
    db.flush()
    job = IngestionJob(document_id=document_id, revision_id=copy.id)
    db.add(job)
    doc.status, doc.error = "queued", None
    db.commit()
    return {"job_id": job.id, "revision_id": copy.id}


@router.patch("/documents/{document_id}/metadata")
def update_metadata(document_id: str, body: DocumentMetadataRequest, db: DB, user: CurrentUser):
    doc = get_document(db, user, document_id)
    try:
        value = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00")) if body.expires_at else None
        if value and value.tzinfo is None:
            raise ValueError("timezone required")
        doc.expires_at = value.astimezone(UTC).replace(tzinfo=None) if value else None
    except ValueError:
        raise HTTPException(422, "expires_at 必须为包含时区的 ISO 时间")
    doc.source = body.source
    db.execute(update(KnowledgeBase).where(KnowledgeBase.id == doc.knowledge_base_id)
        .values(generation=KnowledgeBase.generation + 1))
    db.commit()
    return {"id": doc.id, "expires_at": doc.expires_at, "source": doc.source}


@router.post("/sources/sync")
async def sync_source(body: SourceSyncRequest, request: Request, db: DB, user: CurrentUser):
    from io import BytesIO

    from fastapi import UploadFile

    from app.api.routes import ALLOWED_EXTENSIONS, upload_document
    from app.services.admission import consume
    require_kb(db, user, body.knowledge_base_id)
    settings = request.app.state.settings
    consume(request.app.state.database, f"sync:{user.id}", 5, 60)
    if not settings.source_sync_root:
        raise HTTPException(503, "尚未配置文档源目录")
    root = settings.source_sync_root.resolve()
    target = (root / body.relative_path).resolve()
    if not target.is_relative_to(root) or not target.exists():
        raise HTTPException(422, "路径必须位于配置的文档源目录内")
    files = []
    for scanned, path in enumerate([target] if target.is_file() else target.rglob("*"), 1):
        if scanned > 2000:
            raise HTTPException(422, "文档源目录过大，请选择更小的子目录")
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            if not path.resolve().is_relative_to(root):
                raise HTTPException(422, "文档源包含越界符号链接")
            files.append(path)
            if len(files) > 100:
                raise HTTPException(422, "单次同步最多100个文件，请按子目录分批同步")
    rows = []
    for path in sorted(files):
        import hashlib
        source = "folder:" + path.relative_to(root).as_posix()
        if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "文档源文件超过大小限制")
        import asyncio
        data = await asyncio.to_thread(path.read_bytes)
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "文档源文件超过大小限制")
        existing = db.scalar(select(Document).where(Document.knowledge_base_id == body.knowledge_base_id,
            Document.source == source))
        if existing and existing.sha256 == hashlib.sha256(data).hexdigest():
            rows.append({"document_id": existing.id, "unchanged": True})
            continue
        result = await upload_document(request, db, user, UploadFile(BytesIO(data), filename=path.name),
            body.knowledge_base_id, existing.id if existing else None)
        doc = db.get(Document, result["document"]["id"])
        if doc.source != source:
            db.execute(update(KnowledgeBase).where(KnowledgeBase.id == doc.knowledge_base_id)
                .values(generation=KnowledgeBase.generation + 1))
        doc.source = source
        db.commit()
        rows.append({"document_id": doc.id, "job_id": result["job_id"], "unchanged": False})
    return {"files": rows, "note": "增量导入与替换；源文件移除时保留知识库版本，需显式过期或删除"}
