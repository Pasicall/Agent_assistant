"""Personal ownership is shared by document, retrieval, history and evaluation access."""
import pytest
from app.models import Chunk, Document, KnowledgeBase
from sqlalchemy import select, text
from test_api import create_kb, register, upload, wait_evaluation, wait_job


@pytest.mark.parametrize('field,value', [('organization_id', 'legacy-org'), ('organization_id', None),
    ('visibility', 'organization'), ('visibility', 'restricted'), ('role', 'admin')])
def test_knowledge_base_rejects_removed_fields(client, field, value):
    owner = register(client)
    response = client.post('/api/knowledge-bases', headers=owner, json={'name': '个人资料', field: value})
    assert response.status_code == 422
    assert any(error['loc'] == ['body', field] and error['type'] == 'extra_forbidden'
        for error in response.json()['detail'])
    assert client.get('/api/knowledge-bases', headers=owner).json() == []


def test_personal_knowledge_base_contract_and_lifecycle_isolation(client, tmp_path):
    owner, other = register(client), register(client, 'other@example.com')
    response = client.post('/api/knowledge-bases', headers=owner,
        json={'name': '个人资料', 'description': '数据库学习笔记'})
    assert response.status_code == 201
    assert set(response.json()) == {'id', 'name', 'description', 'created_at', 'document_count'}
    kb = response.json()['id']
    assert set(client.get('/api/knowledge-bases', headers=owner).json()[0]) == set(response.json())
    first = upload(client, owner, kb)
    second = upload(client, owner, kb, '事务的特性是原子性。'.encode(), 'transactions.txt')
    for result in [first, second]:
        assert wait_job(client, owner, result['job_id'])['status'] == 'completed'
    doc = first['document']['id']
    other_doc = second['document']['id']
    revision = client.get(f'/api/documents/{doc}/versions', headers=owner).json()[0]['id']
    other_revision = client.get(f'/api/documents/{other_doc}/versions', headers=owner).json()[0]['id']
    for path in [f'/api/documents/{doc}/versions', f'/api/documents/{doc}/versions/{revision}/download']:
        assert client.get(path, headers=other).status_code == 404
    assert client.post(f'/api/documents/{doc}/versions/{revision}/restore', headers=other).status_code == 404
    assert client.patch(f'/api/documents/{doc}/metadata', headers=other, json={'source': 'foreign'}).status_code == 404
    assert client.post('/api/sources/sync', headers=other,
        json={'knowledge_base_id': kb, 'relative_path': '.'}).status_code == 404
    assert client.get(f'/api/documents/{doc}/versions/{other_revision}/download', headers=owner).status_code == 404
    assert client.post(f'/api/documents/{doc}/versions/{other_revision}/restore', headers=owner).status_code == 404
    outside = tmp_path / 'outside.txt'
    outside.write_text('must never be downloaded')
    from app.models import DocumentRevision
    with client.app.state.database.session() as db:
        db.get(DocumentRevision, revision).stored_path = str(outside)
        db.commit()
    assert client.get(f'/api/documents/{doc}/versions/{revision}/download', headers=owner).status_code == 404


