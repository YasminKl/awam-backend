"""empty message

Revision ID: 7f3d22c69e3b
Revises: 5e182e818d1f
Create Date: 2026-09-07 09:35:47.776817

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3d22c69e3b'
down_revision: Union[str, None] = '5e182e818d1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
