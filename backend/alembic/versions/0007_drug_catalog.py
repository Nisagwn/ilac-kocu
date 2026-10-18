"""İlaç kataloğu (drug_catalog) ve ilaç-katalog bağlantısı (medications.catalog_id, verified)

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
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('brand_names', sa.JSON(), nullable=False),
    sa.Column('tr_brand_names', sa.JSON(), nullable=False),
    sa.Column('pharm_class', sa.String(length=200), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('openfda_available', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_drug_catalog_generic_name'), 'drug_catalog', ['generic_name'], unique=True)

    with op.batch_alter_table('medications') as batch:
        batch.add_column(sa.Column('catalog_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('verified', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.create_index(batch.f('ix_medications_catalog_id'), ['catalog_id'], unique=False)
        batch.create_foreign_key('fk_medications_catalog_id_drug_catalog', 'drug_catalog', ['catalog_id'], ['id'],
                                 ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('medications') as batch:
        batch.drop_constraint('fk_medications_catalog_id_drug_catalog', type_='foreignkey')
        batch.drop_index(batch.f('ix_medications_catalog_id'))
        batch.drop_column('verified')
        batch.drop_column('catalog_id')
    op.drop_index(op.f('ix_drug_catalog_generic_name'), table_name='drug_catalog')
    op.drop_table('drug_catalog')
