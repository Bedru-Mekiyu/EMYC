import uuid
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.competition_service import CompetitionService
from app.services.membership_service import ParticipantService
from app.services.webapp_exam_service import WebAppExamService, UnauthorizedAttemptAccessError
from app.bot.handlers.admin import resolve_target_competition

settings = get_settings()
ADMIN_ID = 999999999
CANDIDATE_ID = 123456789


@pytest.fixture(autouse=True)
def configure_admin_id(monkeypatch):
    """Ensures ADMIN_ID is recognized as authoritative administrator in test environment."""
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_IDS", str(ADMIN_ID))


@pytest_asyncio.fixture
async def setup_live_competition(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Official EMYC Championship",
        description="Live competitive examination",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=2,
    )
    db_session.add(comp)
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="First pillar of Islam?",
        options={"A": "Shahadah", "B": "Salah", "C": "Zakah", "D": "Sawm"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Capital of Ethiopia?",
        options={"A": "Addis Ababa", "B": "Hawassa", "C": "Dire Dawa", "D": "Bahir Dar"},
        correct_option="A",
        order_index=2,
    )
    db_session.add_all([q1, q2])
    await db_session.commit()
    await db_session.refresh(comp)
    return comp


@pytest.mark.asyncio
async def test_admin_cannot_register_or_bind_as_participant(db_session: AsyncSession):
    """Admin telegram accounts cannot register or bind as exam participants."""
    with pytest.raises(ValueError, match="Administrators cannot be registered as exam participants"):
        await ParticipantService.register_or_bind_participant(
            db=db_session,
            telegram_user_id=ADMIN_ID,
            membership_id="EMYC/77777/2026",
            full_name="Admin User",
            phone_number="+251911000000",
        )

    # Verify no participant was inserted for the admin
    stmt = select(Participant).where(Participant.telegram_user_id == ADMIN_ID)
    res = (await db_session.execute(stmt)).scalar_one_or_none()
    assert res is None


@pytest.mark.asyncio
async def test_admin_cannot_access_session_or_create_attempt(
    db_session: AsyncSession, setup_live_competition: Competition
):
    """Admin opening Mini App session receives admin_restricted and zero attempts/participants created."""
    comp = setup_live_competition

    session_res = await WebAppExamService.get_or_create_session(
        db=db_session,
        telegram_user_id=ADMIN_ID,
        comp_id=str(comp.id),
    )

    assert session_res["status"] == "admin_restricted"
    assert "authorized for administration" in session_res["message"]

    # Verify 0 participant records and 0 attempts for admin
    p_stmt = select(Participant).where(Participant.telegram_user_id == ADMIN_ID)
    assert (await db_session.execute(p_stmt)).scalar_one_or_none() is None

    att_stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
    attempts = (await db_session.execute(att_stmt)).scalars().all()
    assert len(attempts) == 0


@pytest.mark.asyncio
async def test_admin_cannot_submit_batch_answers(
    db_session: AsyncSession, setup_live_competition: Competition
):
    """Admin accounts are rejected from submitting batch answers."""
    dummy_attempt_id = uuid.uuid4()
    with pytest.raises(UnauthorizedAttemptAccessError, match="Administrators cannot submit exam attempts"):
        await WebAppExamService.submit_batch_answers(
            db=db_session,
            telegram_user_id=ADMIN_ID,
            attempt_id=dummy_attempt_id,
            answers=[{"display_order": 1, "selected_option": "A"}],
        )


@pytest.mark.asyncio
async def test_admin_web_login_rejected_with_403(client: AsyncClient):
    """Calling /api/v1/webapp/auth/web-login with admin telegram user ID returns HTTP 403."""
    res = await client.post(
        "/api/v1/webapp/auth/web-login",
        json={
            "membership_id": "EMYC/99999/2026",
            "telegram_user_id": ADMIN_ID,
        },
    )
    assert res.status_code == 403
    data = res.json()
    assert "authorized for administration" in data["detail"]


@pytest.mark.asyncio
async def test_admin_purged_from_participant_and_attempt_tables(
    db_session: AsyncSession, setup_live_competition: Competition
):
    """Purge routine clears any admin entries if they existed previously."""
    comp = setup_live_competition
    now = datetime.now(timezone.utc)

    # Manually insert admin participant and attempt bypassing service guards
    admin_p = Participant(
        telegram_user_id=ADMIN_ID,
        membership_id="EMYC/99999/2026",
        full_name="Admin Test",
    )
    db_session.add(admin_p)
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=admin_p.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=20),
        score=2,
    )
    db_session.add(att)
    await db_session.commit()

    # Run purge
    purged_count = await ParticipantService.purge_admin_participant_records(db_session, [ADMIN_ID])
    assert purged_count >= 1

    # Verify cleaned
    p_check = await db_session.get(Participant, admin_p.id)
    assert p_check is None
    att_check = await db_session.get(ExamAttempt, att.id)
    assert att_check is None


