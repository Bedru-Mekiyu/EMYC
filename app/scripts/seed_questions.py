"""Question and competition seeding script for EMYC platform."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion


SAMPLE_QUESTIONS = [
    {
        "question_text": "In Islamic history, what was the first Hijrah destination for early Muslims fleeing persecution in Makkah?",
        "options": {"A": "Abyssinia (Ethiopia)", "B": "Yemen", "C": "Ta'if", "D": "Madinah"},
        "correct_option": "A",
    },
    {
        "question_text": "Who was the just and righteous King of Abyssinia (Al-Najashi) who sheltered the Prophet Muhammad's (pbuh) companions?",
        "options": {"A": "Armah", "B": "Ezana", "C": "Kaleb", "D": "Menelik"},
        "correct_option": "A",
    },
    {
        "question_text": "Which Surah of the Holy Quran was recited by Ja'far ibn Abi Talib before the King of Abyssinia?",
        "options": {"A": "Surah Maryam", "B": "Surah Al-Kahf", "C": "Surah Ya-Sin", "D": "Surah Al-Baqarah"},
        "correct_option": "A",
    },
    {
        "question_text": "Who was the companion of the Prophet Muhammad (pbuh) known as the first Mu'adhin in Islam, of Ethiopian heritage?",
        "options": {"A": "Bilal ibn Rabah", "B": "Salman Al-Farsi", "C": "Ammar ibn Yasir", "D": "Abu Dharr Al-Ghifari"},
        "correct_option": "A",
    },
    {
        "question_text": "What is the primary objective of the Ethiopian Muslim Youth Council (EMYC)?",
        "options": {
            "A": "Empowering Muslim youth through education, ethics, and unity",
            "B": "Commercial trading",
            "C": "Political campaigning",
            "D": "Athletic sponsorships",
        },
        "correct_option": "A",
    },
    {
        "question_text": "How many daily obligatory prayers (Fard Salah) are prescribed for Muslims?",
        "options": {"A": "3", "B": "5", "C": "7", "D": "4"},
        "correct_option": "B",
    },
    {
        "question_text": "In which Islamic month is fasting (Sawm) obligatory for all eligible Muslims?",
        "options": {"A": "Rajab", "B": "Sha'ban", "C": "Ramadan", "D": "Dhul-Hijjah"},
        "correct_option": "C",
    },
    {
        "question_text": "What is the longest Surah (chapter) in the Holy Quran?",
        "options": {"A": "Surah Ali 'Imran", "B": "Surah Al-Baqarah", "C": "Surah An-Nisa", "D": "Surah Al-A'raf"},
        "correct_option": "B",
    },
    {
        "question_text": "What is the shortest Surah (chapter) in the Holy Quran?",
        "options": {"A": "Surah Al-Ikhlas", "B": "Surah An-Nasr", "C": "Surah Al-Kawthar", "D": "Surah Al-Asr"},
        "correct_option": "C",
    },
    {
        "question_text": "In which blessed city was Prophet Muhammad (peace be upon him) born?",
        "options": {"A": "Madinah", "B": "Makkah", "C": "Jerusalem", "D": "Ta'if"},
        "correct_option": "B",
    },
    {
        "question_text": "In which cave did Prophet Muhammad (pbuh) receive the first revelation from Angel Jibreel?",
        "options": {"A": "Cave Thawr", "B": "Cave Hira", "C": "Cave of Uhud", "D": "Cave Kahf"},
        "correct_option": "B",
    },
    {
        "question_text": "The Prophet Muhammad (pbuh) said: 'The most beloved of deeds to Allah is that which is...'",
        "options": {"A": "Most expensive", "B": "Most regular and consistent, even if small", "C": "Done only once a year", "D": "Done in public only"},
        "correct_option": "B",
    },
    {
        "question_text": "What is the first month of the Islamic Hijri calendar?",
        "options": {"A": "Muharram", "B": "Safar", "C": "Ramadan", "D": "Shawwal"},
        "correct_option": "A",
    },
    {
        "question_text": "Which historic mosque located in northern Ethiopia is renowned as one of the oldest Islamic heritage sites in Africa?",
        "options": {"A": "Al-Nejashi Mosque", "B": "Anwar Mosque", "C": "Nur Mosque", "D": "Grand Jamia Mosque"},
        "correct_option": "A",
    },
    {
        "question_text": "What is the Islamic term for the obligatory annual charity given by eligible Muslims to support the poor?",
        "options": {"A": "Sadaqah", "B": "Zakat", "C": "Waqf", "D": "Fidyah"},
        "correct_option": "B",
    },
    {
        "question_text": "How many chapters (Surahs) are in the Holy Quran?",
        "options": {"A": "110", "B": "114", "C": "120", "D": "100"},
        "correct_option": "B",
    },
    {
        "question_text": "What was the decisive historical battle that took place on the 17th of Ramadan in the 2nd year of Hijrah?",
        "options": {"A": "Battle of Uhud", "B": "Battle of Badr", "C": "Battle of Khandaq", "D": "Battle of Hunayn"},
        "correct_option": "B",
    },
    {
        "question_text": "In Islamic terminology, the highest level of faith, 'Ihsan', is defined as:",
        "options": {
            "A": "Worshipping Allah as if you see Him, for if you see Him not, He sees you",
            "B": "Performing pilgrimage every year",
            "C": "Memorizing the dictionary",
            "D": "Fasting every alternate day",
        },
        "correct_option": "A",
    },
    {
        "question_text": "Which chemical element makes up approximately 78% of the Earth's atmosphere?",
        "options": {"A": "Oxygen", "B": "Nitrogen", "C": "Carbon Dioxide", "D": "Hydrogen"},
        "correct_option": "B",
    },
    {
        "question_text": "Which core principle best represents EMYC's commitment to community development and youth ethics?",
        "options": {
            "A": "Sincere service (Khidmah), integrity (Amanah), and moral excellence (Akhlaq)",
            "B": "Individual isolation",
            "C": "Material accumulation",
            "D": "Passive bystander culture",
        },
        "correct_option": "A",
    },
]


async def seed_sample_competition(
    session: AsyncSession,
    title: str = "EMYC 2026 Youth Knowledge Challenge",
    duration_minutes: int = 30,
    status: CompetitionStatus = CompetitionStatus.LIVE,
) -> Competition:
    now = datetime.now(timezone.utc)
    competition = Competition(
        id=uuid.uuid4(),
        title=title,
        description="Official Ethiopian Muslim Youth Council Competitive Exam",
        status=status,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(days=7),
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
        print(f"Successfully seeded competition: '{comp.title}' (ID: {comp.id}) with {len(SAMPLE_QUESTIONS)} questions.")
        print(f"Status: {comp.status.value}, Open until: {comp.closes_at}")


if __name__ == "__main__":
    asyncio.run(main())
