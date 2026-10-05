"""Add actual_exam_started_at and actual_exam_ends_at to competitions

Revision ID: 0003_exam_session_timestamps
Revises: 0002_question_defaults
Create Date: 2026-10-05 14:20:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_exam_session_timestamps"
down_revision: Union[str, None] = "0002_question_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "competitions",
        sa.Column("actual_exam_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("actual_exam_ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("competitions", "actual_exam_ends_at")
    op.drop_column("competitions", "actual_exam_started_at")
