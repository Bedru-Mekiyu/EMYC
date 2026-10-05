import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, Text, DateTime, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class CompetitionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    OPEN = "OPEN"
    LIVE = "LIVE"
    CLOSED = "CLOSED"
    RESULTS_FINALIZED = "RESULTS_FINALIZED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[CompetitionStatus] = mapped_column(
        Enum(CompetitionStatus, native_enum=False, length=30),
        default=CompetitionStatus.DRAFT,
        nullable=False,
        index=True,
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    actual_exam_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_exam_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    questions: Mapped[List["CompetitionQuestion"]] = relationship(
        "CompetitionQuestion",
        back_populates="competition",
        cascade="all, delete-orphan",
        order_by="CompetitionQuestion.order_index",
    )
    attempts: Mapped[List["ExamAttempt"]] = relationship(
        "ExamAttempt",
        back_populates="competition",
        cascade="all, delete-orphan",
    )
