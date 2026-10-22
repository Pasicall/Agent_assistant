
from test_api import create_kb, register, wait_job


def test_incremental_folder_sync_and_path_boundary(client, tmp_path, monkeypatch):
    headers = register(client)
    kb = create_kb(client, headers)
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'policy.txt').write_text('住宿报销上限500元。', encoding='utf-8')
    monkeypatch.setattr(client.app.state.settings, 'source_sync_root', root)
    body = {'knowledge_base_id': kb, 'relative_path': '.'}
    response = client.post('/api/sources/sync', headers=headers, json=body)
    assert response.status_code == 200, response.text
    row = response.json()['files'][0]
    wait_job(client, headers, row['job_id'])
    assert client.post('/api/sources/sync', headers=headers, json=body).json()['files'][0]['unchanged']
    assert client.post('/api/sources/sync', headers=headers,
        json={**body, 'relative_path': '../outside'}).status_code == 422



def test_source_sync_rejects_unconfigured_root_and_escaping_symlinks(client, tmp_path, monkeypatch):
    headers = register(client)
    kb = create_kb(client, headers)
    body = {'knowledge_base_id': kb, 'relative_path': '.'}
    assert client.post('/api/sources/sync', headers=headers, json=body).status_code == 503
    root = tmp_path / 'configured-source'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('must not be imported')
    (root / 'escape.txt').symlink_to(outside)
    monkeypatch.setattr(client.app.state.settings, 'source_sync_root', root)
    assert client.post('/api/sources/sync', headers=headers, json=body).status_code == 422
    assert client.get('/api/documents', headers=headers).json() == []
