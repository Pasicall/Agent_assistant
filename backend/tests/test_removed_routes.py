"""Removed enterprise endpoints have no routes, schemas or user identity fields."""
from collections import Counter

import pytest
from test_api import register

REMOVED = [
    ('GET', '/api/organizations'), ('POST', '/api/organizations'),
    ('GET', '/api/organizations/legacy/members'), ('PUT', '/api/organizations/legacy/members'),
    ('DELETE', '/api/organizations/legacy/members/user'),
    ('GET', '/api/knowledge-bases/legacy/members'), ('PUT', '/api/knowledge-bases/legacy/members'),
    ('DELETE', '/api/knowledge-bases/legacy/members/user'),
    ('GET', '/api/audit-events'), ('GET', '/api/operations'),
    ('GET', '/api/platform/users'), ('GET', '/api/platform/quotas'), ('PUT', '/api/platform/quotas'),
    ('DELETE', '/api/platform/quotas'), ('GET', '/api/platform/jobs'),
    ('POST', '/api/platform/jobs/ingestion/legacy/cancel'),
    ('POST', '/api/platform/jobs/evaluation/legacy/retry'),
    ('POST', '/api/platform/jobs/cleanup/legacy/retry'),
    ('GET', '/api/auth/oidc/config'), ('GET', '/api/auth/oidc/start'), ('GET', '/api/auth/oidc/callback'),
]


@pytest.mark.parametrize('method,path', REMOVED)
def test_enterprise_endpoint_is_unreachable(client, method, path):
    assert client.request(method, path).status_code == 404
    owner = register(client)
    assert client.request(method, path, headers=owner).status_code == 404


def test_openapi_and_runtime_only_expose_current_capabilities(client, monkeypatch):
    schema = client.get('/openapi.json').json()
    assert not any(any(term in path for term in ['/organizations', '/platform/', '/auth/oidc/', '/members', '/audit-events', '/operations'])
        for path in schema['paths'])
    assert 'OrganizationRequest' not in schema['components']['schemas']
    assert 'MemberRequest' not in schema['components']['schemas']
    assert set(schema['components']['schemas']['KnowledgeBaseRequest']['properties']) == {'name', 'description'}
    routes = Counter((method, route.path) for route in client.app.routes for method in getattr(route, 'methods', []))
    assert all(count == 1 for count in routes.values())
    owner = register(client)
    assert set(client.get('/api/auth/me', headers=owner).json()) == {'id', 'email', 'name'}
    logged_in = client.post('/api/auth/login', json={'email': 'owner@example.com', 'password': 'correct horse 47'})
    assert set(logged_in.json()['user']) == {'id', 'email', 'name'}
    monkeypatch.setattr(client.app.state.settings, 'diagnostics_token', 'operator-token')
    features = client.get('/api/system', headers={'Authorization': 'Bearer operator-token'}).json()['features']
    assert not {'organization_roles', 'oidc_configured'} & features.keys()
    assert all(features[key] for key in ['hybrid_retrieval', 'bounded_agent', 'revocable_sessions',
        'persistent_bm25', 'document_versions', 'async_evaluations', 'durable_cleanup'])
