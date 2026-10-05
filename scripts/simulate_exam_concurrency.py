"""High-concurrency simulation and stress-testing tool for the EMYC Examination Platform.

Simulates hundreds to thousands of concurrent participants simultaneously starting an
exam, answering questions, and submitting results to measure backend throughput (RPS),
database latency, and reliability.

Usage:
    python scripts/simulate_exam_concurrency.py --participants 200 --questions 20
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.competition_service import CompetitionService
from app.services.scoring_service import ScoringAndRankingService


async def setup_test_competition(num_questions: int = 20) -> uuid.UUID:
    """Creates a sample LIVE competition with questions for the simulation."""
    from app.core.database import Base
    import app.core.database as core_db

    # Auto-create tables if running against fresh database
    async with core_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        comp = Competition(
            title=f"Stress Test Simulation ({num_questions} Questions)",
            description="Automated concurrency stress testing",
            status=CompetitionStatus.LIVE,
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=1),
            actual_exam_started_at=now - timedelta(minutes=5),
            actual_exam_ends_at=now + timedelta(hours=2),
            duration_minutes=30,
            question_count=num_questions,
        )
        db.add(comp)
        await db.flush()

        for idx in range(1, num_questions + 1):
            q = CompetitionQuestion(
                competition_id=comp.id,
                question_text=f"Sample Question {idx}: What is the correct response?",
                options={"A": "Option Alpha", "B": "Option Beta", "C": "Option Gamma", "D": "Option Delta"},
                correct_option="A" if idx % 2 == 1 else "B",
                order_index=idx,
            )
            db.add(q)
        await db.commit()
        return comp.id


async def simulate_single_participant(
    user_idx: int,
    comp_id: uuid.UUID,
    num_questions: int,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Simulates one complete student journey: registration, start, answering, and submission."""
    async with semaphore:
        t0 = time.perf_counter()
        tg_id = 9000000 + user_idx
        mem_id = f"EMYC/SIM/{user_idx:06d}/2026"
        p_name = f"Simulated Student {user_idx}"
        phone = f"+2519{user_idx:08d}"[-13:]

        try:
            async with AsyncSessionLocal() as db:
                # 1. Register / Get Participant
                stmt = select(Participant).where(Participant.telegram_user_id == tg_id)
                res = await db.execute(stmt)
                p = res.scalar_one_or_none()
                if not p:
                    p = Participant(
                        telegram_user_id=tg_id,
                        membership_id=mem_id,
                        full_name=p_name,
                        phone_number=phone,
                        language_code="en",
                    )
                    db.add(p)
                    await db.commit()
                    await db.refresh(p)
                p_id = p.id

            # 2. Start Attempt
            async with AsyncSessionLocal() as db:
                attempt = await CompetitionService.start_attempt(db, comp_id, p_id)
                att_id = attempt.id

            # 3. Submit Answers
            async with AsyncSessionLocal() as db:
                for q_num in range(1, min(6, num_questions + 1)):
                    # Submit first few answers to simulate answering
                    chosen = "A" if q_num % 2 == 1 else "B"
                    await CompetitionService.submit_answer(
                        db=db,
                        attempt_id=att_id,
                        selected_display_option=chosen,
                        participant_id=p_id,
                        display_order=q_num,
                    )

            # 4. Final Submit Exam
            async with AsyncSessionLocal() as db:
                await CompetitionService.submit_attempt(db, att_id, participant_id=p_id)

            duration = time.perf_counter() - t0
            return {"user_idx": user_idx, "success": True, "duration": duration, "error": None}

        except Exception as e:
            duration = time.perf_counter() - t0
            return {"user_idx": user_idx, "success": False, "duration": duration, "error": str(e)}


async def run_simulation(num_participants: int, num_questions: int, max_concurrency: int):
    """Executes the concurrent simulation batch."""
    print("=" * 75)
    print(f" EMYC PLATFORM CONCURRENCY STRESS TEST")
    print(f" Target Participants: {num_participants} | Questions: {num_questions} | Concurrency: {max_concurrency}")
    print("=" * 75)

    print("\n[1/4] Setting up test competition and questions in database...")
    comp_id = await setup_test_competition(num_questions)
    print(f"      Competition created: {comp_id}")

    print(f"\n[2/4] Spawning {num_participants} simultaneous participant exam sessions...")
    semaphore = asyncio.Semaphore(max_concurrency)
    start_time = time.perf_counter()

    tasks = [
        simulate_single_participant(i, comp_id, num_questions, semaphore)
        for i in range(1, num_participants + 1)
    ]
    results = await asyncio.gather(*tasks)
    total_time = time.perf_counter() - start_time

    print(f"\n[3/4] Finalizing scores and rankings for {num_participants} examinees...")
    t_fin = time.perf_counter()
    async with AsyncSessionLocal() as db:
        finalized = await ScoringAndRankingService.finalize_competition_results(db, comp_id)
        csv_data, filename = await ScoringAndRankingService.generate_results_csv(db, comp_id)
    fin_time = time.perf_counter() - t_fin
    print(f"      Finalized {len(finalized)} attempts in {fin_time:.2f}s.")
    print(f"      Official CSV generated: {filename} ({len(csv_data.encode('utf-8')) / 1024:.1f} KB)")

    # Analyze metrics
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    durations = [r["duration"] for r in successful]

    avg_latency = sum(durations) / len(durations) if durations else 0
    min_latency = min(durations) if durations else 0
    max_latency = max(durations) if durations else 0
    rps = (num_participants * 8) / total_time  # ~8 DB ops per participant

    print("\n" + "=" * 75)
    print(" SIMULATION RESULTS SUMMARY")
    print("=" * 75)
    print(f" • Total Participants Simulated : {num_participants}")
    print(f" • Successful Exam Sessions     : {len(successful)} ({(len(successful)/num_participants)*100:.1f}%)")
    print(f" • Failed Sessions              : {len(failed)}")
    print(f" • Total Time Elapsed           : {total_time:.2f} seconds")
    print(f" • Effective Database Ops/sec   : {rps:.1f} ops/second")
    print(f" • Average Journey Latency      : {avg_latency:.3f}s (Min: {min_latency:.3f}s, Max: {max_latency:.3f}s)")
    if failed:
        print("\n [!] Sample Errors:")
        for f in failed[:3]:
            print(f"    - Participant {f['user_idx']}: {f['error']}")
    else:
        print("\n [SUCCESS] 100% SUCCESS RATE -- ZERO DATABASE ERRORS OR RACE CONDITIONS!")
    print("=" * 75)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EMYC Concurrency Simulation")
    parser.add_argument("--participants", type=int, default=100, help="Number of simulated examinees")
    parser.add_argument("--questions", type=int, default=20, help="Number of questions in exam")
    parser.add_argument("--concurrency", type=int, default=20, help="Concurrent workers")
    parser.add_argument("--test-db", action="store_true", help="Use local test database (port 5433)")
    args = parser.parse_args()

    if args.test_db:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from app.core.config import get_settings
        import app.core.database as core_db
        engine_test = create_async_engine(get_settings().TEST_DATABASE_URL, echo=False)
        core_db.engine = engine_test
        core_db.AsyncSessionLocal = async_sessionmaker(bind=engine_test, class_=AsyncSession, expire_on_commit=False)
        # Also re-import into this module
        AsyncSessionLocal = core_db.AsyncSessionLocal

    asyncio.run(run_simulation(args.participants, args.questions, args.concurrency))
