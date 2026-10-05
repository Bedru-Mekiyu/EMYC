import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, JSON, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class CompetitionQuestion(Base):
    __tablename__ = "competition_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    competition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored as JSON: {"A": "Option text A", "B": "Option text B", "C": "...", "D": "..."}
    options: Mapped[Dict[str, str]] = mapped_column(JSON, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(5), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    competition: Mapped["Competition"] = relationship(
        "Competition", back_populates="questions"
    )
