"""Isolated personal-document acceptance and before/after retrieval comparison.

Uses real repository documentation. Never opens an existing business database.
Configured mode uses the selected models; demo mode explicitly uses hash/excerpts.
Generation mode tests the selected remote LLM with hash vectors, without accepting
semantic retrieval quality or invoking any local model.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    ('文档上传默认最多接受多少 MiB 的文件？', ['50']),
    ('切块默认窗口和重叠分别是多少字符？', ['800', '120']),
    ('Worker 的任务租约默认为多少秒？', ['600']),
]


def wait(client, headers, path, terminal):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        if result['status'] in terminal:
            assert result['status'] == 'completed', result
            return result
        time.sleep(.05)
    raise TimeoutError(path)


def compare(before, after):
    assert before['mode'] == after['mode'], 'Comparison must use identical probe modes'
    assert before['settings'] == after['settings'], 'Comparison must use identical model/retrieval settings'
    assert before['identities'] == after['identities'], 'Restore must preserve resource IDs'
    max_delta = 0
    for old, new in zip(before['questions'], after['questions'], strict=True):
        assert old['question'] == new['question']
        for strategy in ['dense', 'bm25', 'hybrid']:
            left, right = old['rankings'][strategy], new['rankings'][strategy]
            assert len(left) == len(right), strategy
            for a, b in zip(left, right, strict=True):
                for key in a:
                    if key == 'dense_score' and a[key] is not None:
                        delta = abs(a[key] - b[key])
                        assert delta < 1e-6, (strategy, key, delta)
                        max_delta = max(max_delta, delta)
                    else:
                        assert a[key] == b[key], (strategy, key)
        # Model wording can vary; both runs must contain the expected facts and valid source IDs.
        for mode in ['standard', 'agent']:
            assert old['answers'][mode]['facts_present'] and new['answers'][mode]['facts_present']
            if before['mode'] == 'demo':
                assert old['answers'][mode]['answer'] == new['answers'][mode]['answer']
    return {'passed': True, 'identical_ranked_evidence': True, 'resource_ids_preserved': True,
        'answer_facts_and_sources_valid': True, 'max_dense_score_delta': max_delta,
        'dense_score_absolute_tolerance': 1e-6}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['demo', 'generation', 'configured'], default='demo',
        help='demo: hash/excerpts; generation: remote LLM with hash, no semantic acceptance; configured: semantic models')
    parser.add_argument('--code-root', type=Path, default=ROOT, help='Frozen checkout for before/after comparison')
    parser.add_argument('--restore', type=Path, help='Restore a coordinated probe backup into the new isolated database')
    parser.add_argument('--snapshot', type=Path, help='Save a coordinated probe backup after stopping the worker')
    parser.add_argument('--compare', type=Path, help='Compare against a previous probe JSON')
    parser.add_argument('--with-ragas', action='store_true', help='Run optional real-model Ragas scoring during lifecycle validation')
    parser.add_argument('--lifecycle', action='store_true', help='Also replace, restore, evaluate and delete repository documents')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/personal-validation.json')
    args = parser.parse_args()
    if args.with_ragas and args.mode != 'configured':
        parser.error('--with-ragas requires --mode configured and semantic embeddings')
    sys.path.insert(0, str(args.code_root.resolve() / 'backend'))
    with tempfile.TemporaryDirectory(prefix='rag-personal-') as temporary:
        root = Path(temporary)
        os.environ.update(RAG_DATA_DIR=temporary, RAG_DATABASE_URL='sqlite:///' + str(root / 'personal.db'),
            RAG_REDIS_URL='', RAG_QDRANT_URL='', RAG_ENVIRONMENT='development', RAG_AUTO_MIGRATE='true',
            RAG_WORKER_POLL_SECONDS='.03', RAG_CHAT_RATE_PER_MINUTE='200', RAG_QDRANT_COLLECTION_PREFIX='personal_acceptance')
        if args.mode in {'demo', 'generation'}:
            os.environ.update(RAG_EMBEDDING_PROVIDER='hash', RAG_EMBEDDING_MODEL='deterministic-hash-demo',
                RAG_EMBEDDING_DIMENSION='384', RAG_RERANKER_MODEL='', RAG_VISION_MODEL='',
                RAG_OCR_ENABLED='false', RAG_CITATION_VERIFIER='numeric')
        if args.mode == 'demo':
            os.environ.update(RAG_LLM_MODEL='', RAG_LLM_API_KEY='')
        from app.config import get_settings
        from app.main import create_app
        from app.maintenance import backup, restore, verify_backup
        from app.models import Chunk, CleanupTask, Document, DocumentRevision, KnowledgeBase, User
        from app.services import retrieval
        from app.services.corpus import CorpusScope
        from fastapi.testclient import TestClient
        from sqlalchemy import select, text
        settings = get_settings()
        if args.mode == 'configured':
            assert settings.embedding_provider != 'hash' and settings.llm_model, 'Configure semantic embeddings and generation'
        if args.mode == 'generation':
            from urllib.parse import urlsplit
            assert settings.llm_provider == 'openai' and settings.llm_model and settings.llm_api_key, 'Configure a remote LLM'
            assert urlsplit(settings.llm_base_url).hostname not in {'localhost', '127.0.0.1', '::1'}, 'Use a remote model endpoint'
        if args.restore:
            restore(settings, args.restore.resolve(), quiesced=True)
        report = {'mode': args.mode, 'material': 'Actual repository architecture and deployment documentation',
            'settings': {key: getattr(settings, key) for key in ['embedding_provider', 'embedding_model',
                'embedding_dimension', 'llm_provider', 'llm_model', 'reranker_model', 'chunk_size', 'chunk_overlap',
                'agent_max_rewrites', 'citation_verifier', 'ollama_num_ctx', 'max_output_tokens']},
            'scope': {'semantic_embedding_quality_tested': args.mode == 'configured', 'ragas_tested': args.with_ragas,
                'local_model_inference': False if args.mode in {'demo', 'generation'} else 'depends on configuration'},
            'questions': [], 'checks': {}}
        try:
            with TestClient(create_app()) as client:
                account = {'email': 'personal-acceptance@fixture.local', 'password': 'Synthetic-personal-validation-47'}
                response = client.post('/api/auth/login' if args.restore else '/api/auth/register',
                    json=account if args.restore else {**account, 'name': '个人资料验收'})
                assert response.status_code in {200, 201}, response.text
                headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
                if args.restore:
                    kb = client.get('/api/knowledge-bases', headers=headers).json()[0]['id']
                    docs = client.get('/api/documents', headers=headers).json()
                else:
                    kb = client.post('/api/knowledge-bases', headers=headers, json={'name': '项目实际文档'}).json()['id']
                    docs = []
                    for name in ['architecture.md', 'deployment.md']:
                        content = (ROOT / 'docs' / name).read_bytes()
                        response = client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
                            files={'file': (name, content)})
                        assert response.status_code == 201, response.text
                        result = response.json()
                        wait(client, headers, '/api/jobs/' + result['job_id'], {'completed', 'failed'})
                        docs.append(result['document'])
                doc = next(row for row in docs if row['name'] == 'architecture.md')
                original = client.get('/api/documents/' + doc['id'] + '/download', headers=headers)
                assert original.status_code == 200 and original.content == (ROOT / 'docs/architecture.md').read_bytes()
                duplicate = client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
                    files={'file': ('architecture.md', original.content)})
                assert duplicate.json()['duplicate'] and duplicate.json()['document']['id'] == doc['id']
                report['checks'].update(upload_and_dedup=True, original_download=True)
                with client.app.state.database.session() as db:
                    report['identities'] = {model.__tablename__: sorted(db.scalars(select(model.id)).all())
                        for model in [KnowledgeBase, Document, DocumentRevision, Chunk]}
                    report['migration_head'] = db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
                for question, facts in QUESTIONS:
                    row = {'question': question, 'expected_facts': facts, 'rankings': {}, 'answers': {}}
                    with client.app.state.database.session() as db:
                        user = db.scalar(select(User))
                        for strategy in ([] if args.mode == 'generation' else ['dense', 'bm25', 'hybrid']):
                            hits = retrieval.retrieve(question, CorpusScope(db, user, [kb]), top_k=5, strategy=strategy)
                            row['rankings'][strategy] = [{key: hit.get(key) for key in ['chunk_id', 'document_id',
                                'document_name', 'revision_id', 'page', 'ordinal', 'text', 'score', 'dense_score', 'bm25_score']} for hit in hits]
                    for mode in ['standard', 'agent']:
                        print(f'Evaluating {mode}: {question}', flush=True)
                        body = {'question': question, 'knowledge_base_ids': [kb], 'mode': mode, 'top_k': 5,
                            'request_id': 'personal-probe-' + uuid.uuid4().hex}
                        response = client.post('/api/chat', headers=headers, json=body)
                        assert response.status_code == 200, response.text
                        result = response.json()
                        assert result['citations'], (question, result)
                        facts_present = all(fact in result['answer'] for fact in facts)
                        assert facts_present, (question, result['answer'])
                        if args.mode != 'demo':
                            assert result['usage']['model_calls'] >= 1, result
                        for citation in result['citations']:
                            download = client.get(f'/api/documents/{citation["document_id"]}/versions/{citation["revision_id"]}/download', headers=headers)
                            assert download.status_code == 200 and citation['knowledge_base_id'] == kb
                        row['answers'][mode] = {key: result.get(key) for key in ['answer', 'citations', 'provider', 'verification', 'usage']}
                        row['answers'][mode]['facts_present'] = facts_present
                        recorded = result
                        if not report['questions'] and mode == 'standard':
                            replay = client.post('/api/chat', headers=headers, json=body)
                            assert replay.status_code == 200 and replay.json()['message_id'] == result['message_id']
                            report['checks']['idempotent_replay'] = True
                            if settings.cache_ttl > 0 and result['verification']['passed']:
                                cached = client.post('/api/chat', headers=headers,
                                    json={**body, 'request_id': 'personal-cache-' + uuid.uuid4().hex})
                                assert cached.status_code == 200 and cached.json()['cached']
                                assert cached.json()['usage']['model_calls'] == 0
                                report['checks']['answer_cache_without_model_call'] = True
                    report['questions'].append(row)
                answers = [answer for row in report['questions'] for answer in row['answers'].values()]
                report['generation_results'] = {'answers': len(answers),
                    'direct_model_answers': sum(answer['provider'] == settings.llm_model for answer in answers),
                    'extractive_fallback_answers': sum(answer['provider'].startswith('extractive (') for answer in answers),
                    'numeric_verification_passed': sum(answer['verification']['mode'] == 'numeric'
                        and bool(answer['verification']['passed']) for answer in answers),
                    'semantic_fact_verification_tested': any(answer['verification'].get('semantic_checked', False) for answer in answers)}
                report['checks'].update(standard_and_agent=True, citation_downloads=True, model_calls=args.mode != 'demo')
                if args.compare:
                    report['comparison'] = compare(json.loads(args.compare.read_text()), report)
                if args.lifecycle:
                    case = {'question': QUESTIONS[-1][0], 'reference': 'Worker 的任务租约默认600秒。', 'expected_document_ids': [doc['id']]}
                    if args.mode != 'generation':
                        response = client.post('/api/evaluations/compare', headers=headers, json={'knowledge_base_ids': [kb], 'cases': [case]})
                        assert response.status_code == 200
                        report['retrieval_comparison'] = response.json()
                    response = client.post('/api/evaluations', headers=headers, json={'name': '真实项目文档历史复评',
                        'knowledge_base_ids': [kb], 'cases': [{**case, 'message_id': recorded['message_id']}], 'use_ragas': args.with_ragas})
                    assert response.status_code == 202, response.text
                    evaluation = wait(client, headers, '/api/evaluations/' + response.json()['id'], {'completed', 'failed'})
                    assert evaluation['metrics']['history_replayed_cases'] == 1
                    report['evaluation_metrics'] = evaluation['metrics']
                    ordinary = client.post('/api/evaluations', headers=headers, json={'name': '项目文档普通评测',
                        'knowledge_base_ids': [kb], 'cases': [case], 'use_ragas': False})
                    assert ordinary.status_code == 202, ordinary.text
                    ordinary = wait(client, headers, '/api/evaluations/' + ordinary.json()['id'], {'completed', 'failed'})
                    assert ordinary['metrics']['history_replayed_cases'] == 0 and '600' in ordinary['results'][0]['answer']
                    report['ordinary_evaluation_metrics'] = ordinary['metrics']
                    cache_ttl = settings.cache_ttl
                    settings.cache_ttl = 0  # Exercise actual provider streaming, rather than replaying the preceding answer cache.
                    try:
                        stream = client.post('/api/chat/stream', headers=headers,
                            json={'question': QUESTIONS[-1][0], 'knowledge_base_ids': [kb]})
                    finally:
                        settings.cache_ttl = cache_ttl
                    assert stream.status_code == 200 and 'event: done' in stream.text
                    final = json.loads(stream.text.split('event: done\ndata: ')[1].split('\n\n')[0])
                    assert final['citations'] and '600' in final['answer']
                    report['stream'] = {'draft_events': stream.text.count('event: draft'),
                        **{key: final[key] for key in ['answer', 'provider', 'usage', 'verification', 'cached']}}
                    if args.mode != 'demo':
                        assert report['stream']['draft_events'] > 0 and not final['cached'] and final['usage']['model_calls'] >= 1
                    empty_kb = client.post('/api/knowledge-bases', headers=headers, json={'name': '空库拒答检查'}).json()['id']
                    empty = client.post('/api/chat', headers=headers,
                        json={'question': QUESTIONS[0][0], 'knowledge_base_ids': [empty_kb]}).json()
                    assert empty['abstained'] and not empty['citations'] and empty['usage']['model_calls'] == 0
                    assert client.delete('/api/knowledge-bases/' + empty_kb, headers=headers).status_code == 204
                    versions = client.get('/api/documents/' + doc['id'] + '/versions', headers=headers).json()
                    original_revision = next(row['id'] for row in versions if row['active'])
                    response = client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb, 'document_id': doc['id']},
                        files={'file': ('design.md', (ROOT / 'docs/design.md').read_bytes())})
                    assert response.status_code == 201, response.text
                    wait(client, headers, '/api/jobs/' + response.json()['job_id'], {'completed', 'failed'})
                    response = client.post(f'/api/documents/{doc["id"]}/versions/{original_revision}/restore', headers=headers)
                    assert response.status_code == 202, response.text
                    wait(client, headers, '/api/jobs/' + response.json()['job_id'], {'completed', 'failed'})
                    assert client.get('/api/documents/' + doc['id'] + '/download', headers=headers).content == original.content
                    assert client.delete('/api/documents/' + doc['id'], headers=headers).status_code == 204
                    assert client.get('/api/conversations/' + recorded['conversation_id'], headers=headers).json()['messages'][1]['citations'] == []
                    assert client.get('/api/evaluations/' + evaluation['id'], headers=headers).json()['status'] == 'restricted'
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        with client.app.state.database.session() as db:
                            tasks = db.scalars(select(CleanupTask).where(CleanupTask.document_id == doc['id'], CleanupTask.revision_id.is_(None))).all()
                            if tasks and all(task.status == 'completed' for task in tasks):
                                break
                        time.sleep(.05)
                    else:
                        raise TimeoutError('Durable deletion did not complete')
                    report['checks'].update(history_evaluation=True, ordinary_evaluation=True, stream=True,
                        empty_scope_abstention_without_model_call=True, replacement=True, historical_restore=True,
                        deletion=True, deleted_history_hidden=True, cleanup_completed=True)
                    if args.mode != 'generation':
                        report['checks']['dense_hybrid_comparison'] = True
                report['passed'] = True
            if retrieval.client.cache_info().currsize:
                retrieval.client().close()
                retrieval.client.cache_clear()
            if args.snapshot:
                backup(settings, args.snapshot.resolve(), quiesced=True)
                verify_backup(args.snapshot)
                report['checks']['coordinated_backup_verified'] = True
        finally:
            if retrieval.client.cache_info().currsize:
                retrieval.client().close()
                retrieval.client.cache_clear()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'mode': args.mode, 'checks': report['checks'],
            'comparison': report.get('comparison')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
