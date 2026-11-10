
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
    assert client.get('/api/ready/models').status_code == 404
    assert client.get('/api/metrics').status_code == 404
    owner = register(client)
    monkeypatch.setattr(health, 'model_checks', lambda _: {'embedding': 'configured'})
    monkeypatch.setattr(client.app.state.settings, 'diagnostics_token', 'operator-token')
    assert client.get('/api/ready/models', headers=owner).status_code == 401
    assert client.get('/api/system', headers=owner).status_code == 401
    assert client.get('/api/ready/models', headers={'Authorization': 'Bearer operator-token'}).json()['checks'] == {'embedding': 'configured'}
    monkeypatch.setattr(client.app.state.settings, 'metrics_token', 'independent-metrics-token')
    assert client.get('/api/metrics', headers=owner).status_code == 401
    assert client.get('/api/metrics', headers={'Authorization': 'Bearer independent-metrics-token'}).status_code == 200
    assert client.delete('/api/auth/sessions', headers=owner).status_code == 204
    assert client.get('/api/auth/me', headers=owner).status_code == 401


def test_learner_capabilities_and_disabled_routes(client, monkeypatch, tmp_path):
    assert client.get('/api/capabilities').status_code == 401
    owner = register(client)
    settings = client.app.state.settings
    monkeypatch.setattr(settings, 'evaluation_enabled', False)
    monkeypatch.setattr(settings, 'source_sync_root', tmp_path)
    monkeypatch.setattr(settings, 'learning_enabled', True)
    monkeypatch.setattr(settings, 'llm_model', '')
    assert client.get('/api/capabilities', headers=owner).json() == {
        'max_upload_mb': settings.max_upload_mb,
        'learning_enabled': True,
        'learning_assessment_configured': False,
        'evaluation_enabled': False,
        'folder_sync_available': False,
    }
    for path in ('/api/evaluations', '/api/evaluations/example'):
        assert client.get(path, headers=owner).status_code == 403
    for path in ('/api/evaluations', '/api/evaluations/compare'):
        assert client.post(path, headers=owner, json={'name': '案例', 'cases': [{'question': '问题', 'reference': '答案'}]}).status_code == 403
    assert client.get('/api/learning/workspaces', headers=owner).status_code == 200
