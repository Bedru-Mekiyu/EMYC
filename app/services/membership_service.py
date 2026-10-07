import re
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
import httpx

from app.models.participant import Participant
from app.core.config import get_settings
from app.core.logging import logger, log_audit_event

settings = get_settings()


class MembershipError(Exception):
    """Base membership domain exception."""
    pass


class InvalidMembershipFormatError(MembershipError):
    pass


class MembershipNotFoundError(MembershipError):
    pass


class MembershipAlreadyBoundError(MembershipError):
    pass


class TelegramAccountAlreadyBoundError(MembershipError):
    pass


class MembershipVerificationService(ABC):
    """Abstract interface for verifying participant membership against external registries."""

    @abstractmethod
    async def verify_membership(self, membership_id: str) -> bool:
        """Verifies if a membership ID is valid and active."""
        pass


class MockMembershipVerificationService(MembershipVerificationService):
    """Mock verification service for development and testing.
    
    Considers any ID matching pattern valid, unless specifically blacklisted.
    """
    def __init__(self, regex_pattern: Optional[str] = None):
        self.regex = re.compile(regex_pattern or settings.MEMBERSHIP_REGEX, re.IGNORECASE)
        # Mock set of deactivated/banned membership IDs
        self.revoked_memberships = {"EMYC/0000000/2026", "EMYC/BANNED/2026"}

    async def verify_membership(self, membership_id: str) -> bool:
        membership_clean = membership_id.strip()
        if not self.regex.match(membership_clean):
            return False
        if membership_clean in self.revoked_memberships:
            return False
        return True


class HttpMembershipVerificationService(MembershipVerificationService):
    """Production verification adapter connecting to an external membership API."""

    def __init__(self, api_url: str, api_key: Optional[str] = None):
        self.api_url = api_url
        self.api_key = api_key

    async def verify_membership(self, membership_id: str) -> bool:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.api_url,
                    json={"membership_id": membership_id},
                    headers=headers,
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("is_valid", False)
                return False
        except Exception as e:
            logger.error(f"External membership verification request failed: {e}")
            return False


def get_membership_service() -> MembershipVerificationService:
    """Factory creating configured membership verification adapter."""
    if settings.MEMBERSHIP_ADAPTER_TYPE == "http" and settings.MEMBERSHIP_API_URL:
        return HttpMembershipVerificationService(
            api_url=settings.MEMBERSHIP_API_URL,
            api_key=settings.MEMBERSHIP_API_KEY,
        )
    return MockMembershipVerificationService()


