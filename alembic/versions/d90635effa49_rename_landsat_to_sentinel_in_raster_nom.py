"""rename landsat to sentinel in raster.nom

Revision ID: d90635effa49
Revises: 864f42675c79
Create Date: 2026-09-24 15:36:57.117366

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd90635effa49'
down_revision: Union[str, None] = '864f42675c79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
