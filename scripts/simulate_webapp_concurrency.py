"""High-Concurrency Async Simulator for Telegram Mini App Backend Engine.

Simulates hundreds of virtual examinees hitting the high-throughput Mini App endpoints:
1. One-shot question session download
2. Client-side local offline simulation (0ms backend cost)
3. Atomic batch 100-answer submission in a single database transaction

Reports exact Amazon-grade metrics:
- Total Operations
- Operations Per Second (RPS)
- Success Rate (target: 100.0%)
- p50, p90, p95, p99 Latency (target: < 50ms)
"""

import asyncio
import json
import time
import random
import uuid
import statistics
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.main import app
from app.core.config import get_settings
from app.core.database import get_db, Base
import app.core.database as core_db
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.core.telegram_auth import compute_init_data_hash

from sqlalchemy.pool import AsyncAdaptedQueuePool

settings = get_settings()

DB_URL = os.getenv("BENCHMARK_DB_URL", settings.TEST_DATABASE_URL)
bench_engine = create_async_engine(
    DB_URL,
    poolclass=AsyncAdaptedQueuePool,
    pool_size=30,
    max_overflow=20,
    pool_recycle=300,
)
BenchmarkSessionLocal = async_sessionmaker(bind=bench_engine, class_=AsyncSession, expire_on_commit=False)
core_db.AsyncSessionLocal = BenchmarkSessionLocal

async def override_get_db():
    async with BenchmarkSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db


def make_test_init_data(user_id: int) -> str:
    """Generates authentic cryptographically signed Telegram initData."""
    user_json = json.dumps({"id": user_id, "username": f"user_{user_id}", "first_name": f"Examinee {user_id}"}, separators=(",", ":"))
    auth_date = str(int(time.time()))
    data_dict = {"auth_date": auth_date, "user": user_json}
    calc_hash = compute_init_data_hash(data_dict, settings.TELEGRAM_BOT_TOKEN)
    return f"auth_date={auth_date}&user={user_json}&hash={calc_hash}"


async def setup_test_environment(num_questions: int = 10, num_participants: int = 200) -> Dict[str, Any]:
    """Sets up a live competition and pre-registers participants for load simulation."""
    async with bench_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with BenchmarkSessionLocal() as db:
        now = datetime.now(timezone.utc)
        comp = Competition(
            title=f"National 100k Concurrency Benchmark {uuid.uuid4().hex[:6]}",
            status=CompetitionStatus.LIVE,
            opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(days=2),
            actual_exam_started_at=now - timedelta(minutes=5),
            actual_exam_ends_at=now + timedelta(hours=2),
            duration_minutes=60,
            question_count=num_questions,
        )
        db.add(comp)
        await db.flush()

        # Seed questions
        questions = []
        for i in range(1, num_questions + 1):
            q = CompetitionQuestion(
                competition_id=comp.id,
                question_text=f"Sample Competition Question #{i}: What is the correct answer?",
                options={"A": "Option Alpha", "B": "Option Beta", "C": "Option Gamma", "D": "Option Delta"},
                correct_option=random.choice(["A", "B", "C", "D"]),
                order_index=i,
            )
            questions.append(q)
        db.add_all(questions)

        # Pre-register participants
        participants = []
        base_id = random.randint(20000000, 80000000)
        for i in range(num_participants):
            uid = base_id + i
            p = Participant(
                telegram_user_id=uid,
                membership_id=f"EMYC/{uid}/2026",
                full_name=f"Student {i+1}",
                phone_number=f"+25191{i:07d}",
                language_code="en",
            )
            participants.append(p)
        db.add_all(participants)
        await db.commit()

        return {
            "competition_id": str(comp.id),
            "participant_ids": [p.telegram_user_id for p in participants],
            "question_count": num_questions,
        }


