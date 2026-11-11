"""Persist grounded learning drafts and generated task evidence."""

import sqlalchemy as sa
from alembic import op

revision = "008_learning_general"
down_revision = "007_learning"


def upgrade():
    op.add_column("learning_workspaces", sa.Column("source_fingerprint", sa.String(64)))
    op.add_column("learning_capabilities", sa.Column("coverage_details", sa.JSON()))
    op.add_column("learning_tasks", sa.Column("generation_details", sa.JSON()))
    op.add_column("learning_jobs", sa.Column("input_payload", sa.JSON()))


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
