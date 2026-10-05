"""Add server_default for competition_questions id and created_at

Revision ID: 0002_add_question_server_defaults
Revises: 0001_initial_schema
Create Date: 2026-10-05 10:20:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_question_defaults"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "competition_questions",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )
    op.alter_column(
        "competition_questions",
        "created_at",
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.alter_column(
        "competition_questions",
        "id",
        server_default=None,
    )
    op.alter_column(
        "competition_questions",
        "created_at",
        server_default=None,
    )
