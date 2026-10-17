"""Idempotency-Key kayıtları (idempotency_keys)

Revision ID: 0008_idempotency
Revises: 0006_security
Create Date: 2026-10-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008_idempotency'
down_revision: Union[str, None] = '0007_drug_catalog'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('idempotency_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'key', name='uq_idempotency_user_key')
    )
    op.create_index(op.f('ix_idempotency_keys_created_at'), 'idempotency_keys', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_idempotency_keys_created_at'), table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
