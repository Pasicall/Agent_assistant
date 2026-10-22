import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.models import utcnow
from sqlalchemy import update
from test_api import ask, create_kb, register, upload, wait_job


def test_concurrent_turn_and_global_admission_are_rejected_before_second_generation(client, monkeypatch):
    from app.services import answering
    entered, resume = threading.Event(), threading.Event()
    original = answering.answer_question
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert resume.wait(10)
        return original(*args, **kwargs)
    owner, other = register(client), register(client, 'other@example.com')
    kb = create_kb(client, owner)
    initial = client.post('/api/chat', headers=owner, json={'question': '初始化', 'knowledge_base_ids': [kb]}).json()
    monkeypatch.setattr(answering, 'answer_question', blocked)
    monkeypatch.setattr(client.app.state.settings, 'global_active_chats', 1)
    body = {'question': '第一条问题', 'conversation_id': initial['conversation_id'], 'request_id': 'concurrent-turn-1'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat', headers=owner, json=body)
        try:
            assert entered.wait(5)
            busy = client.post('/api/chat', headers=owner, json=body)
            assert busy.status_code == 409
            global_busy = client.post('/api/chat', headers=other, json={'question': '另一账户问题'})
            assert global_busy.status_code == 429
        finally:
            resume.set()
        assert future.result(timeout=10).status_code == 200
    assert len(calls) == 1
    history = client.get('/api/conversations/' + initial['conversation_id'], headers=owner).json()['messages']
    assert [message['role'] for message in history] == ['user', 'assistant', 'user', 'assistant']



def test_idempotency_and_conversation_busy_guard(client):
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    first = ask(client, headers, kb, request_id='idempotent-1')
    second = ask(client, headers, kb, request_id='idempotent-1')
    assert second['idempotent_replay'] and first['message_id'] == second['message_id']
    assert client.post('/api/chat', headers=headers, json={'question': 'different', 'request_id': 'idempotent-1'}).status_code == 409
    from app.models import Conversation
    with client.app.state.database.session() as db:
        db.execute(update(Conversation).where(Conversation.id == first['conversation_id']).values(
            lease_token='another-request', lease_expires_at=utcnow() + timedelta(minutes=5)))
        db.commit()
    response = client.post('/api/chat', headers=headers, json={'question': '追问', 'conversation_id': first['conversation_id']})
    assert response.status_code == 409
