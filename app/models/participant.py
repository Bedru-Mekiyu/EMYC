import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, BigInteger, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Unique Telegram User ID (Primary Telegram Identity)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    telegram_username: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    # Strict 1-to-1 unique binding between Membership ID and Telegram account
    membership_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    language_code: Mapped[str] = mapped_column(
        String(10), default="en", nullable=False
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    attempts: Mapped[List["ExamAttempt"]] = relationship(
        "ExamAttempt", back_populates="participant", cascade="all, delete-orphan"
    )
