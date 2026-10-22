
from app.models import AuthSession
from sqlalchemy import select
from test_api import register


def test_session_revocation_and_cookie_csrf(client):
    headers = register(client)
    token = headers['Authorization'].split()[1]
    assert client.cookies.get('rag_session') == token
    assert client.post('/api/knowledge-bases', json={'name': 'csrf'}).status_code == 403
    assert client.post('/api/knowledge-bases', headers={'X-RAG-Request': '1', 'Origin': 'https://evil.test'},
                       json={'name': 'csrf'}).status_code == 403
    assert client.post('/api/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    with client.app.state.database.session() as db:
        assert db.scalar(select(AuthSession)).revoked



def test_login_rate_limits_before_password_work(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'auth_rate_per_minute', 2)
    body = {'email': 'missing@example.com', 'password': 'incorrect password'}
    assert client.post('/api/auth/login', json=body).status_code == 401
    assert client.post('/api/auth/login', json=body).status_code == 401
    response = client.post('/api/auth/login', json=body)
    assert response.status_code == 429 and response.headers['Retry-After']