class ParticipantService:
    @staticmethod
    def validate_format(membership_id: str) -> str:
        """Validates format of membership ID string without leaking internal regex rules.
        Automatically normalizes common human formatting variations in mock/dev mode.
        """
        cleaned = membership_id.strip().upper().replace(" ", "").replace("-", "/")
        if re.match(settings.MEMBERSHIP_REGEX, cleaned):
            return cleaned

        # In mock adapter or non-strict mode, accept common variations (e.g. 12345 or EMYC12345)
        if settings.MEMBERSHIP_ADAPTER_TYPE == "mock":
            digits_match = re.search(r"(\d{4,10})", cleaned)
            if digits_match:
                digits = digits_match.group(1)
                normalized = f"EMYC/{digits.zfill(7)}/2026"
                if re.match(settings.MEMBERSHIP_REGEX, normalized):
                    return normalized

        raise InvalidMembershipFormatError(
            "Membership verification could not be completed. Please check your official membership ID and try again."
        )

    @staticmethod
    async def get_participant_by_telegram_id(
        db: AsyncSession, telegram_user_id: int
    ) -> Optional[Participant]:
        """Looks up a registered participant by primary Telegram User ID."""
        stmt = select(Participant).where(Participant.telegram_user_id == telegram_user_id)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def get_registered_participants_count(db: AsyncSession, exclude_admins: bool = True) -> int:
        """Returns total count of registered participants, excluding administrative accounts by default."""
        stmt = select(func.count(Participant.id))
        if exclude_admins:
            admin_ids = settings.admin_ids
            if admin_ids:
                stmt = stmt.where(Participant.telegram_user_id.notin_(admin_ids))
        res = await db.execute(stmt)
        return res.scalar() or 0

    @staticmethod
    async def get_registered_participant_user_ids(db: AsyncSession, exclude_admins: bool = True) -> List[int]:
        """Returns unique Telegram user IDs of all registered participants, excluding admins by default."""
        stmt = select(Participant.telegram_user_id).distinct()
        if exclude_admins:
            admin_ids = settings.admin_ids
            if admin_ids:
                stmt = stmt.where(Participant.telegram_user_id.notin_(admin_ids))
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def update_language(
        db: AsyncSession, telegram_user_id: int, language_code: str
    ) -> Optional[Participant]:
        """Updates preferred language for a Telegram user."""
        participant = await ParticipantService.get_participant_by_telegram_id(db, telegram_user_id)
        if participant:
            participant.language_code = language_code
            await db.commit()
            await db.refresh(participant)
            log_audit_event("LANGUAGE_UPDATED", "PARTICIPANT", str(telegram_user_id), {
                "language_code": language_code
            })
        return participant

    @staticmethod
    async def verify_membership_id(
        db: AsyncSession,
        telegram_user_id: int,
        membership_id: str,
        verifier: Optional[MembershipVerificationService] = None,
    ) -> str:
        """Verifies membership format and external validity, ensuring no account binding conflicts."""
        # 1. Format validation
        cleaned_membership = ParticipantService.validate_format(membership_id)

        # 2. Check if this membership is already bound to another Telegram account
        mem_stmt = select(Participant).where(Participant.membership_id == cleaned_membership)
        res_mem = await db.execute(mem_stmt)
        existing_mem_owner = res_mem.scalar_one_or_none()

        if existing_mem_owner and existing_mem_owner.telegram_user_id != telegram_user_id:
            raise MembershipAlreadyBoundError(
                "This Membership ID is already bound to a different Telegram account"
            )

        # 3. Check if this Telegram account is already bound to a different membership ID
        tg_stmt = select(Participant).where(Participant.telegram_user_id == telegram_user_id)
        res_tg = await db.execute(tg_stmt)
        existing_tg_user = res_tg.scalar_one_or_none()

        if existing_tg_user and existing_tg_user.membership_id != cleaned_membership:
            raise TelegramAccountAlreadyBoundError(
                "This Telegram account is already linked to a verified EMYC membership."
            )

        # 4. External membership verification
        verifier = verifier or get_membership_service()
        is_valid = await verifier.verify_membership(cleaned_membership)
        if not is_valid:
            raise MembershipNotFoundError("Membership ID could not be verified in the registry")

        return cleaned_membership

    @staticmethod
    async def register_or_bind_participant(
        db: AsyncSession,
        telegram_user_id: int,
        membership_id: str,
        telegram_username: Optional[str] = None,
        language_code: str = "en",
        verifier: Optional[MembershipVerificationService] = None,
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> Participant:
        """Verifies membership and binds/persists participant in a single database transaction."""
        cleaned_membership = await ParticipantService.verify_membership_id(
            db=db,
            telegram_user_id=telegram_user_id,
            membership_id=membership_id,
            verifier=verifier,
        )

        tg_stmt = select(Participant).where(Participant.telegram_user_id == telegram_user_id)
        res_tg = await db.execute(tg_stmt)
        existing_tg_user = res_tg.scalar_one_or_none()

        if existing_tg_user:
            # Already bound to the same membership ID - update profile fields if provided
            updated = False
            if full_name and existing_tg_user.full_name != full_name:
                existing_tg_user.full_name = full_name
                updated = True
            if phone_number and existing_tg_user.phone_number != phone_number:
                existing_tg_user.phone_number = phone_number
                updated = True
            if telegram_username and existing_tg_user.telegram_username != telegram_username:
                existing_tg_user.telegram_username = telegram_username
                updated = True
            if updated:
                await db.commit()
                await db.refresh(existing_tg_user)
            return existing_tg_user

        # Persist participant binding
        participant = Participant(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            membership_id=cleaned_membership,
            language_code=language_code,
            full_name=full_name,
            phone_number=phone_number,
        )
        db.add(participant)
        try:
            await db.commit()
            await db.refresh(participant)
        except IntegrityError as e:
            await db.rollback()
            raise MembershipAlreadyBoundError(f"Binding rejected due to constraint violation: {e}")

        # Mask sensitive phone number in audit log
        masked_phone = f"...{phone_number[-4:]}" if phone_number and len(phone_number) >= 4 else None
        log_audit_event("MEMBERSHIP_BOUND", "PARTICIPANT", str(telegram_user_id), {
            "membership_id": cleaned_membership,
            "telegram_username": telegram_username,
            "has_full_name": full_name is not None,
            "phone_masked": masked_phone,
        })
        return participant
