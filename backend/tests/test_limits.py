import json
from datetime import timedelta

from app.models import utcnow
from sqlalchemy import update
from test_api import create_kb, register, upload, wait_job


def test_storage_and_pending_task_quotas(client, monkeypatch):
    owner = register(client)
    kb = create_kb(client, owner)
    result = upload(client, owner, kb)
    wait_job(client, owner, result['job_id'])
    monkeypatch.setattr(client.app.state.settings, 'user_storage_mb', 1)
    from app.models import DocumentRevision
    with client.app.state.database.session() as db:
        db.execute(update(DocumentRevision).values(size_bytes=1024 * 1024))
        db.commit()
    response = client.post('/api/documents/upload', headers=owner, data={'knowledge_base_id': kb},
                           files={'file': ('second.txt', b'new content')})
    assert response.status_code == 429
    assert len(client.get('/api/documents', headers=owner).json()) == 1



def test_static_limits_apply_to_upload_reindex_restore_chat_and_evaluation(client, monkeypatch):

    from app.models import DocumentRevision, IngestionJob
    from sqlalchemy import text, update
    from test_api import wait_evaluation, wait_job

    owner = register(client)
    kb = create_kb(client, owner)
    first = upload(client, owner, kb)
    assert wait_job(client, owner, first['job_id'])['status'] == 'completed'
    actor = client.get('/api/auth/me', headers=owner).json()['id']
    doc = first['document']['id']
    revision = client.get(f'/api/documents/{doc}/versions', headers=owner).json()[0]['id']
    # Legacy overrides remain stored but cannot override static application settings.
    with client.app.state.database.session() as db:
        for scope in ['global', 'user:' + actor]:
            db.execute(text('INSERT INTO operational_policies (scope, limits, updated_by, updated_at) '
                'VALUES (:scope, :limits, :actor, :now)'), {'scope': scope,
                'limits': json.dumps({'max_pending_jobs': 999, 'user_storage_mb': 99999, 'user_daily_model_requests': 99999}),
                'actor': actor, 'now': utcnow().isoformat()})
        pending = IngestionJob(document_id=doc, available_at=utcnow() + timedelta(hours=1))
        db.add(pending)
        db.commit()
        pending_id = pending.id
    monkeypatch.setattr(client.app.state.settings, 'max_pending_jobs', 1)
    assert client.post('/api/documents/upload', headers=owner, data={'knowledge_base_id': kb},
        files={'file': ('extra.txt', b'extra content')}).status_code == 429
    assert client.post(f'/api/documents/{doc}/retry', headers=owner).status_code == 429
    assert client.post(f'/api/documents/{doc}/versions/{revision}/restore', headers=owner).status_code == 429
    with client.app.state.database.session() as db:
        db.delete(db.get(IngestionJob, pending_id))
        db.execute(update(DocumentRevision).where(DocumentRevision.id == revision).values(size_bytes=1024 * 1024))
        db.commit()
    monkeypatch.setattr(client.app.state.settings, 'user_storage_mb', 1)
    assert client.post('/api/documents/upload', headers=owner, data={'knowledge_base_id': kb},
        files={'file': ('extra.txt', b'extra content')}).status_code == 429
    assert len(client.get('/api/documents', headers=owner).json()) == 1
    monkeypatch.setattr(client.app.state.settings, 'user_daily_model_requests', 2)
    assert client.post('/api/chat', headers=owner, json={'question': '年假有几天'}).status_code == 200
    evaluation = client.post('/api/evaluations', headers=owner,
        json={'name': '静态预算', 'cases': [{'question': '年假有几天'}]})
    assert evaluation.status_code == 202, evaluation.text
    assert wait_evaluation(client, owner, evaluation.json()['id'])['status'] == 'completed'
    assert client.post('/api/chat', headers=owner, json={'question': '年假有几天'}).status_code == 429
    assert client.post('/api/evaluations', headers=owner,
        json={'name': '预算用尽', 'cases': [{'question': '年假有几天'}]}).status_code == 429
    assert client.post('/api/auth/logout', headers=owner).status_code == 204
    with client.app.state.database.session() as db:
        assert db.execute(text('SELECT COUNT(*) FROM audit_events')).scalar_one() == 0
        assert db.execute(text('SELECT COUNT(*) FROM operational_policies')).scalar_one() == 2
