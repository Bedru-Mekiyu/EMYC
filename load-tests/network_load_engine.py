"""Production Network Topology Capacity Benchmarking Engine for EMYC Platform.

Executes real HTTP/1.1 TCP network workloads through the Nginx reverse proxy
and load-balanced multi-worker application cluster:

    Load Engine (Real TCP Sockets)
                 │
                 ▼
    Nginx Reverse Proxy & Load Balancer (:8080)
                 │  least_conn / keepalive
         ┌───────┼───────┬───────┐
         ▼       ▼       ▼       ▼
       App 1   App 2   App 3   App 4  (Uvicorn Workers)
         │       │       │       │
         └───────┼───────┴───────┘
                 ▼
    PostgreSQL 16 Production Container (:5432)
"""

import asyncio
import json
import math
import time
import uuid
import sys
import os
import socket
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import psutil
import httpx
from httpx import AsyncClient

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TELEGRAM_BOT_TOKEN"] = "mock_token_for_tests"

# Intelligent database resolution for setup & audit:
# Check containerized DB on 5434 first, then native PostgreSQL on 5433
if "DATABASE_URL" not in os.environ:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 5434))
        s.close()
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgrespassword@localhost:5434/competition_db"
    except Exception:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", 5433))
            s.close()
            os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@localhost:5433/competition_test_db"
        except Exception:
            os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./benchmark.db"

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from sqlalchemy import select, func, insert, text
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine, Base
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.api.v1.webapp import create_web_auth_token


class NetworkBenchmarkMetrics:
    def __init__(self, name: str):
        self.name = name
        self.latencies_ms: List[float] = []
        self.success_count = 0
        self.error_count = 0
        self.status_codes: Dict[int, int] = {}
        self.start_time: float = 0
        self.end_time: float = 0
        self.current_in_flight = 0
        self.peak_in_flight = 0

    def start(self):
        self.start_time = time.perf_counter()

    def enter_request(self):
        self.current_in_flight += 1
        if self.current_in_flight > self.peak_in_flight:
            self.peak_in_flight = self.current_in_flight

    def exit_request(self):
        self.current_in_flight = max(0, self.current_in_flight - 1)

    def record(self, latency_ms: float, success: bool = True, status_code: int = 200):
        self.latencies_ms.append(latency_ms)
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1
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
                "peak_in_flight_concurrency": self.peak_in_flight,
                "status_distribution": self.status_codes,
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
            "peak_in_flight_concurrency": self.peak_in_flight,
            "status_distribution": self.status_codes,
            "p50_ms": pct(50),
            "p90_ms": pct(90),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "max_ms": round(sorted_lat[-1], 2),
        }


class DatabasePeakSampler:
    """Continuously samples PostgreSQL connection pool metrics to record high-water marks."""
    def __init__(self, interval_sec: float = 0.5):
        self.interval_sec = interval_sec
        self.running = False
        self.peak_connections = 0
        self.peak_active_connections = 0
        self._task: Optional[asyncio.Task] = None

    async def _sample_loop(self):
        settings = get_settings()
        if settings.DATABASE_URL.startswith("sqlite"):
            return
        while self.running:
            try:
                async with AsyncSessionLocal() as db:
                    res = await db.execute(text(
                        "SELECT count(*), count(*) FILTER (WHERE state = 'active') FROM pg_stat_activity WHERE datname = current_database();"
                    ))
                    row = res.first()
                    if row:
                        total, active = row[0] or 0, row[1] or 0
                        if total > self.peak_connections:
                            self.peak_connections = total
                        if active > self.peak_active_connections:
                            self.peak_active_connections = active
            except Exception:
                pass
            await asyncio.sleep(self.interval_sec)

    def start(self):
        self.running = True
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> Dict[str, int]:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return {
            "peak_total_connections": self.peak_connections,
            "peak_active_connections": self.peak_active_connections,
        }


async def get_db_telemetry() -> Dict[str, Any]:
    """Captures connection count from the active database."""
    settings = get_settings()
    if settings.DATABASE_URL.startswith("sqlite"):
        return {"db_type": "SQLite", "active_connections": 1}

    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"))
            active_conns = res.scalar() or 0
            return {
                "db_type": "PostgreSQL Container",
                "active_connections": active_conns,
            }
        except Exception:
            return {"db_type": "PostgreSQL", "active_connections": 0}


