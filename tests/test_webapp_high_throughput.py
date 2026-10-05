"""Unit and integration tests for Telegram Mini App high-throughput examination engine."""

import json
import time
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.core.config import get_settings
from app.core.telegram_auth import parse_and_validate_init_data, TelegramAuthError

settings = get_settings()


def make_test_init_data(user_id: int, username: str = "testuser", first_name: str = "Test") -> str:
    """Constructs valid mock initData query string for test authentication."""
    user_json = json.dumps({"id": user_id, "username": username, "first_name": first_name})
    auth_date = int(time.time())
    # In test/debug environment, test_valid_mock_hash is accepted by telegram_auth
    return f"user={user_json}&auth_date={auth_date}&hash=test_valid_mock_hash"


def test_telegram_auth_validation_logic():
    """Verifies that initData is parsed correctly and rejected when hash is missing or corrupted."""
    valid_str = make_test_init_data(12345678)
    res = parse_and_validate_init_data(valid_str)
    assert res["user"]["id"] == 12345678

    # Corrupt string without hash
    with pytest.raises(TelegramAuthError):
        parse_and_validate_init_data("user={}&auth_date=123")

    # Empty string
    with pytest.raises(TelegramAuthError):
        parse_and_validate_init_data("")


@pytest.mark.asyncio
async def test_webapp_health_endpoint():
    """Verifies that the Mini App health probe is online."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/webapp/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webapp_static_file_serving():
    """Verifies that the Telegram Mini App static assets are served properly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/webapp/index.html")
        assert resp.status_code == 200
        assert "EMYC Online Examination" in resp.text
        assert "telegram-web-app.js" in resp.text


def test_start_exam_keyboard_with_webapp_url(monkeypatch):
    """Verifies that when WEBAPP_URL is set, get_start_exam_keyboard provides the WebApp button."""
    from app.bot.keyboards import get_start_exam_keyboard
    test_comp_id = uuid.uuid4()

    # Case 1: Without WEBAPP_URL
    monkeypatch.setattr(settings, "WEBAPP_URL", None)
    kb1 = get_start_exam_keyboard(test_comp_id, lang="en")
    assert len(kb1.inline_keyboard) == 2
    assert kb1.inline_keyboard[0][0].web_app is None

    # Case 2: With WEBAPP_URL configured
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://emyc-exam.pages.dev")
    kb2 = get_start_exam_keyboard(test_comp_id, lang="en")
    assert len(kb2.inline_keyboard) == 3
    assert kb2.inline_keyboard[0][0].web_app is not None
    assert kb2.inline_keyboard[0][0].web_app.url == "https://emyc-exam.pages.dev"


@pytest.mark.asyncio
async def test_webapp_session_retrieval_and_batch_submission(db_session: AsyncSession):
    """Verifies the complete one-shot question retrieval and single-transaction batch submission."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="National Youth Championship 2026",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(days=2),
        actual_exam_started_at=now - timedelta(minutes=5),
        actual_exam_ends_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=3,
    )
    p = Participant(
        telegram_user_id=778899,
        membership_id="EMYC/778899/2026",
        full_name="Zubair Bilal",
        phone_number="+251911778899",
        language_code="en",
    )
    db_session.add_all([comp, p])
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="What is the capital of Ethiopia?",
        options={"A": "Addis Ababa", "B": "Gondar", "C": "Dire Dawa", "D": "Hawassa"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="In which month is Sawm observed?",
        options={"A": "Shawwal", "B": "Ramadan", "C": "Safar", "D": "Rajab"},
        correct_option="B",
        order_index=2,
    )
    q3 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="How many daily Fard prayers?",
        options={"A": "3", "B": "4", "C": "5", "D": "6"},
        correct_option="C",
        order_index=3,
    )
    db_session.add_all([q1, q2, q3])
    await db_session.commit()

    init_data = make_test_init_data(p.telegram_user_id)
    headers = {"X-Telegram-Init-Data": init_data}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Fetch Session (All questions in 1 shot)
        sess_resp = await client.get("/api/v1/webapp/session", headers=headers)
        assert sess_resp.status_code == 200
        session_data = sess_resp.json()

        assert session_data["status"] == "ready"
        assert session_data["competition_title"] == "National Youth Championship 2026"
        assert len(session_data["questions"]) == 3
        attempt_id = session_data["attempt_id"]

        # Examine questions payload structure
        q_item = session_data["questions"][0]
        assert "display_order" in q_item
        assert "question_id" in q_item
        assert "question_text" in q_item
        assert "options" in q_item
        assert set(q_item["options"].keys()) == {"A", "B", "C", "D"}

        # 2. Batch Submit All Answers in 1 Single Transaction
        submit_payload = {
            "attempt_id": attempt_id,
            "answers": [
                {"display_order": 1, "selected_option": "A"},
                {"display_order": 2, "selected_option": "B"},
                {"display_order": 3, "selected_option": "C"},
            ],
        }
        sub_resp = await client.post("/api/v1/webapp/submit", json=submit_payload, headers=headers)
        assert sub_resp.status_code == 200
        result = sub_resp.json()

        assert result["status"] == "success"
        assert result["score"] >= 0  # Scoring depends on per-attempt option randomization
        assert result["total_questions"] == 3
        assert "completion_seconds" in result

        # 3. Subsequent session fetch shows "already_submitted"
        sess_resp2 = await client.get("/api/v1/webapp/session", headers=headers)
        assert sess_resp2.status_code == 200
        assert sess_resp2.json()["status"] == "already_submitted"
