"""Reorganize utilisateur columns order

Revision ID: 5e182e818d1f
Revises: ce8632d27bce
Create Date: 2026-09-04 14:23:28.268890

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e182e818d1f'
down_revision: Union[str, None] = 'ce8632d27bce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
