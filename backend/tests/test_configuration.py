"""Deployment examples must agree with the settings supported by the personal app."""
from pathlib import Path

import yaml
from app.config import Settings
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def test_compose_only_forwards_supported_application_settings():
    compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
    supported = {'RAG_' + key.upper() for key in Settings.model_fields}
    for service in ['api', 'worker', 'evaluation-worker', 'learning-worker', 'migrate']:
        variables = set(compose['services'][service]['environment'])
        assert {key for key in variables if key.startswith('RAG_')} <= supported
    assert compose['services']['api']['environment']['RAG_ENVIRONMENT'] == 'production'
    assert compose['services']['worker']['environment']['RAG_WORKER_ROLE'] == 'ingestion'
    assert compose['services']['evaluation-worker']['environment']['RAG_WORKER_ROLE'] == 'evaluation'
    assert compose['services']['learning-worker']['environment']['RAG_WORKER_ROLE'] == 'learning'


def test_environment_examples_have_no_removed_or_unknown_application_fields():
    deployment = {'RAG_SERVICE_MEMORY', 'RAG_SERVICE_CPUS'}
    supported = {'RAG_' + key.upper() for key in Settings.model_fields} | deployment
    for name in ['.env.example', '.env.ollama.example', '.env.production.example']:
        variables = dotenv_values(ROOT / name)
        assert {key for key in variables if key.startswith('RAG_')} <= supported
    ignores = (ROOT / '.dockerignore').read_text().splitlines()
    assert '.conda-env' in ignores and '.env*' in ignores and 'artifacts' in ignores
