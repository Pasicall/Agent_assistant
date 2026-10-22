import threading

from test_api import ask, create_kb, register, upload, wait_job


def test_version_rebuild_keeps_old_until_atomic_switch_and_can_restore(client, monkeypatch):
    from app.services import parsing
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    doc = uploaded['document']['id']
    original = client.get(f'/api/documents/{doc}/versions', headers=headers).json()[0]['id']
    entered, resume = threading.Event(), threading.Event()
    real_parse = parsing.parse_document
    def blocking_parse(path, filename, **kwargs):
        if b'600' in path.read_bytes():
            entered.set()
            assert resume.wait(10)
        return real_parse(path, filename, **kwargs)
    monkeypatch.setattr(parsing, 'parse_document', blocking_parse)
    try:
        response = client.post('/api/documents/upload', headers=headers,
            data={'knowledge_base_id': kb, 'document_id': doc}, files={'file': ('updated.txt', '住宿报销上限600元。'.encode())})
        assert response.status_code == 201, response.text
        assert entered.wait(5)
        assert '500' in ask(client, headers, kb)['answer']
        resume.set()
        assert wait_job(client, headers, response.json()['job_id'])['status'] == 'completed'
        answer = ask(client, headers, kb)
        assert '600' in answer['answer'] and answer['citations'][0]['revision_id'] != original
        restored = client.post(f'/api/documents/{doc}/versions/{original}/restore', headers=headers)
        assert restored.status_code == 202, restored.text
        wait_job(client, headers, restored.json()['job_id'])
        assert '500' in ask(client, headers, kb)['answer']
        assert '500' in client.get(f'/api/documents/{doc}/versions/{original}/download', headers=headers).text
    finally:
        resume.set()



def test_expiry_invalidates_cached_answer(client):
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    assert '500' in ask(client, headers, kb)['answer']
    doc = uploaded['document']['id']
    expired = client.patch(f'/api/documents/{doc}/metadata', headers=headers,
        json={'expires_at': '2020-01-01T00:00:00Z', 'source': 'policy:v1'})
    assert expired.status_code == 200
    answer = ask(client, headers, kb)
    assert answer['citations'] == [] and answer['abstained']
