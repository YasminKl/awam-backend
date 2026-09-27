"""create parcelle table

Revision ID: d97e5513ec18
Revises: 9562a61acb6b
Create Date: 2026-09-15 11:30:03.883726

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


# revision identifiers, used by Alembic.
revision: str = 'd97e5513ec18'
down_revision: Union[str, None] = '9562a61acb6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('parcelle',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nom', sa.String(length=150), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='MULTIPOLYGON', srid=4326, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['utilisateur.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parcelle_id'), 'parcelle', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_parcelle_id'), table_name='parcelle')
    op.drop_table('parcelle')