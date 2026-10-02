import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class MembershipVerifyRequest(BaseModel):
    membership_id: str = Field(..., description="Membership ID (e.g. EMYC/4055828/2026)")
    telegram_user_id: int
    telegram_username: Optional[str] = None
    language_code: str = "en"


class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    telegram_user_id: int
    telegram_username: Optional[str] = None
    membership_id: str
    language_code: str
    registered_at: datetime
    is_active: bool
