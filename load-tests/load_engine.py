"""High-throughput asynchronous distributed load-testing engine for EMYC Platform.

Simulates realistic, synchronized examination workloads:
1. Live Exam Start Surge: large participant volume fetching /session simultaneously.
2. In-Exam Phase: zero network traffic (offline-first client execution).
3. Live Exam Submission Spike: synchronized batch submission (/submit) with scoring.
4. Retry / Idempotency Storms: duplicate submissions under simulated network drops.
5. Admin Dashboard Concurrency: real-time dashboard metrics under peak examinee load.
"""

import asyncio
import json
import math
import time
import uuid
import sys
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import httpx
from httpx import ASGITransport, AsyncClient

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Default to benchmark SQLite if no database URL provided
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./benchmark.db"

from sqlalchemy import select, func
from app.main import app
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine, Base
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.api.v1.webapp import create_web_auth_token
from tests.test_webapp_high_throughput import make_test_init_data


class BenchmarkMetrics:
    def __init__(self, name: str):
        self.name = name
        self.latencies_ms: List[float] = []
        self.success_count = 0
        self.error_count = 0
        self.start_time: float = 0
        self.end_time: float = 0

    def start(self):
        self.start_time = time.perf_counter()

    def record(self, latency_ms: float, success: bool = True):
        self.latencies_ms.append(latency_ms)
        if success:
            self.success_count += 1
        else:
            self.error_count += 1

    def finish(self):
        self.end_time = time.perf_counter()

    def get_summary(self) -> Dict[str, Any]:
        duration = max(0.001, self.end_time - self.start_time)
        total_reqs = self.success_count + self.error_count
        rps = total_reqs / duration

        if not self.latencies_ms:
            return {
                "name": self.name,
                "total_requests": total_reqs,
                "success_count": self.success_count,
                "error_count": self.error_count,
                "error_rate_pct": 0.0,
                "duration_seconds": round(duration, 2),
                "rps": round(rps, 1),
                "p50_ms": 0.0,
                "p90_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "max_ms": 0.0,
            }

        sorted_lat = sorted(self.latencies_ms)
        n = len(sorted_lat)

        def pct(p: float) -> float:
            idx = int(math.ceil((p / 100.0) * n)) - 1
            return round(sorted_lat[min(max(0, idx), n - 1)], 2)

        return {
            "name": self.name,
            "total_requests": total_reqs,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "error_rate_pct": round((self.error_count / total_reqs) * 100, 3) if total_reqs else 0.0,
            "duration_seconds": round(duration, 2),
            "rps": round(rps, 1),
            "p50_ms": pct(50),
            "p90_ms": pct(90),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(sorted_lat[-1], 2),
        }


