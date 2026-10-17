"""Zamanlanmış haftalık özetler (weekly_summaries) ve koç işleri için DLQ alanları

Revision ID: 0010_weekly_summaries
Revises: 0009_education_review
Create Date: 2026-10-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010_weekly_summaries'
down_revision: Union[str, None] = '0009_education_review'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('weekly_summaries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('narrative_source', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('patient_id', 'period_start', name='uq_weekly_summary_period')
    )
    op.create_index(op.f('ix_weekly_summaries_patient_id'), 'weekly_summaries', ['patient_id'], unique=False)
    op.add_column('coach_jobs', sa.Column('dead_lettered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('coach_jobs', sa.Column('last_error', sa.String(length=80), nullable=True))
    op.create_index(op.f('ix_coach_jobs_dead_lettered_at'), 'coach_jobs', ['dead_lettered_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_coach_jobs_dead_lettered_at'), table_name='coach_jobs')
    op.drop_column('coach_jobs', 'last_error')
    op.drop_column('coach_jobs', 'dead_lettered_at')
    op.drop_index(op.f('ix_weekly_summaries_patient_id'), table_name='weekly_summaries')
    op.drop_table('weekly_summaries')
