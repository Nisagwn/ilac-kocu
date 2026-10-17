"""Eğitim içeriği hekim onayı (review_status, reviewed_by, reviewed_at)

Revision ID: 0010_education_review
Revises: 0006_security
Create Date: 2026-10-17 12:00:00.000000

`reviewed` (bool) geriye dönük uyumluluk için korunur ve review_status == 'approved' ile eş tutulur.
Mevcut reviewed=true satırlar 'approved' olarak işaretlenir. Politika: docs/egitim-icerigi-onayi.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010_education_review'
down_revision: Union[str, None] = '0008_idempotency'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('education_contents') as batch:
        batch.add_column(sa.Column('review_status', sa.String(length=10), nullable=False, server_default='pending'))
        batch.add_column(sa.Column('reviewed_by', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('fk_education_contents_reviewed_by_users', 'users', ['reviewed_by'], ['id'],
                                 ondelete='SET NULL')
        batch.create_index(op.f('ix_education_contents_review_status'), ['review_status'], unique=False)
    op.execute("UPDATE education_contents SET review_status = 'approved' WHERE reviewed")


def downgrade() -> None:
    with op.batch_alter_table('education_contents') as batch:
        batch.drop_index(op.f('ix_education_contents_review_status'))
        batch.drop_constraint('fk_education_contents_reviewed_by_users', type_='foreignkey')
        batch.drop_column('reviewed_at')
        batch.drop_column('reviewed_by')
        batch.drop_column('review_status')