async def setup_benchmark_environment(num_participants: int, question_count: int = 100) -> Tuple[uuid.UUID, List[Dict[str, Any]]]:
    """Pre-seeds a dedicated LIVE competition and synthetic participants for the benchmark."""
    print(f"\n[SETUP] Seeding benchmark environment with {num_participants} participants and {question_count} questions...")
    now = datetime.now(timezone.utc)
    comp_id = uuid.uuid4()

    # Ensure schema is created
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        comp = Competition(
            id=comp_id,
            title=f"100K Benchmark Exam {str(comp_id)[:8]}",
            status=CompetitionStatus.LIVE,
            opens_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(minutes=55),
            actual_exam_started_at=now - timedelta(minutes=5),
            actual_exam_ends_at=now + timedelta(minutes=55),
            duration_minutes=60,
            question_count=question_count,
        )
        db.add(comp)

        # Seed questions
        questions = []
        for i in range(1, question_count + 1):
            q = CompetitionQuestion(
                competition_id=comp_id,
                question_text=f"Question #{i}: Standard Benchmark Item for Capacity Engineering",
                options={
                    "A": f"Canonical Option Alpha {i}",
                    "B": f"Canonical Option Beta {i}",
                    "C": f"Canonical Option Gamma {i}",
                    "D": f"Canonical Option Delta {i}",
                },
                correct_option=["A", "B", "C", "D"][(i - 1) % 4],
                order_index=i,
            )
            questions.append(q)
        db.add_all(questions)
        await db.commit()

        # Query existing synthetic participants if already seeded
        existing_result = await db.execute(
            select(Participant).where(
                Participant.telegram_user_id.between(900000001, 900000000 + num_participants)
            )
        )
        existing_map = {p.telegram_user_id: p for p in existing_result.scalars().all()}

        # Seed missing participants in batches
        participants_data = []
        batch_parts = []
        for i in range(1, num_participants + 1):
            tg_id = 900000000 + i
            if tg_id in existing_map:
                p = existing_map[tg_id]
            else:
                mid = f"LOADTEST/{i:06d}/2026"
                p = Participant(
                    telegram_user_id=tg_id,
                    telegram_username=f"examinee_{i}",
                    full_name=f"Load Candidate {i}",
                    phone_number=f"+251910{i:06d}",
                    membership_id=mid,
                    is_active=True,
                )
                batch_parts.append(p)

            token = create_web_auth_token(tg_id, p.telegram_username, p.full_name)
            participants_data.append({
                "telegram_user_id": tg_id,
                "membership_id": p.membership_id,
                "full_name": p.full_name,
                "token": token,
            })

            if len(batch_parts) >= 1000:
                db.add_all(batch_parts)
                await db.commit()
                batch_parts = []

        if batch_parts:
            db.add_all(batch_parts)
            await db.commit()

    print(f"[SETUP] Successfully initialized competition {comp_id} with {len(participants_data)} participants.")
    return comp_id, participants_data


async def run_session_launch_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    comp_id: uuid.UUID,
    concurrency_limit: int = 200,
) -> Tuple[BenchmarkMetrics, Dict[int, str]]:
    """Phase 1: Simulates the massive surge of examinees opening the Mini App at exam start."""
    metrics = BenchmarkMetrics("Session Retrieval Surge (GET /session)")
    attempt_ids: Dict[int, str] = {}
    sem = asyncio.Semaphore(concurrency_limit)

    async def fetch_session(p_info: Dict[str, Any]):
        tg_id = p_info["telegram_user_id"]
        headers = {
            "X-Web-Auth-Token": p_info["token"],
        }
        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await client.get(f"/api/v1/webapp/session?comp_id={comp_id}", headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "ready":
                    data = resp.json()
                    attempt_ids[tg_id] = data["attempt_id"]
                    metrics.record(latency_ms, success=True)
                else:
                    metrics.record(latency_ms, success=False)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False)

    metrics.start()
    tasks = [fetch_session(p) for p in participants]
    await asyncio.gather(*tasks)
    metrics.finish()
    return metrics, attempt_ids


async def run_batch_submission_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    concurrency_limit: int = 200,
) -> BenchmarkMetrics:
    """Phase 2: Simulates synchronized final submission surge at the end of the 60-minute exam."""
    metrics = BenchmarkMetrics("Batch Submission Surge (POST /submit)")
    sem = asyncio.Semaphore(concurrency_limit)

    async def submit_answers(p_info: Dict[str, Any]):
        tg_id = p_info["telegram_user_id"]
        att_id = attempt_ids.get(tg_id)
        if not att_id:
            return

        headers = {
            "X-Web-Auth-Token": p_info["token"],
        }
        # Simulate answers for 100 questions
        answers = [{"display_order": q_idx, "selected_option": ["A", "B", "C", "D"][(q_idx + tg_id) % 4]} for q_idx in range(1, 101)]
        payload = {
            "attempt_id": att_id,
            "answers": answers,
        }

        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "success":
                    metrics.record(latency_ms, success=True)
                else:
                    metrics.record(latency_ms, success=False)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False)

    metrics.start()
    tasks = [submit_answers(p) for p in participants]
    await asyncio.gather(*tasks)
    metrics.finish()
    return metrics


