import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.membership_service import (
    ParticipantService,
    MockMembershipVerificationService,
    InvalidMembershipFormatError,
    MembershipNotFoundError,
    MembershipAlreadyBoundError,
    TelegramAccountAlreadyBoundError,
)


@pytest.mark.asyncio
async def test_format_validation():
    # Valid formats
    assert ParticipantService.validate_format("EMYC/4055828/2026") == "EMYC/4055828/2026"
    assert ParticipantService.validate_format("emyc/1234567/2025") == "EMYC/1234567/2025"

    # Invalid formats
    with pytest.raises(InvalidMembershipFormatError):
        ParticipantService.validate_format("INVALID_MEMBERSHIP")

    with pytest.raises(InvalidMembershipFormatError):
        ParticipantService.validate_format("EMYC/123/2026")  # too short digit block

    with pytest.raises(InvalidMembershipFormatError):
        ParticipantService.validate_format("EMYC/4055828/26")  # 2-digit year


@pytest.mark.asyncio
async def test_membership_binding_and_duplicate_prevention(db_session: AsyncSession):
    verifier = MockMembershipVerificationService()

    # 1. Successful first binding
    p1 = await ParticipantService.register_or_bind_participant(
        db_session,
        telegram_user_id=11111,
        membership_id="EMYC/4055828/2026",
        telegram_username="user_one",
        language_code="en",
        verifier=verifier,
    )
    assert p1.telegram_user_id == 11111
    assert p1.membership_id == "EMYC/4055828/2026"

    # 2. Idempotent re-bind with identical credentials
    p1_again = await ParticipantService.register_or_bind_participant(
        db_session,
        telegram_user_id=11111,
        membership_id="EMYC/4055828/2026",
        telegram_username="user_one",
        verifier=verifier,
    )
    assert p1_again.id == p1.id

    # 3. Prevent duplicate binding: Different Telegram user trying to bind the same membership
    with pytest.raises(MembershipAlreadyBoundError):
        await ParticipantService.register_or_bind_participant(
            db_session,
            telegram_user_id=22222,
            membership_id="EMYC/4055828/2026",
            telegram_username="user_two",
            verifier=verifier,
        )

    # 4. Prevent duplicate binding: Same Telegram user trying to bind a different membership
    with pytest.raises(TelegramAccountAlreadyBoundError):
        await ParticipantService.register_or_bind_participant(
            db_session,
            telegram_user_id=11111,
            membership_id="EMYC/7777777/2026",
            telegram_username="user_one",
            verifier=verifier,
        )

    # 5. Revoked / invalid membership rejection
    with pytest.raises(MembershipNotFoundError):
        await ParticipantService.register_or_bind_participant(
            db_session,
            telegram_user_id=33333,
            membership_id="EMYC/0000000/2026",  # in revoked_memberships
            verifier=verifier,
        )


@pytest.mark.asyncio
async def test_update_language(db_session: AsyncSession):
    verifier = MockMembershipVerificationService()
    p = await ParticipantService.register_or_bind_participant(
        db_session,
        telegram_user_id=44444,
        membership_id="EMYC/4444444/2026",
        language_code="en",
        verifier=verifier,
    )
    assert p.language_code == "en"

    # Update to Amharic
    updated = await ParticipantService.update_language(db_session, 44444, "am")
    assert updated.language_code == "am"

    # Update to Afaan Oromoo
    updated = await ParticipantService.update_language(db_session, 44444, "om")
    assert updated.language_code == "om"
