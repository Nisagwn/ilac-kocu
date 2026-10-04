"""RAG: eğitim içeriği embedding (pgvector), kind, drug_key, reviewed

Revision ID: 0003_rag
Revises: 0002
Create Date: 2026-10-04 12:00:00.000000

PostgreSQL'de `vector` eklentisi kurulur, embedding `vector(384)` olur ve HNSW (cosine) indeksi
oluşturulur. Diğer veritabanlarında (SQLite) embedding JSON olarak saklanır, vektör indeksi yoktur.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = '0003_rag'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384
HNSW_INDEX = 'ix_education_contents_embedding_hnsw'


def _is_pg() -> bool:
    return op.get_context().dialect.name == 'postgresql'


def upgrade() -> None:
    pg = _is_pg()
    if pg:
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    embedding_type = Vector(EMBEDDING_DIM) if pg else sa.JSON()
    with op.batch_alter_table('education_contents') as batch:
        batch.add_column(sa.Column('kind', sa.String(length=20), nullable=False, server_default='curated'))
        batch.add_column(sa.Column('drug_key', sa.String(length=80), nullable=True))
        batch.add_column(sa.Column('reviewed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('embedding', embedding_type, nullable=True))
        batch.add_column(sa.Column('embedding_model', sa.String(length=80), nullable=True))
    op.create_index(op.f('ix_education_contents_drug_key'), 'education_contents', ['drug_key'], unique=True)
    if pg:
        op.execute(f'CREATE INDEX {HNSW_INDEX} ON education_contents USING hnsw (embedding vector_cosine_ops)')


def downgrade() -> None:
    if _is_pg():
        op.execute(f'DROP INDEX IF EXISTS {HNSW_INDEX}')
    op.drop_index(op.f('ix_education_contents_drug_key'), table_name='education_contents')
    with op.batch_alter_table('education_contents') as batch:
        batch.drop_column('embedding_model')
        batch.drop_column('embedding')
        batch.drop_column('reviewed')
        batch.drop_column('drug_key')
        batch.drop_column('kind')
    # Not: `vector` eklentisi başka nesnelerce kullanılabileceği için kaldırılmaz.
