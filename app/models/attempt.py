import enum
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    JSON,
    Enum,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    EXPIRED = "EXPIRED"
    FINALIZED = "FINALIZED"


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("participants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, native_enum=False, length=30),
        default=AttemptStatus.IN_PROGRESS,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # deadline = min(started_at + duration, competition.closes_at)
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Server-calculated scoring & ranking metrics
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    correct_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    incorrect_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # High-concurrency batch storage (stores full 100-answer submission in 1 write)
    answers_summary: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)
    question_sequence: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # 1 official attempt per participant per competition
        UniqueConstraint(
            "competition_id", "participant_id", name="uq_competition_participant_attempt"
        ),
        # High-concurrency composite indexes
        Index("ix_exam_attempts_comp_status", "competition_id", "status"),
        Index("ix_exam_attempts_status_deadline", "status", "deadline_at"),
        Index("ix_exam_attempts_comp_score_time", "competition_id", "score", "completion_seconds"),
    )

    # Relationships
    competition: Mapped["Competition"] = relationship(
        "Competition", back_populates="attempts"
    )
    participant: Mapped["Participant"] = relationship(
        "Participant", back_populates="attempts"
    )
    question_orders: Mapped[List["AttemptQuestionOrder"]] = relationship(
        "AttemptQuestionOrder",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="AttemptQuestionOrder.display_order",
    )
    answers: Mapped[List["ParticipantAnswer"]] = relationship(
        "ParticipantAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class AttemptQuestionOrder(Base):
    __tablename__ = "attempt_question_order"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exam_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competition_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Stored mapping: {"A": "canonical_option", "B": "canonical_option", ...}
    option_mapping: Mapped[Dict[str, str]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("attempt_id", "display_order", name="uq_attempt_display_order"),
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),
    )

    attempt: Mapped["ExamAttempt"] = relationship(
        "ExamAttempt", back_populates="question_orders"
    )
    question: Mapped["CompetitionQuestion"] = relationship("CompetitionQuestion")


class ParticipantAnswer(Base):
    __tablename__ = "participant_answers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exam_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competition_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    selected_display_option: Mapped[str] = mapped_column(String(5), nullable=False)
    resolved_canonical_option: Mapped[str] = mapped_column(String(5), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        # Idempotency constraint: 1 answer per question per attempt
        UniqueConstraint(
            "attempt_id", "question_id", name="uq_attempt_question_answer"
        ),
    )

    attempt: Mapped["ExamAttempt"] = relationship(
        "ExamAttempt", back_populates="answers"
    )
    question: Mapped["CompetitionQuestion"] = relationship("CompetitionQuestion")
