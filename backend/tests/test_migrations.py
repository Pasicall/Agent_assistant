
from app.models import Document
from sqlalchemy import select


def test_legacy_database_migrates_without_losing_content(tmp_path):
    from app.config import Settings
    from app.db import Database
    from migrations.schema_v1 import Base
    settings = Settings(_env_file=None, database_url='sqlite:///' + (tmp_path / 'legacy.db').as_posix(),
                        redis_url='', qdrant_url='', environment='development')
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    tables = Base.metadata.tables
    with database.engine.begin() as connection:
        connection.execute(tables['users'].insert().values(id='u', email='legacy@example.com', name='legacy', password_hash='x'))
        connection.execute(tables['knowledge_bases'].insert().values(id='k', user_id='u', name='legacy'))
        connection.execute(tables['documents'].insert().values(id='d', knowledge_base_id='k', name='policy.txt',
            stored_path='unused', sha256='hash', size_bytes=20, status='ready', chunk_count=1))
        connection.execute(tables['chunks'].insert().values(id='c', document_id='d', text='报销500元', ordinal=0))
    database.initialize()
    database.initialize()
    from app.models import Chunk, ChunkTerm
    with database.session() as db:
        doc, chunk = db.get(Document, 'd'), db.get(Chunk, 'c')
        assert doc.active_revision_id == chunk.revision_id and chunk.text == '报销500元'
        assert db.scalar(select(ChunkTerm).where(ChunkTerm.chunk_id == 'c'))
    database.engine.dispose()



def test_runtime_metadata_excludes_enterprise_models_but_keeps_frozen_tables(client):
    from app import models
    from sqlalchemy import inspect, text

    removed = {'Membership', 'KnowledgeBaseGrant', 'AuditEvent', 'OIDCIdentity', 'OIDCFlow', 'OperationalPolicy'}
    assert all(not hasattr(models, name) for name in removed)
    legacy_tables = {'memberships', 'knowledge_base_grants', 'audit_events', 'oidc_identities', 'oidc_flows', 'operational_policies'}
    assert not legacy_tables & models.Base.metadata.tables.keys()
    assert set(models.Organization.__table__.columns.keys()) == {'id'}
    assert models.Base.metadata.sorted_tables  # Compatibility foreign keys still resolve.
    assert legacy_tables <= set(inspect(client.app.state.database.engine).get_table_names())
    with client.app.state.database.session() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '006_operations'
