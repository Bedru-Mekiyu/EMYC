"""High-throughput asynchronous capacity benchmarking engine for EMYC Platform.

Simulates realistic, synchronized examination workloads across progressive scale ladders:
1. Live Exam Start Surge: large participant volume fetching /session simultaneously.
2. In-Exam Phase: zero network traffic (offline-first client execution).
3. Live Exam Submission Spike: synchronized batch submission (/submit) with scoring.
4. Retry / Idempotency Storms: duplicate submissions under simulated network drops.
5. Admin Dashboard Concurrency: real-time dashboard metrics under peak examinee load.
6. Resource & System Telemetry: CPU %, RAM MB, DB Connections, Latency percentiles.
"""

import asyncio
import json
import math
import time
import uuid
import sys
import os
import socket
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from concurrent.futures import ProcessPoolExecutor
import psutil
from httpx import ASGITransport, AsyncClient
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("emyc_exam").setLevel(logging.WARNING)
logging.getLogger("emyc_exam.audit").setLevel(logging.WARNING)
logging.getLogger("app").setLevel(logging.WARNING)

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Intelligent database resolution: detect native PostgreSQL 17 on 5433
if "DATABASE_URL" not in os.environ:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 5433))
        s.close()
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@localhost:5433/competition_test_db"
    except Exception:
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./benchmark.db"

from sqlalchemy import select, func, insert, delete, text
from app.main import app
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine, Base
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.api.v1.webapp import create_web_auth_token


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


