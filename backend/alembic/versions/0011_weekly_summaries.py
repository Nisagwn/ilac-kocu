"""Zamanlanmış haftalık özetler (weekly_summaries)

Revision ID: 0011_weekly_summaries
Revises: 0010_education_review
Create Date: 2026-10-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011_weekly_summaries'
down_revision: Union[str, None] = '0010_education_review'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('weekly_summaries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('narrative_source', sa.String(length=20), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('patient_id', 'period_end', name='uq_weekly_summary_patient_period')
    )
    op.create_index(op.f('ix_weekly_summaries_patient_id'), 'weekly_summaries', ['patient_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_weekly_summaries_patient_id'), table_name='weekly_summaries')
    op.drop_table('weekly_summaries')
