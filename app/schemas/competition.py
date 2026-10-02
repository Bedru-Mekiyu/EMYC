import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from app.models.competition import CompetitionStatus


class CompetitionBase(BaseModel):
    title: str
    description: Optional[str] = None
    opens_at: datetime
    closes_at: datetime
    duration_minutes: int = 120
    question_count: int = 100


class CompetitionCreate(CompetitionBase):
    pass


class CompetitionResponse(CompetitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CompetitionStatus
    created_at: datetime
    updated_at: datetime


class CompetitionStatusStats(BaseModel):
    competition_id: uuid.UUID
    title: str
    status: CompetitionStatus
    total_participants: int
    started_count: int
    submitted_count: int
    in_progress_count: int
    not_started_count: int