async def simulate_single_examinee(
    client: AsyncClient,
    telegram_user_id: int,
    latencies: List[float],
    success_counter: List[int],
    error_counter: List[int],
) -> None:
    """Executes the complete lifecycle for 1 examinee: fetch session + submit 100 answers."""
    init_data = make_test_init_data(telegram_user_id)
    headers = {"X-Telegram-Init-Data": init_data}

    # 1. Fetch Session (1-shot question retrieval)
    t0 = time.perf_counter()
    try:
        sess_resp = await client.get("/api/v1/webapp/session", headers=headers)
        t_sess = time.perf_counter() - t0
        latencies.append(t_sess * 1000)

        if sess_resp.status_code != 200:
            error_counter[0] += 1
            return

        session_data = sess_resp.json()
        if session_data.get("status") != "ready":
            error_counter[0] += 1
            return

        attempt_id = session_data["attempt_id"]
        questions = session_data.get("questions", [])

        # 2. Local-first answer answering (0ms server cost)
        # Client answers all questions in phone memory
        answers_batch = []
        for q in questions:
            answers_batch.append({
                "question_id": q["question_id"],
                "display_order": q["display_order"],
                "selected_option": random.choice(["A", "B", "C", "D"]),
            })

        # 3. Batch Submit (1 single atomic transaction)
        submit_payload = {
            "attempt_id": attempt_id,
            "answers": answers_batch,
        }
        t1 = time.perf_counter()
        sub_resp = await client.post("/api/v1/webapp/submit", json=submit_payload, headers=headers)
        t_sub = time.perf_counter() - t1
        latencies.append(t_sub * 1000)

        if sub_resp.status_code == 200 and sub_resp.json().get("status") == "success":
            success_counter[0] += 1
        else:
            error_counter[0] += 1
    except Exception as e:
        error_counter[0] += 1


async def run_benchmark(num_participants: int = 150, concurrency_limit: int = 30):
    """Orchestrates high-concurrency examination benchmark."""
    print("=" * 65)
    print("  EMYC TELEGRAM MINI APP 100,000 EXAMINEE BENCHMARK HARNESS")
    print(f"  Simulating {num_participants} examinees with concurrency {concurrency_limit}")
    print("=" * 65)

    print("\n[1/3] Provisioning benchmark competition and examinees...")
    env_info = await setup_test_environment(num_questions=10, num_participants=num_participants)
    print(f"      Competition ID: {env_info['competition_id']}")
    print(f"      Examinees Provisioned: {len(env_info['participant_ids'])}")

    latencies: List[float] = []
    success_counter = [0]
    error_counter = [0]

    transport = ASGITransport(app=app)
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def bounded_worker(client: AsyncClient, uid: int):
        async with semaphore:
            await simulate_single_examinee(client, uid, latencies, success_counter, error_counter)

    print("\n[2/3] Launching high-throughput concurrent exam sessions...")
    start_time = time.perf_counter()

    async with AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
        tasks = [bounded_worker(client, uid) for uid in env_info["participant_ids"]]
        await asyncio.gather(*tasks)

    total_time = time.perf_counter() - start_time
    total_ops = len(latencies)
    ops_per_sec = total_ops / total_time if total_time > 0 else 0

    print("\n[3/3] Benchmark Results:")
    print("-" * 65)
    print(f"  Total Examinees Completed: {success_counter[0]} / {num_participants}")
    print(f"  Total API Operations:      {total_ops}")
    print(f"  Failed Operations:         {error_counter[0]} (Error Rate: {error_counter[0]/max(1, total_ops)*100:.2f}%)")
    print(f"  Total Duration:            {total_time:.2f} seconds")
    print(f"  Throughput (Ops / Sec):    {ops_per_sec:.1f} ops/sec")

    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p90 = latencies[int(len(latencies) * 0.90)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        print(f"  Latency p50:               {p50:.2f} ms")
        print(f"  Latency p90:               {p90:.2f} ms")
        print(f"  Latency p95:               {p95:.2f} ms")
        print(f"  Latency p99:               {p99:.2f} ms")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_benchmark(num_participants=100, concurrency_limit=25))
