"""Eğitim içeriği hekim onayı (education_contents.review_status, reviewed_by_user_id, reviewed_at, review_note)

Revision ID: 0009_education_review
Revises: 0008_idempotency
Create Date: 2026-10-17 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009_education_review'
down_revision: Union[str, None] = '0008_idempotency'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('education_contents', sa.Column('review_status', sa.String(length=10), server_default='pending',
                                                  nullable=False))
    op.add_column('education_contents', sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True))
    op.add_column('education_contents', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('education_contents', sa.Column('review_note', sa.Text(), nullable=True))
    op.create_index(op.f('ix_education_contents_review_status'), 'education_contents', ['review_status'],
                    unique=False)
    op.create_foreign_key('fk_education_contents_reviewed_by', 'education_contents', 'users',
                          ['reviewed_by_user_id'], ['id'], ondelete='SET NULL')
    # Önceden "reviewed" işaretli içerik onaylı sayılır
    op.execute("UPDATE education_contents SET review_status = 'approved' WHERE reviewed")


def downgrade() -> None:
    op.drop_constraint('fk_education_contents_reviewed_by', 'education_contents', type_='foreignkey')
    op.drop_index(op.f('ix_education_contents_review_status'), table_name='education_contents')
    op.drop_column('education_contents', 'review_note')
    op.drop_column('education_contents', 'reviewed_at')
    op.drop_column('education_contents', 'reviewed_by_user_id')
    op.drop_column('education_contents', 'review_status')