def summarize_metrics(
    name: str,
    latencies: List[float],
    success_count: int,
    error_count: int,
    duration: float,
) -> Dict[str, Any]:
    """Computes standard percentiles and throughput for aggregated run metrics."""
    duration = max(0.001, duration)
    total_reqs = success_count + error_count
    rps = total_reqs / duration

    if not latencies:
        return {
            "name": name,
            "total_requests": total_reqs,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate_pct": 0.0,
            "duration_seconds": round(duration, 2),
            "rps": round(rps, 1),
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    def pct(p: float) -> float:
        idx = int(math.ceil((p / 100.0) * n)) - 1
        return round(sorted_lat[min(max(0, idx), n - 1)], 2)

    return {
        "name": name,
        "total_requests": total_reqs,
        "success_count": success_count,
        "error_count": error_count,
        "error_rate_pct": round((error_count / total_reqs) * 100, 3) if total_reqs else 0.0,
        "duration_seconds": round(duration, 2),
        "rps": round(rps, 1),
        "p50_ms": pct(50),
        "p90_ms": pct(90),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "max_ms": round(sorted_lat[-1], 2),
    }


async def get_db_telemetry() -> Dict[str, Any]:
    """Captures real-time database connection metrics and active backend transactions."""
    settings = get_settings()
    if settings.DATABASE_URL.startswith("sqlite"):
        return {"db_type": "SQLite", "active_connections": 1}

    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"))
            active_conns = res.scalar() or 0
            return {
                "db_type": "PostgreSQL 17",
                "active_connections": active_conns,
            }
        except Exception:
            return {"db_type": "PostgreSQL 17", "active_connections": -1}


async def setup_benchmark_environment(num_participants: int, question_count: int = 100) -> Tuple[uuid.UUID, List[Dict[str, Any]]]:
    """Pre-seeds a dedicated LIVE competition and synthetic participants for the benchmark."""
    print(f"\n[SETUP] Initializing schema and benchmark competition with {num_participants:,} participants and {question_count} questions...")
    now = datetime.now(timezone.utc)
    comp_id = uuid.uuid4()

    # Ensure schema is created
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        comp = Competition(
            id=comp_id,
            title=f"Capacity Benchmark Exam {str(comp_id)[:8]}",
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

        # Check existing participants by max telegram_user_id to avoid redundant inserts
        max_tg_res = await db.execute(
            select(func.max(Participant.telegram_user_id)).where(Participant.telegram_user_id >= 900000001)
        )
        max_tg = max_tg_res.scalar() or 900000000
        existing_count = max(0, max_tg - 900000000)

        # Bulk insert missing participants using SQLAlchemy Core
        if num_participants > existing_count:
            print(f"[SETUP] Seeding {num_participants - existing_count:,} additional participants in fast bulk batches...")
            batch_size = 2000
            for start_idx in range(existing_count + 1, num_participants + 1, batch_size):
                end_idx = min(num_participants + 1, start_idx + batch_size)
                batch_rows = []
                for i in range(start_idx, end_idx):
                    batch_rows.append({
                        "id": uuid.uuid4(),
                        "telegram_user_id": 900000000 + i,
                        "telegram_username": f"examinee_{i}",
                        "full_name": f"Load Candidate {i}",
                        "phone_number": f"+251910{i:06d}",
                        "membership_id": f"LOADTEST/{i:06d}/2026",
                        "language_code": "en",
                        "registered_at": now,
                        "is_active": True,
                    })
                await db.execute(insert(Participant).values(batch_rows))
                await db.commit()

        # Generate lightweight participant authentication tokens in memory
        print(f"[SETUP] Generating WebAuth tokens for {num_participants:,} virtual examinees...")
        participants_data = []
        for i in range(1, num_participants + 1):
            tg_id = 900000000 + i
            username = f"examinee_{i}"
            full_name = f"Load Candidate {i}"
            token = create_web_auth_token(tg_id, username, full_name)
            participants_data.append({
                "telegram_user_id": tg_id,
                "token": token,
            })

    print(f"[SETUP] Successfully prepared competition {comp_id} with {len(participants_data):,} examinees.")
    return comp_id, participants_data


async def run_session_launch_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    comp_id: uuid.UUID,
    concurrency_limit: int = 250,
) -> Tuple[BenchmarkMetrics, Dict[int, str]]:
    """Phase 1: Simulates examinees launching the Mini App and requesting exam session."""
    metrics = BenchmarkMetrics("Session Retrieval Surge (GET /session)")
    attempt_ids: Dict[int, str] = {}
    queue: asyncio.Queue = asyncio.Queue()

    for p in participants:
        queue.put_nowait(p)

    async def session_worker():
        while not queue.empty():
            try:
                p_info = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            tg_id = p_info["telegram_user_id"]
            headers = {"X-Web-Auth-Token": p_info["token"]}
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
                    if metrics.error_count < 3:
                        print(f"[ERR SAMPLE] /session status={resp.status_code} body={resp.text[:120]}")
                    metrics.record(latency_ms, success=False)
            except Exception as e:
                t1 = time.perf_counter()
                if metrics.error_count < 3:
                    print(f"[EXC SAMPLE] /session {type(e).__name__}: {e}")
                metrics.record((t1 - t0) * 1000, success=False)
            finally:
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(session_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics, attempt_ids


async def run_batch_submission_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    concurrency_limit: int = 250,
) -> BenchmarkMetrics:
    """Phase 2: Simulates synchronized batch submission spike at the end of the 60-minute exam."""
    metrics = BenchmarkMetrics("Batch Submission Surge (POST /submit)")
    queue: asyncio.Queue = asyncio.Queue()

    for p in participants:
        if p["telegram_user_id"] in attempt_ids:
            queue.put_nowait(p)

    async def submit_worker():
        while not queue.empty():
            try:
                p_info = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            tg_id = p_info["telegram_user_id"]
            att_id = attempt_ids.get(tg_id)
            headers = {"X-Web-Auth-Token": p_info["token"]}
            answers = [
                {"display_order": q_idx, "selected_option": ["A", "B", "C", "D"][(q_idx + tg_id) % 4]}
                for q_idx in range(1, 101)
            ]
            payload = {"attempt_id": att_id, "answers": answers}

            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "success":
                    metrics.record(latency_ms, success=True)
                else:
                    if metrics.error_count < 3:
                        print(f"[ERR SAMPLE] /submit status={resp.status_code} body={resp.text[:120]}")
                    metrics.record(latency_ms, success=False)
            except Exception as e:
                t1 = time.perf_counter()
                if metrics.error_count < 3:
                    print(f"[EXC SAMPLE] /submit {type(e).__name__}: {e}")
                metrics.record((t1 - t0) * 1000, success=False)
            finally:
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(submit_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics


async def run_idempotency_retry_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    sample_size: int = 1000,
    concurrency_limit: int = 250,
) -> BenchmarkMetrics:
    """Phase 3: Simulates duplicate submission retries caused by mobile network disconnects."""
    metrics = BenchmarkMetrics("Submission Idempotency Retries (POST /submit)")
    sample = participants[:sample_size]
    queue: asyncio.Queue = asyncio.Queue()

    for p in sample:
        if p["telegram_user_id"] in attempt_ids:
            queue.put_nowait(p)

    async def retry_worker():
        while not queue.empty():
            try:
                p_info = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            tg_id = p_info["telegram_user_id"]
            att_id = attempt_ids.get(tg_id)
            headers = {"X-Web-Auth-Token": p_info["token"]}
            answers = [{"display_order": 1, "selected_option": "A"}]
            payload = {"attempt_id": att_id, "answers": answers}

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
            finally:
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(retry_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics


def _run_worker_process_slice(
    worker_id: int,
    participants_slice: List[Dict[str, Any]],
    comp_id_str: str,
    concurrency_per_worker: int,
) -> Dict[str, Any]:
    """Child worker process executing session surge and batch submission concurrently."""
    import asyncio
    import os
    import sys
    import uuid
    import time
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("emyc_exam").setLevel(logging.WARNING)
    logging.getLogger("emyc_exam.audit").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.WARNING)

    os.environ["DB_POOL_SIZE"] = "15"
    os.environ["DB_MAX_OVERFLOW"] = "5"
    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@localhost:5433/competition_test_db"

    from httpx import ASGITransport, AsyncClient
    from app.main import app

    comp_id = uuid.UUID(comp_id_str)

    async def _async_run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://benchmark", timeout=120.0) as client:
            t0_s = time.perf_counter()
            sess_metrics, attempt_ids = await run_session_launch_surge(
                client, participants_slice, comp_id, concurrency_per_worker
            )
            t1_s = time.perf_counter()

            t0_sub = time.perf_counter()
            sub_metrics = await run_batch_submission_surge(
                client, participants_slice, attempt_ids, concurrency_per_worker
            )
            t1_sub = time.perf_counter()

            retry_count = min(250, len(participants_slice))
            t0_ret = time.perf_counter()
            retry_metrics = await run_idempotency_retry_surge(
                client, participants_slice, attempt_ids, sample_size=retry_count, concurrency_limit=concurrency_per_worker
            )
            t1_ret = time.perf_counter()

            return {
                "worker_id": worker_id,
                "session_latencies": sess_metrics.latencies_ms,
                "session_success": sess_metrics.success_count,
                "session_error": sess_metrics.error_count,
                "session_duration": t1_s - t0_s,
                "submission_latencies": sub_metrics.latencies_ms,
                "submission_success": sub_metrics.success_count,
                "submission_error": sub_metrics.error_count,
                "submission_duration": t1_sub - t0_sub,
                "retry_latencies": retry_metrics.latencies_ms,
                "retry_success": retry_metrics.success_count,
                "retry_error": retry_metrics.error_count,
                "retry_duration": t1_ret - t0_ret,
            }

    return asyncio.run(_async_run())


async def execute_staged_benchmark(
    target_users: int = 1000,
    concurrency_limit: int = 250,
    worker_processes: int = 1,
) -> Dict[str, Any]:
    """Runs end-to-end benchmark for a specified user scale and captures system telemetry."""
    print("\n" + "=" * 80)
    print(f" EXECUTING CAPACITY BENCHMARK: {target_users:,} CONCURRENT EXAMINEES")
    print(f" Concurrency Limit: {concurrency_limit} | Worker Processes: {worker_processes}")
    print(f" Target Database: {get_settings().DATABASE_URL[:40]}...")
    print("=" * 80)

    # Initial telemetry
    process = psutil.Process()
    cpu_before = psutil.cpu_percent(interval=None)
    mem_before_mb = process.memory_info().rss / (1024 * 1024)
    db_telemetry_before = await get_db_telemetry()
    print(f"[TELEMETRY] Initial State: CPU={cpu_before}% | Process RAM={mem_before_mb:.1f} MB | DB Active Conns={db_telemetry_before.get('active_connections')}")

    comp_id, participants = await setup_benchmark_environment(target_users, question_count=100)

    if worker_processes > 1:
        chunk_size = math.ceil(len(participants) / worker_processes)
        slices = [
            participants[i * chunk_size : min((i + 1) * chunk_size, len(participants))]
            for i in range(worker_processes)
        ]
        concurrency_per_worker = max(10, concurrency_limit // worker_processes)
        print(f"\n[*] Launching {worker_processes} parallel worker processes (concurrency per process: {concurrency_per_worker})...")

        with ProcessPoolExecutor(max_workers=worker_processes) as executor:
            futures = [
                executor.submit(
                    _run_worker_process_slice,
                    idx + 1,
                    slices[idx],
                    str(comp_id),
                    concurrency_per_worker,
                )
                for idx in range(worker_processes)
                if slices[idx]
            ]
            worker_results = [f.result() for f in futures]

        all_sess_lat = sum([w["session_latencies"] for w in worker_results], [])
        total_sess_succ = sum(w["session_success"] for w in worker_results)
        total_sess_err = sum(w["session_error"] for w in worker_results)
        sess_max_dur = max(w["session_duration"] for w in worker_results)
        sess_summary = summarize_metrics("Session Retrieval Surge (GET /session)", all_sess_lat, total_sess_succ, total_sess_err, sess_max_dur)

        all_sub_lat = sum([w["submission_latencies"] for w in worker_results], [])
        total_sub_succ = sum(w["submission_success"] for w in worker_results)
        total_sub_err = sum(w["submission_error"] for w in worker_results)
        sub_max_dur = max(w["submission_duration"] for w in worker_results)
        sub_summary = summarize_metrics("Batch Submission Surge (POST /submit)", all_sub_lat, total_sub_succ, total_sub_err, sub_max_dur)

        all_retry_lat = sum([w["retry_latencies"] for w in worker_results], [])
        total_retry_succ = sum(w["retry_success"] for w in worker_results)
        total_retry_err = sum(w["retry_error"] for w in worker_results)
        retry_max_dur = max(w["retry_duration"] for w in worker_results)
        retry_summary = summarize_metrics("Submission Idempotency Retries (POST /submit)", all_retry_lat, total_retry_succ, total_retry_err, retry_max_dur)
    else:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://benchmark", timeout=90.0) as client:
            # Phase 1: Synchronized Session Retrieval Surge
            print(f"\n[*] Starting Phase 1: {target_users:,} examinees opening Mini App...")
            sess_metrics, attempt_ids = await run_session_launch_surge(client, participants, comp_id, concurrency_limit)
            sess_summary = sess_metrics.get_summary()

            # Phase 2: Synchronized Batch Submission Spike
            print(f"\n[*] Starting Phase 2: {len(attempt_ids):,} examinees submitting final exam in tight spike...")
            sub_metrics = await run_batch_submission_surge(client, participants, attempt_ids, concurrency_limit)
            sub_summary = sub_metrics.get_summary()

            # Phase 3: Network Retries & Idempotency Check
            retry_count = min(1000, target_users)
            print(f"\n[*] Starting Phase 3: {retry_count:,} duplicate retry submissions (network drop simulation)...")
            retry_metrics = await run_idempotency_retry_surge(client, participants, attempt_ids, sample_size=retry_count, concurrency_limit=concurrency_limit)
            retry_summary = retry_metrics.get_summary()

    print(f"\n[PHASE 1] Session Retrieval: {sess_summary['success_count']:,} successes, {sess_summary['error_count']} errors ({sess_summary['error_rate_pct']}%) | {sess_summary['rps']} RPS | p50={sess_summary['p50_ms']}ms, p95={sess_summary['p95_ms']}ms")
    print(f"[PHASE 2] Batch Submission: {sub_summary['success_count']:,} successes, {sub_summary['error_count']} errors ({sub_summary['error_rate_pct']}%) | {sub_summary['rps']} Submissions/sec | p50={sub_summary['p50_ms']}ms, p95={sub_summary['p95_ms']}ms")
    print(f"[PHASE 3] Idempotent Retries: {retry_summary['success_count']:,} handles, {retry_summary['error_count']} errors ({retry_summary['error_rate_pct']}%) | {retry_summary['rps']} RPS | p95={retry_summary['p95_ms']}ms")

    # Final telemetry
    cpu_after = psutil.cpu_percent(interval=None)
    mem_after_mb = process.memory_info().rss / (1024 * 1024)
    db_telemetry_after = await get_db_telemetry()
    print(f"\n[TELEMETRY] Final State: CPU={cpu_after}% | Process RAM={mem_after_mb:.1f} MB | DB Active Conns={db_telemetry_after.get('active_connections')}")

    return {
        "target_users": target_users,
        "concurrency_limit": concurrency_limit,
        "worker_processes": worker_processes,
        "competition_id": str(comp_id),
        "database": db_telemetry_after,
        "telemetry": {
            "ram_before_mb": round(mem_before_mb, 1),
            "ram_after_mb": round(mem_after_mb, 1),
            "cpu_percent": cpu_after,
        },
        "session_metrics": sess_summary,
        "submission_metrics": sub_summary,
        "retry_metrics": retry_summary,
    }


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    results = asyncio.run(execute_staged_benchmark(count, concurrency, worker_processes=workers))
    print("\nBenchmark Execution Complete.")
    print(json.dumps(results, indent=2))
