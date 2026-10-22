
from sqlalchemy import select
from test_api import create_kb, register, upload, wait_job


def test_persistent_bm25_loads_only_candidates(client, monkeypatch):
    from app.services import corpus, evidence
    from app.services.retrieval import retrieve
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    from app.models import User
    with client.app.state.database.session() as db:
        user = db.scalar(select(User))
        scope = corpus.CorpusScope(db, user, [kb])
        real_tokenize = evidence.tokenize
        calls = []
        def track(text):
            calls.append(text)
            return real_tokenize(text)
        monkeypatch.setattr(evidence, 'tokenize', track)
        hits = retrieve('住宿报销', scope, strategy='bm25')
        assert hits and hits[0]['document_id'] == uploaded['document']['id']
        assert calls == ['住宿报销']
