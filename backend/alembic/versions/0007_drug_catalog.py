"""İlaç kataloğu (drug_catalog) ve ilaçların katalog bağlantısı (medications.catalog_id, verified)

Revision ID: 0007_drug_catalog
Revises: 0006_security
Create Date: 2026-10-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007_drug_catalog'
down_revision: Union[str, None] = '0006_security'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('drug_catalog',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('generic_name', sa.String(length=160), nullable=False),
    sa.Column('brand_names', sa.JSON(), nullable=False),
    sa.Column('indication_group', sa.String(length=20), nullable=False),
    sa.Column('pharm_class', sa.String(length=160), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('label_count', sa.Integer(), nullable=True),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('generic_name')
    )
    op.add_column('medications', sa.Column('catalog_id', sa.Integer(), nullable=True))
    op.add_column('medications', sa.Column('verified', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index(op.f('ix_medications_catalog_id'), 'medications', ['catalog_id'], unique=False)
    op.create_foreign_key('fk_medications_catalog_id', 'medications', 'drug_catalog', ['catalog_id'], ['id'],
                          ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_medications_catalog_id', 'medications', type_='foreignkey')
    op.drop_index(op.f('ix_medications_catalog_id'), table_name='medications')
    op.drop_column('medications', 'verified')
    op.drop_column('medications', 'catalog_id')
    op.drop_table('drug_catalog')
