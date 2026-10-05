"""Add full_name and phone_number to participants

Revision ID: 0004_participant_name_and_phone
Revises: 0003_exam_session_timestamps
Create Date: 2026-10-05 15:30:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_participant_name_and_phone"
down_revision: Union[str, None] = "0003_exam_session_timestamps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("full_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "participants",
        sa.Column("phone_number", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("participants", "phone_number")
    op.drop_column("participants", "full_name")