async def setup_benchmark_environment(
    num_participants: int,
    question_count: int = 100,
    comp_id: Optional[uuid.UUID] = None,
    participant_offset: int = 0,
) -> Tuple[uuid.UUID, List[Dict[str, Any]]]:
    """Initializes schema and seeds participants in the target production database with optional partitioning."""
    print(f"\n[SETUP] Initializing schema and benchmark participants (count={num_participants:,}, offset={participant_offset:,}, questions={question_count})...")
    if comp_id is None:
        comp_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Ensure schema is initialized
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Verify schema self-healing columns and indexes exist
        await conn.execute(text("ALTER TABLE exam_attempts ADD COLUMN IF NOT EXISTS answers_summary JSONB;"))
        await conn.execute(text("ALTER TABLE exam_attempts ADD COLUMN IF NOT EXISTS question_sequence JSONB;"))
        await conn.execute(text("ALTER TABLE competitions ADD COLUMN IF NOT EXISTS actual_exam_started_at TIMESTAMPTZ;"))
        await conn.execute(text("ALTER TABLE competitions ADD COLUMN IF NOT EXISTS actual_exam_ends_at TIMESTAMPTZ;"))
        await conn.execute(text("ALTER TABLE participants ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);"))
        await conn.execute(text("ALTER TABLE participants ADD COLUMN IF NOT EXISTS phone_number VARCHAR(50);"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_exam_attempts_comp_status ON exam_attempts (competition_id, status);"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_exam_attempts_status_deadline ON exam_attempts (status, deadline_at);"))

    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, comp_id)
        if not comp:
            comp = Competition(
                id=comp_id,
                title=f"Network Capacity Exam {str(comp_id)[:8]}",
                status=CompetitionStatus.LIVE,
                opens_at=now - timedelta(minutes=5),
                closes_at=now + timedelta(hours=4),
                actual_exam_started_at=now - timedelta(minutes=5),
                actual_exam_ends_at=now + timedelta(hours=4),
                duration_minutes=240,
                question_count=question_count,
            )
            db.add(comp)

            # Seed questions
            questions = []
            for i in range(1, question_count + 1):
                q = CompetitionQuestion(
                    competition_id=comp_id,
                    question_text=f"Question #{i}: Production Network Item for Capacity Engineering",
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

        # Partitioned participant range
        start_tg_id = 900000000 + participant_offset
        end_tg_id = start_tg_id + num_participants

        # Find existing participants within this specific partitioned range
        res = await db.execute(
            select(Participant.telegram_user_id).where(
                Participant.telegram_user_id.between(start_tg_id + 1, end_tg_id)
            )
        )
        existing_ids = set(res.scalars().all())
        missing_ids = [i for i in range(start_tg_id + 1, end_tg_id + 1) if i not in existing_ids]

        if missing_ids:
            print(f"[SETUP] Seeding {len(missing_ids):,} missing participants in range {start_tg_id + 1}..{end_tg_id}...")
            batch_size = 2000
            for b_start in range(0, len(missing_ids), batch_size):
                chunk = missing_ids[b_start:b_start + batch_size]
                batch_rows = [{
                    "id": uuid.uuid4(),
                    "telegram_user_id": tg_id,
                    "telegram_username": f"examinee_{tg_id - 900000000}",
                    "full_name": f"Load Candidate {tg_id - 900000000}",
                    "phone_number": f"+251910{(tg_id % 1000000):06d}",
                    "membership_id": f"LOADTEST/{(tg_id % 1000000):06d}/2026",
                    "language_code": "en",
                    "registered_at": now,
                    "is_active": True,
                } for tg_id in chunk]
                await db.execute(insert(Participant).values(batch_rows))
                await db.commit()

        print(f"[SETUP] Generating WebAuth tokens for {num_participants:,} virtual examinees (offset {participant_offset:,})...")
        participants_data = []
        for i in range(start_tg_id + 1, end_tg_id + 1):
            idx = i - 900000000
            username = f"examinee_{idx}"
            full_name = f"Load Candidate {idx}"
            token = create_web_auth_token(i, username, full_name)
            participants_data.append({
                "telegram_user_id": i,
                "token": token,
            })

    print(f"[SETUP] Successfully prepared competition {comp_id} with {len(participants_data):,} examinees.")
    return comp_id, participants_data


async def run_network_session_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    comp_id: uuid.UUID,
    concurrency_limit: int = 250,
) -> Tuple[NetworkBenchmarkMetrics, Dict[int, str]]:
    """Phase 1: Real TCP GET /session requests routed through Nginx to application cluster."""
    metrics = NetworkBenchmarkMetrics("Network Session Retrieval Surge (GET /session via Nginx)")
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
            metrics.enter_request()
            t0 = time.perf_counter()
            try:
                resp = await client.get(f"/api/v1/webapp/session?comp_id={comp_id}", headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "ready":
                    data = resp.json()
                    attempt_ids[tg_id] = data["attempt_id"]
                    metrics.record(latency_ms, success=True, status_code=resp.status_code)
                else:
                    if metrics.error_count < 3:
                        print(f"[NETWORK ERR] /session status={resp.status_code} body={resp.text[:120]}")
                    metrics.record(latency_ms, success=False, status_code=resp.status_code)
            except Exception as e:
                t1 = time.perf_counter()
                if metrics.error_count < 3:
                    print(f"[SOCKET EXC] /session {type(e).__name__}: {e}")
                metrics.record((t1 - t0) * 1000, success=False, status_code=599)
            finally:
                metrics.exit_request()
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(session_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics, attempt_ids


ANSWER_PATTERNS = [
    [{"display_order": q_idx, "selected_option": ["A", "B", "C", "D"][(q_idx + offset) % 4]} for q_idx in range(1, 101)]
    for offset in range(4)
]


async def run_network_submission_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    concurrency_limit: int = 250,
) -> NetworkBenchmarkMetrics:
    """Phase 2: Real TCP POST /submit requests routed through Nginx to application cluster."""
    metrics = NetworkBenchmarkMetrics("Network Batch Submission Surge (POST /submit via Nginx)")
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
            answers = ANSWER_PATTERNS[tg_id % 4]
            payload = {"attempt_id": att_id, "answers": answers}

            metrics.enter_request()
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "success":
                    metrics.record(latency_ms, success=True, status_code=resp.status_code)
                else:
                    if metrics.error_count < 3:
                        print(f"[NETWORK ERR] /submit status={resp.status_code} body={resp.text[:120]}")
                    metrics.record(latency_ms, success=False, status_code=resp.status_code)
            except Exception as e:
                t1 = time.perf_counter()
                if metrics.error_count < 3:
                    print(f"[SOCKET EXC] /submit {type(e).__name__}: {e}")
                metrics.record((t1 - t0) * 1000, success=False, status_code=599)
            finally:
                metrics.exit_request()
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(submit_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics


async def run_network_retry_surge(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    sample_size: int = 1000,
    concurrency_limit: int = 250,
) -> NetworkBenchmarkMetrics:
    """Phase 3: Real TCP POST /submit retry requests simulating mobile disconnects."""
    metrics = NetworkBenchmarkMetrics("Network Idempotent Retries (POST /submit via Nginx)")
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

            metrics.enter_request()
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") in ["already_submitted", "success"]:
                    metrics.record(latency_ms, success=True, status_code=resp.status_code)
                else:
                    metrics.record(latency_ms, success=False, status_code=resp.status_code)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False, status_code=599)
            finally:
                metrics.exit_request()
                queue.task_done()

    metrics.start()
    workers = [asyncio.create_task(retry_worker()) for _ in range(concurrency_limit)]
    await asyncio.gather(*workers)
    metrics.finish()
    return metrics


async def execute_network_benchmark(
    target_users: int = 1000,
    concurrency_limit: int = 250,
    base_url: str = "http://127.0.0.1:8080",
) -> Dict[str, Any]:
    """Executes end-to-end benchmark against live Nginx reverse proxy endpoint."""
    print("\n" + "=" * 80)
    print(f" EXECUTING PRODUCTION TOPOLOGY BENCHMARK: {target_users:,} CONCURRENT EXAMINEES")
    print(f" Concurrency Limit: {concurrency_limit} | Target URL: {base_url}")
    print(f" Target Database: {get_settings().DATABASE_URL[:45]}...")
    print("=" * 80)

    # Initial telemetry
    process = psutil.Process()
    cpu_before = psutil.cpu_percent(interval=None)
    mem_before_mb = process.memory_info().rss / (1024 * 1024)
    db_telemetry_before = await get_db_telemetry()
    print(f"[TELEMETRY] Initial State: CPU={cpu_before}% | Process RAM={mem_before_mb:.1f} MB | DB Active Conns={db_telemetry_before.get('active_connections')}")

    comp_id, participants = await setup_benchmark_environment(target_users, question_count=100)

    # Start peak connection monitor
    peak_sampler = DatabasePeakSampler(interval_sec=0.5)
    peak_sampler.start()

    # Configure real TCP HTTP/1.1 client with high connection pooling
    limits = httpx.Limits(max_connections=600, max_keepalive_connections=300, keepalive_expiry=60.0)
    async with AsyncClient(base_url=base_url, limits=limits, timeout=180.0) as client:
        # Check reverse proxy health
        try:
            health_resp = await client.get("/health")
            print(f"[TOPOLOGY] Nginx & Upstream Health Check: status={health_resp.status_code}")
        except Exception as e:
            print(f"[WARNING] Health check failed: {e}")

        # Phase 1: Session Retrieval Surge
        print(f"\n[*] Starting Phase 1: {target_users:,} examinees opening Mini App via Nginx...")
        sess_metrics, attempt_ids = await run_network_session_surge(client, participants, comp_id, concurrency_limit)
        sess_summary = sess_metrics.get_summary()

        # Phase 2: Batch Submission Spike
        print(f"\n[*] Starting Phase 2: {len(attempt_ids):,} examinees submitting final exam via Nginx...")
        sub_metrics = await run_network_submission_surge(client, participants, attempt_ids, concurrency_limit)
        sub_summary = sub_metrics.get_summary()

        # Phase 3: Network Retries & Idempotency Check
        retry_count = min(1000, target_users)
        print(f"\n[*] Starting Phase 3: {retry_count:,} duplicate retry submissions via Nginx...")
        retry_metrics = await run_network_retry_surge(client, participants, attempt_ids, sample_size=retry_count, concurrency_limit=concurrency_limit)
        retry_summary = retry_metrics.get_summary()

    # Stop peak connection monitor
    db_peak_data = await peak_sampler.stop()

    print(f"\n[PHASE 1] Session Retrieval: {sess_summary['success_count']:,} successes, {sess_summary['error_count']} errors ({sess_summary['error_rate_pct']}%) | {sess_summary['rps']} RPS | p50={sess_summary['p50_ms']}ms, p95={sess_summary['p95_ms']}ms | Peak In-Flight={sess_summary['peak_in_flight_concurrency']}")
    print(f"[PHASE 2] Batch Submission: {sub_summary['success_count']:,} successes, {sub_summary['error_count']} errors ({sub_summary['error_rate_pct']}%) | {sub_summary['rps']} Submissions/sec | p50={sub_summary['p50_ms']}ms, p95={sub_summary['p95_ms']}ms | Peak In-Flight={sub_summary['peak_in_flight_concurrency']}")
    print(f"[PHASE 3] Idempotent Retries: {retry_summary['success_count']:,} handles, {retry_summary['error_count']} errors ({retry_summary['error_rate_pct']}%) | {retry_summary['rps']} RPS | p95={retry_summary['p95_ms']}ms")
    print(f"[DB HEADROOM] Peak DB Connections Observed: {db_peak_data['peak_total_connections']} / 250 limit | Peak Active Queries: {db_peak_data['peak_active_connections']}")

    # Final telemetry
    cpu_after = psutil.cpu_percent(interval=None)
    mem_after_mb = process.memory_info().rss / (1024 * 1024)
    db_telemetry_after = await get_db_telemetry()
    print(f"\n[TELEMETRY] Final State: CPU={cpu_after}% | Process RAM={mem_after_mb:.1f} MB | DB Active Conns={db_telemetry_after.get('active_connections')}")

    return {
        "target_users": target_users,
        "concurrency_limit": concurrency_limit,
        "target_url": base_url,
        "competition_id": str(comp_id),
        "database_headroom": {
            "max_connections_limit": 250,
            "peak_total_connections_observed": db_peak_data["peak_total_connections"],
            "peak_active_connections_observed": db_peak_data["peak_active_connections"],
            "final_active_connections": db_telemetry_after.get("active_connections"),
        },
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
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    url = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8080"
    results = asyncio.run(execute_network_benchmark(count, concurrency, url))
    print("\nProduction Topology Benchmark Execution Complete.")
    print(json.dumps(results, indent=2))
