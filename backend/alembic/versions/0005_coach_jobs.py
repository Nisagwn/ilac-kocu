"""Asenkron koç işleri (coach_jobs)

Revision ID: 0005_coach_jobs
Revises: 0004_llm_usage
Create Date: 2026-10-04 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005_coach_jobs'
down_revision: Union[str, None] = '0004_llm_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('coach_jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('reply', sa.JSON(), nullable=True),
    sa.Column('error', sa.String(length=200), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_coach_jobs_patient_id'), 'coach_jobs', ['patient_id'], unique=False)
    op.create_index(op.f('ix_coach_jobs_status'), 'coach_jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_coach_jobs_status'), table_name='coach_jobs')
    op.drop_index(op.f('ix_coach_jobs_patient_id'), table_name='coach_jobs')
    op.drop_table('coach_jobs')
