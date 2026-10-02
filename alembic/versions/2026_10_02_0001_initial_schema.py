"""Initial database schema for competitive exam platform

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-02 21:00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Competitions
    op.create_table(
        "competitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_competitions_status", "competitions", ["status"])

    # 2. Competition Questions
    op.create_table(
        "competition_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "competition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct_option", sa.String(length=5), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_competition_questions_comp_id", "competition_questions", ["competition_id"])

    # 3. Participants
    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("membership_id", sa.String(length=100), nullable=False),
        sa.Column("language_code", sa.String(length=10), nullable=False, server_default="en"),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_participants_telegram_user_id", "participants", ["telegram_user_id"], unique=True)
    op.create_index("ix_participants_membership_id", "participants", ["membership_id"], unique=True)

    # 4. Exam Attempts
    op.create_table(
        "exam_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "competition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("incorrect_count", sa.Integer(), nullable=True),
        sa.Column("completion_seconds", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.UniqueConstraint("competition_id", "participant_id", name="uq_competition_participant_attempt"),
    )
    op.create_index("ix_exam_attempts_comp_id", "exam_attempts", ["competition_id"])
    op.create_index("ix_exam_attempts_part_id", "exam_attempts", ["participant_id"])
    op.create_index("ix_exam_attempts_status", "exam_attempts", ["status"])
    op.create_index("ix_exam_attempts_deadline", "exam_attempts", ["deadline_at"])
    op.create_index("ix_exam_attempts_rank", "exam_attempts", ["rank"])

    # 5. Attempt Question Order
    op.create_table(
        "attempt_question_order",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competition_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("option_mapping", sa.JSON(), nullable=False),
        sa.UniqueConstraint("attempt_id", "display_order", name="uq_attempt_display_order"),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),
    )
    op.create_index("ix_attempt_question_order_attempt_id", "attempt_question_order", ["attempt_id"])

    # 6. Participant Answers
    op.create_table(
        "participant_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competition_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selected_display_option", sa.String(length=5), nullable=False),
        sa.Column("resolved_canonical_option", sa.String(length=5), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question_answer"),
    )
    op.create_index("ix_participant_answers_attempt_id", "participant_answers", ["attempt_id"])

    # 7. Announcements
    op.create_table(
        "announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "competition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 8. Audit Logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("announcements")
    op.drop_table("participant_answers")
    op.drop_table("attempt_question_order")
    op.drop_table("exam_attempts")
    op.drop_table("participants")
    op.drop_table("competition_questions")
    op.drop_table("competitions")