def test_legacy_organization_corpus_stays_stored_but_is_inaccessible(client):
    owner, member = register(client), register(client, 'member@example.com')
    kb = create_kb(client, owner)
    uploaded = upload(client, owner, kb, '住宿报销上限500元。'.encode())
    assert wait_job(client, owner, uploaded['job_id'])['status'] == 'completed'
    body = {'question': '住宿报销上限是多少', 'request_id': 'legacy-replay-1'}
    response = client.post('/api/chat', headers=owner, json=body)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert '500' in answer['answer'] and answer['citations']
    case = {'question': body['question'], 'message_id': answer['message_id']}
    response = client.post('/api/evaluations', headers=owner, json={'name': '历史复评', 'cases': [case]})
    assert response.status_code == 202
    run_id = response.json()['id']
    assert wait_evaluation(client, owner, run_id)['status'] == 'completed'
    owner_id = client.get('/api/auth/me', headers=owner).json()['id']
    member_id = client.get('/api/auth/me', headers=member).json()['id']
    # Represent data created by an older release; no removed business endpoint is needed.
    with client.app.state.database.session() as db:
        org_id = 'legacy-organization'
        db.execute(text('INSERT INTO organizations (id, name, created_at) VALUES (:id, :name, :created)'),
            {'id': org_id, 'name': '旧组织资料，待明确归属', 'created': '2026-10-21 00:00:00'})
        db.get(KnowledgeBase, kb).organization_id = org_id
        for user_id in [owner_id, member_id]:
            db.execute(text('INSERT INTO memberships (organization_id, user_id, role) VALUES (:org, :user, :role)'),
                {'org': org_id, 'user': user_id, 'role': 'admin'})
            db.execute(text('INSERT INTO knowledge_base_grants (knowledge_base_id, user_id, role) VALUES (:kb, :user, :role)'),
                {'kb': kb, 'user': user_id, 'role': 'admin'})
        db.commit()
    doc = uploaded['document']['id']
    for actor in [owner, member]:
        assert client.get('/api/knowledge-bases', headers=actor).json() == []
        assert client.get('/api/documents', headers=actor).json() == []
        for path in [f'/api/documents/{doc}/versions', f'/api/documents/{doc}/download',
                     f'/api/documents/{doc}/chunks', f'/api/jobs/{uploaded["job_id"]}']:
            assert client.get(path, headers=actor).status_code == 404
        assert client.delete(f'/api/knowledge-bases/{kb}', headers=actor).status_code == 404
        assert client.delete(f'/api/documents/{doc}', headers=actor).status_code == 404
        assert client.post('/api/chat', headers=actor,
            json={'question': body['question'], 'knowledge_base_ids': [kb]}).status_code == 404
        empty = client.post('/api/chat', headers=actor, json={'question': body['question']})
        assert empty.status_code == 200 and not empty.json()['citations'] and '500' not in empty.json()['answer']
    replay = client.post('/api/chat', headers=owner, json=body)
    assert replay.status_code == 409
    history = client.get('/api/conversations/' + answer['conversation_id'], headers=owner).json()
    assert not history['messages'][1]['citations'] and '500' not in history['messages'][1]['content']
    restricted = client.get('/api/evaluations/' + run_id, headers=owner).json()
    assert restricted['status'] == 'restricted' and restricted['results'] == restricted['cases'] == []
    assert client.get('/api/evaluations', headers=owner).json()[0]['status'] == 'restricted'
    assert client.post('/api/evaluations', headers=owner, json={'name': '不可复评', 'cases': [case]}).status_code == 403
    expected = [{'question': body['question'], 'expected_document_ids': [doc]}]
    assert client.post('/api/evaluations/compare', headers=owner, json={'cases': expected}).status_code == 422
    with client.app.state.database.session() as db:
        assert db.get(KnowledgeBase, kb).organization_id is not None
        assert db.get(Document, doc) is not None
        assert db.scalar(select(Chunk).where(Chunk.document_id == doc)) is not None


def test_deleted_document_cannot_be_reused_by_history_cache_or_evaluations(client, monkeypatch):
    from app.models import EvaluationRun
    from app.services import answering
    from app.worker import process_evaluation

    owner = register(client)
    kb = create_kb(client, owner)
    uploaded = upload(client, owner, kb, '住宿报销上限500元。'.encode())
    assert wait_job(client, owner, uploaded['job_id'])['status'] == 'completed'
    body = {'question': '住宿报销上限是多少', 'knowledge_base_ids': [kb], 'request_id': 'delete-evidence-1'}
    answer = client.post('/api/chat', headers=owner, json=body).json()
    assert '500' in answer['answer']
    case = {'question': body['question'], 'message_id': answer['message_id']}
    completed = client.post('/api/evaluations', headers=owner, json={'name': '旧答案', 'cases': [case]}).json()
    assert wait_evaluation(client, owner, completed['id'])['status'] == 'completed'
    client.app.state.worker_stop.set()
    client.app.state.worker_thread.join(timeout=10)
    queued = client.post('/api/evaluations', headers=owner, json={'name': '待执行旧答案', 'cases': [case]}).json()
    assert client.delete('/api/documents/' + uploaded['document']['id'], headers=owner).status_code == 204
    assert client.post('/api/chat', headers=owner, json=body).status_code == 409
    history = client.get('/api/conversations/' + answer['conversation_id'], headers=owner).json()
    assert not history['messages'][1]['citations'] and '500' not in history['messages'][1]['content']
    assert client.post('/api/evaluations', headers=owner, json={'name': '不允许复用', 'cases': [case]}).status_code == 403
    for run_id in [completed['id'], queued['id']]:
        result = client.get('/api/evaluations/' + run_id, headers=owner).json()
        assert result['status'] == 'restricted' and not result['cases'] and not result['results']
    process_evaluation(client.app.state.database, client.app.state.settings, queued['id'])
    with client.app.state.database.session() as db:
        assert db.get(EvaluationRun, queued['id']).status == 'failed'
    captured = []
    real_answer = answering.answer_question
    def capture(*args, **kwargs):
        captured.extend(kwargs['history'])
        return real_answer(*args, **kwargs)
    monkeypatch.setattr(answering, 'answer_question', capture)
    fresh = client.post('/api/chat', headers=owner, json={'question': '那上限是多少',
        'conversation_id': answer['conversation_id'], 'knowledge_base_ids': [kb]})
    assert fresh.status_code == 200 and not fresh.json()['citations']
    assert all('500' not in message['content'] for message in captured)