@pytest.mark.asyncio
async def test_single_live_competition_invariant(db_session: AsyncSession):
    """Setting competition B to LIVE automatically sweeps competition A to CLOSED."""
    now = datetime.now(timezone.utc)
    comp_a = await CompetitionService.create_competition(
        db=db_session,
        title="Competition Alpha",
        description="First comp",
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=1,
        status=CompetitionStatus.OPEN,
    )
    q_a = CompetitionQuestion(
        competition_id=comp_a.id,
        question_text="Question A?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q_a)

    comp_b = await CompetitionService.create_competition(
        db=db_session,
        title="Competition Beta",
        description="Second comp",
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=1,
        status=CompetitionStatus.OPEN,
    )
    q_b = CompetitionQuestion(
        competition_id=comp_b.id,
        question_text="Question B?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="B",
        order_index=1,
    )
    db_session.add(q_b)
    await db_session.commit()

    # Set Comp A to LIVE
    comp_a = await CompetitionService.update_status(db_session, comp_a.id, CompetitionStatus.LIVE, admin_id="1")
    assert comp_a.status == CompetitionStatus.LIVE

    # Set Comp B to LIVE
    comp_b = await CompetitionService.update_status(db_session, comp_b.id, CompetitionStatus.LIVE, admin_id="1")
    assert comp_b.status == CompetitionStatus.LIVE

    # Refresh Comp A: must be CLOSED
    await db_session.refresh(comp_a)
    assert comp_a.status == CompetitionStatus.CLOSED

    # Query all LIVE competitions: exactly 1
    stmt = select(Competition).where(Competition.status == CompetitionStatus.LIVE)
    live_comps = (await db_session.execute(stmt)).scalars().all()
    assert len(live_comps) == 1
    assert live_comps[0].id == comp_b.id


@pytest.mark.asyncio
async def test_resolve_target_competition_priority(db_session: AsyncSession):
    """Authoritative competition resolution prioritizes LIVE competition over others."""
    now = datetime.now(timezone.utc)
    old_comp = await CompetitionService.create_competition(
        db=db_session,
        title="Old Competition",
        description="Archived comp",
        opens_at=now - timedelta(days=2),
        closes_at=now - timedelta(days=1),
        duration_minutes=30,
        question_count=5,
        status=CompetitionStatus.CLOSED,
    )
    live_comp = await CompetitionService.create_competition(
        db=db_session,
        title="Active Live Competition",
        description="Live comp",
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=5,
        status=CompetitionStatus.LIVE,
    )
    draft_comp = await CompetitionService.create_competition(
        db=db_session,
        title="New Draft Competition",
        description="Draft created after live",
        opens_at=now + timedelta(days=5),
        closes_at=now + timedelta(days=6),
        duration_minutes=30,
        question_count=5,
        status=CompetitionStatus.DRAFT,
    )

    # Without comp_id specified: MUST resolve LIVE competition, NOT draft (even though draft was created after)
    resolved = await resolve_target_competition(db_session)
    assert resolved is not None
    assert resolved.id == live_comp.id

    # With explicit comp_id specified: resolves that specific competition
    resolved_old = await resolve_target_competition(db_session, comp_id=old_comp.id)
    assert resolved_old is not None
    assert resolved_old.id == old_comp.id


@pytest.mark.asyncio
async def test_candidate_attempt_increments_metrics_while_admin_excluded(
    db_session: AsyncSession, setup_live_competition: Competition
):
    """Candidate attempt increments database counter; admin is excluded from candidate count."""
    comp = setup_live_competition

    # 1. Register candidate participant
    cand = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=CANDIDATE_ID,
        membership_id="EMYC/10001/2026",
        full_name="Amina Mohammed",
    )

    # Total registered count excluding admins
    total_reg = await ParticipantService.get_registered_participants_count(db_session, exclude_admins=True)
    assert total_reg == 1

    # 2. Candidate creates session
    cand_session = await WebAppExamService.get_or_create_session(
        db=db_session,
        telegram_user_id=CANDIDATE_ID,
        comp_id=str(comp.id),
    )
    assert cand_session["status"] == "ready"
    attempt_id = uuid.UUID(cand_session["attempt_id"])

    # 3. Candidate submits answers
    submit_res = await WebAppExamService.submit_batch_answers(
        db=db_session,
        telegram_user_id=CANDIDATE_ID,
        attempt_id=attempt_id,
        answers=[
            {"display_order": 1, "selected_option": "A"},
            {"display_order": 2, "selected_option": "A"},
        ],
    )
    assert submit_res["status"] in ["success", "submitted"]
    assert "score" in submit_res

    # 4. Check aggregate metrics for competition
    admin_ids = settings.admin_ids
    admin_p_sub = select(Participant.id).where(Participant.telegram_user_id.in_(admin_ids)) if admin_ids else None
    attempts_stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
    if admin_p_sub is not None:
        attempts_stmt = attempts_stmt.where(ExamAttempt.participant_id.notin_(admin_p_sub))

    attempts = (await db_session.execute(attempts_stmt)).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.SUBMITTED
    assert attempts[0].score is not None
    assert attempts[0].score >= 0