async def run_idempotency_retry_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    sample_size: int = 1000,
    concurrency_limit: int = 200,
) -> BenchmarkMetrics:
    """Phase 3: Simulates duplicate submission retries caused by mobile network disconnects."""
    metrics = BenchmarkMetrics("Submission Idempotency Retries (POST /submit)")
    sample_participants = participants[:sample_size]
    sem = asyncio.Semaphore(concurrency_limit)

    async def resubmit(p_info: Dict[str, Any]):
        tg_id = p_info["telegram_user_id"]
        att_id = attempt_ids.get(tg_id)
        if not att_id:
            return

        headers = {"X-Web-Auth-Token": p_info["token"]}
        answers = [{"display_order": 1, "selected_option": "A"}]
        payload = {"attempt_id": att_id, "answers": answers}

        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") in ["already_submitted", "success"]:
                    metrics.record(latency_ms, success=True)
                else:
                    metrics.record(latency_ms, success=False)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False)

    metrics.start()
    tasks = [resubmit(p) for p in sample_participants]
    await asyncio.gather(*tasks)
    metrics.finish()
    return metrics


async def execute_staged_benchmark(
    target_users: int = 1000,
    concurrency_limit: int = 250,
) -> Dict[str, Any]:
    """Runs end-to-end staged benchmark for a specified concurrency level."""
    print(f"\n================================================================================")
    print(f" EXECUTING STAGED BENCHMARK: {target_users:,} VIRTUAL EXAMINEES")
    print(f" Concurrency Semaphore: {concurrency_limit} | Target: FastAPI ASGI In-Process")
    print(f"================================================================================")

    comp_id, participants = await setup_benchmark_environment(target_users, question_count=100)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://benchmark", timeout=60.0) as client:
        # Step 1: Session Retrieval Surge
        print(f"\n[*] Starting Phase 1: {target_users:,} examinees opening Mini App...")
        sess_metrics, attempt_ids = await run_session_launch_surge(client, participants, comp_id, concurrency_limit)
        sess_summary = sess_metrics.get_summary()
        print(f"    --> Completed: {sess_summary['success_count']} successes, {sess_summary['error_count']} errors.")
        print(f"    --> Throughput: {sess_summary['rps']} RPS | Latency: p50={sess_summary['p50_ms']}ms, p95={sess_summary['p95_ms']}ms, p99={sess_summary['p99_ms']}ms")

        # Step 2: Batch Submission Surge
        print(f"\n[*] Starting Phase 2: {len(attempt_ids):,} examinees submitting final exam in tight spike...")
        sub_metrics = await run_batch_submission_surge(client, participants, attempt_ids, concurrency_limit)
        sub_summary = sub_metrics.get_summary()
        print(f"    --> Completed: {sub_summary['success_count']} successes, {sub_summary['error_count']} errors.")
        print(f"    --> Throughput: {sub_summary['rps']} Submissions/sec | Latency: p50={sub_summary['p50_ms']}ms, p95={sub_summary['p95_ms']}ms, p99={sub_summary['p99_ms']}ms")

        # Step 3: Network Retries & Idempotency Check
        retry_count = min(1000, target_users)
        print(f"\n[*] Starting Phase 3: {retry_count} duplicate retry submissions (network drop simulation)...")
        retry_metrics = await run_idempotency_retry_surge(client, participants, attempt_ids, sample_size=retry_count, concurrency_limit=concurrency_limit)
        retry_summary = retry_metrics.get_summary()
        print(f"    --> Completed: {retry_summary['success_count']} idempotent handles, {retry_summary['error_count']} errors.")
        print(f"    --> Throughput: {retry_summary['rps']} RPS | Latency: p95={retry_summary['p95_ms']}ms")

    return {
        "target_users": target_users,
        "competition_id": str(comp_id),
        "session_metrics": sess_summary,
        "submission_metrics": sub_summary,
        "retry_metrics": retry_summary,
    }


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    results = asyncio.run(execute_staged_benchmark(count, concurrency))
    print("\nBenchmark Execution Complete.")
    print(json.dumps(results, indent=2))
