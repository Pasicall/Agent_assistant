"""Add goal-driven learning records without changing existing tables."""

from alembic import op

from migrations.learning_v1 import Base

revision = "007_learning"
down_revision = "006_operations"


def upgrade():
    for table in Base.metadata.sorted_tables:
        if table.name.startswith("learning_"):
            table.create(op.get_bind())


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
