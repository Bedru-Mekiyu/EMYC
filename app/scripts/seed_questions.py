"""Question and competition seeding script for development and testing."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion


SAMPLE_QUESTIONS = [
    {
        "question_text": "What is the capital city of Ethiopia?",
        "options": {
            "A": "Addis Ababa",
            "B": "Dire Dawa",
            "C": "Hawassa",
            "D": "Mekelle",
        },
        "correct_option": "A",
    },
    {
        "question_text": "Which river is the longest in Africa?",
        "options": {
            "A": "Congo",
            "B": "Nile",
            "C": "Niger",
            "D": "Zambezi",
        },
        "correct_option": "B",
    },
    {
        "question_text": "What is the official currency of Ethiopia?",
        "options": {
            "A": "Shilling",
            "B": "Dinar",
            "C": "Birr",
            "D": "Franc",
        },
        "correct_option": "C",
    },
    {
        "question_text": "In computing, what does 'API' stand for?",
        "options": {
            "A": "Application Programming Interface",
            "B": "Advanced Protocol Integration",
            "C": "Automated Program Instruction",
            "D": "Applied Process Interface",
        },
        "correct_option": "A",
    },
    {
        "question_text": "Which planetary body is closest to the Sun?",
        "options": {
            "A": "Venus",
            "B": "Mars",
            "C": "Mercury",
            "D": "Earth",
        },
        "correct_option": "C",
    },
]


async def seed_sample_competition(
    session: AsyncSession,
    title: str = "EMYC Official Knowledge Championship 2026",
    duration_minutes: int = 60,
    status: CompetitionStatus = CompetitionStatus.LIVE,
) -> Competition:
    now = datetime.now(timezone.utc)
    competition = Competition(
        id=uuid.uuid4(),
        title=title,
        description="Official sample competition for testing and verification.",
        status=status,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=23),
        duration_minutes=duration_minutes,
        question_count=len(SAMPLE_QUESTIONS),
    )
    session.add(competition)
    await session.flush()

    for idx, q_data in enumerate(SAMPLE_QUESTIONS, start=1):
        q = CompetitionQuestion(
            id=uuid.uuid4(),
            competition_id=competition.id,
            question_text=q_data["question_text"],
            options=q_data["options"],
            correct_option=q_data["correct_option"],
            order_index=idx,
        )
        session.add(q)

    await session.commit()
    await session.refresh(competition)
    return competition


async def main():
    async with AsyncSessionLocal() as session:
        comp = await seed_sample_competition(session)
        print(f"Successfully seeded competition: {comp.title} (ID: {comp.id}) with {len(SAMPLE_QUESTIONS)} questions.")


if __name__ == "__main__":
    asyncio.run(main())
