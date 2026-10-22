
from test_api import register


def test_readiness_and_protected_metrics(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'metrics_token', 'test-metrics-secret')
    assert client.get('/api/metrics').status_code == 401
    result = client.get('/api/metrics', headers={'Authorization': 'Bearer test-metrics-secret'})
    assert result.status_code == 200 and 'rag_http_requests_total' in result.text
    response = client.get('/api/ready')
    assert response.status_code in {200, 503} and response.json()['checks']['database']



def test_diagnostics_preserve_authentication_and_independent_metrics_credential(client, monkeypatch):
    from app import health
    assert client.get('/api/ready/models').status_code == 401
    assert client.get('/api/metrics').status_code == 404
    owner = register(client)
    monkeypatch.setattr(health, 'model_checks', lambda _: {'embedding': 'configured'})
    assert client.get('/api/ready/models', headers=owner).json()['checks'] == {'embedding': 'configured'}
    monkeypatch.setattr(client.app.state.settings, 'metrics_token', 'independent-metrics-token')
    assert client.get('/api/metrics', headers=owner).status_code == 401
    assert client.get('/api/metrics', headers={'Authorization': 'Bearer independent-metrics-token'}).status_code == 200
    assert client.delete('/api/auth/sessions', headers=owner).status_code == 204
    assert client.get('/api/auth/me', headers=owner).status_code == 401
