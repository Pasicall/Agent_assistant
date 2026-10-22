import threading

from app.models import CleanupTask, Document, utcnow
from sqlalchemy import select, update
from test_api import create_kb, register, upload, wait_job


def test_durable_delete_during_qdrant_outage(client, monkeypatch):
    from app.services import retrieval
    from app.services.cleanup import process_cleanup
    headers = register(client)
    kb = create_kb(client, headers)
    uploads = [upload(client, headers, kb, f'制度{i}：住宿报销500元。'.encode(), f'{i}.txt') for i in range(2)]
    for item in uploads:
        wait_job(client, headers, item['job_id'])
    # Drive cleanup manually so assertions cannot race an independently claimed lease.
    client.app.state.worker_stop.set()
    client.app.state.worker_thread.join(timeout=10)
    real_delete = retrieval.delete_document
    monkeypatch.setattr(retrieval, 'delete_document', lambda doc: (_ for _ in ()).throw(ConnectionError('outage')))
    assert client.delete('/api/knowledge-bases/' + kb, headers=headers).status_code == 204
    assert client.get('/api/documents', headers=headers).json() == []
    with client.app.state.database.session() as db:
        assert db.scalar(select(Document)) is None
        tasks = db.scalars(select(CleanupTask).where(CleanupTask.revision_id.is_(None))).all()
        assert len(tasks) == 2
        ids = [task.id for task in tasks]
    for task_id in ids:
        process_cleanup(client.app.state.database, client.app.state.settings, task_id)
    with client.app.state.database.session() as db:
        assert all(db.get(CleanupTask, task_id).status == 'queued' for task_id in ids)
        db.execute(update(CleanupTask).where(CleanupTask.id.in_(ids)).values(available_at=utcnow()))
        db.commit()
    monkeypatch.setattr(retrieval, 'delete_document', real_delete)
    for task_id in ids:
        process_cleanup(client.app.state.database, client.app.state.settings, task_id)
    with client.app.state.database.session() as db:
        assert all(db.get(CleanupTask, task_id).status == 'completed' for task_id in ids)



def blocked_parser(monkeypatch):
    from app.services import parsing
    entered, resume = threading.Event(), threading.Event()
    real = parsing.parse_document
    def block(path, filename, **kwargs):
        entered.set()
        assert resume.wait(10)
        return real(path, filename, **kwargs)
    monkeypatch.setattr(parsing,"parse_document",block)
    return entered, resume



def test_delete_while_parser_is_running_returns_before_parse_completes(client, monkeypatch):
    owner=register(client)
    kb=create_kb(client,owner)
    entered,resume=blocked_parser(monkeypatch)
    try:
        result=upload(client,owner,kb)
        assert entered.wait(5)
        assert client.delete("/api/documents/"+result["document"]["id"],headers=owner).status_code==204
        assert client.get("/api/jobs/"+result["job_id"],headers=owner).status_code==404
        assert not client.get("/api/documents",headers=owner).json()
    finally:
        resume.set()
