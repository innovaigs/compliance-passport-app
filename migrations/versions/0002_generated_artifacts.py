"""generated_artifacts

Stores the full source of every model-authored program, plus what it did.

Hand-corrected after autogenerate: the generated version also emitted
op.drop_table for evidence_chunks_fts and its SQLite shadow tables, because the
FTS5 virtual table is not part of Base.metadata and therefore reads as a table
to remove. migrations/env.py now filters those names out of autogenerate.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14 17:23:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'generated_artifacts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('run_id', sa.String(length=36), nullable=False),
        sa.Column('phase', sa.String(length=20), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('origin', sa.String(length=20), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('line_count', sa.Integer(), nullable=False),
        sa.Column('stdout', sa.Text(), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('sandbox_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['questionnaire_runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('generated_artifacts', schema=None) as batch_op:
        batch_op.create_index('idx_gartifact_run_id', ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('generated_artifacts', schema=None) as batch_op:
        batch_op.drop_index('idx_gartifact_run_id')

    op.drop_table('generated_artifacts')
