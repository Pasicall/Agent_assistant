import json

from test_api import create_kb, register, upload, wait_job


def test_true_provider_stream_and_verified_final_answer(client, monkeypatch):
    from app.config import get_settings
    from app.services import answering
    from langchain_core.messages import AIMessageChunk
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    monkeypatch.setattr(get_settings(), 'llm_model', 'stub-stream')
    monkeypatch.setattr(get_settings(), 'llm_provider', 'ollama')
    class Provider:
        def stream(self, _):
            yield AIMessageChunk(content='住宿报销上限')
            yield AIMessageChunk(content='5000元 [1]')
        def invoke(self, _):
            # The initial generation streams; one bounded verification repair may invoke.
            from langchain_core.messages import AIMessage
            return AIMessage(content='住宿报销上限5000元 [1]')
    monkeypatch.setattr(answering, 'configured_llm', lambda: Provider())
    response = client.post('/api/chat/stream', headers=headers,
        json={'question': '住宿报销上限是多少', 'knowledge_base_ids': [kb], 'request_id': 'stream-test-1'})
    assert response.status_code == 200
    assert response.text.count('event: draft') == 2
    assert response.text.index('event: draft') < response.text.index('event: done')
    final = json.loads(response.text.split('event: done\ndata: ')[1].split('\n\n')[0])
    assert '5000' not in final['answer'] and '500' in final['answer']
    assert not final['verification']['passed']



def test_cancelled_generation_releases_lease_and_saves_no_partial_answer(client, monkeypatch):
    from types import SimpleNamespace

    import pytest
    from app.models import AuthSession, ChatRequestRun, Conversation, Message, User
    from app.schemas import ChatRequest
    from app.services import answering
    from app.services.chat import run_chat
    from fastapi import HTTPException
    from sqlalchemy import select

    headers = register(client)
    user_id = client.get('/api/auth/me', headers=headers).json()['id']
    monkeypatch.setattr(answering, 'answer_question', lambda *a, **k: {'answer': 'partial draft', 'citations': []})
    with client.app.state.database.session() as db:
        request = SimpleNamespace(app=client.app, state=SimpleNamespace(session_id=db.scalar(select(AuthSession.id))))
        with pytest.raises(HTTPException) as error:
            run_chat(ChatRequest(question='取消生成', request_id='cancel-generation-1'), db,
                db.get(User, user_id), request, cancelled=lambda: True)
        assert error.value.status_code == 499
        assert db.scalar(select(Message)) is None
        assert db.scalar(select(ChatRequestRun)).status == 'failed'
        assert db.scalar(select(Conversation)).lease_token is None


def test_login_revoked_during_generation_cannot_return_or_persist_answer(client, monkeypatch):
    from app.models import AuthSession, ChatRequestRun, Message
    from app.services import answering
    from sqlalchemy import select, update

    headers = register(client)
    def revoke(*args, **kwargs):
        with client.app.state.database.session() as db:
            db.execute(update(AuthSession).values(revoked=True))
            db.commit()
        return {'answer': 'must not escape after logout', 'citations': []}
    monkeypatch.setattr(answering, 'answer_question', revoke)
    response = client.post('/api/chat', headers=headers, json={'question': '耗时生成', 'request_id': 'revoked-generation-1'})
    assert response.status_code == 401
    assert 'must not escape' not in response.text
    with client.app.state.database.session() as db:
        assert db.scalar(select(Message)) is None
        assert db.scalar(select(ChatRequestRun)).status == 'failed'
