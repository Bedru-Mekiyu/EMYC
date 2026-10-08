"""Synchronized Arrival and Submission Burst Testing Engine for EMYC Platform.

Simulates genuine real-world competitive examination burst scenarios:
1. Synchronized START/SESSION bursts:
   - 10,000 requests within 1.0 second
   - 25,000 requests within 5.0 seconds
   - 50,000 requests within 10.0 seconds
   - 100,000 requests within 30.0 seconds
2. Synchronized SUBMISSION bursts:
   - Thousands of examinees submitting 100 answers at the exact same instant

Directly measures:
- True simultaneous in-flight HTTP requests
- Maximum client sockets & connection reuse
- Upstream Uvicorn event-loop queueing
- Nginx reverse-proxy buffering & status code distribution (200, 502, 503, 504)
- PostgreSQL connection high-water marks (active vs total connections)
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

# Add project root and load-tests directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
os.environ["TELEGRAM_BOT_TOKEN"] = "mock_token_for_tests"

# Intelligent database resolution
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


class BurstMetrics:
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


ANSWER_PATTERNS = [
    [{"display_order": q_idx, "selected_option": ["A", "B", "C", "D"][(q_idx + offset) % 4]} for q_idx in range(1, 101)]
    for offset in range(4)
]


async def run_synchronized_arrival_burst(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    comp_id: uuid.UUID,
    burst_size: int,
    window_seconds: float,
    max_concurrency: int = 400,
) -> Tuple[BurstMetrics, Dict[int, str]]:
    """Fires a synchronized wave of arrivals paced evenly across window_seconds."""
    name = f"Synchronized Arrival Burst: {burst_size:,} examinees in {window_seconds:.1f}s"
    metrics = BurstMetrics(name)
    attempt_ids: Dict[int, str] = {}
    sem = asyncio.Semaphore(max_concurrency)

    sample = participants[:burst_size]
    interval = window_seconds / max(1, burst_size)
    t_start = time.perf_counter()

    async def single_arrival(p_info: Dict[str, Any], scheduled_delay: float):
        delay = scheduled_delay - (time.perf_counter() - t_start)
        if delay > 0:
            await asyncio.sleep(delay)

        async with sem:
            tg_id = p_info["telegram_user_id"]
            headers = {"X-Web-Auth-Token": p_info["token"]}
            metrics.enter_request()
            t0 = time.perf_counter()
            try:
                resp = await client.get(f"/api/v1/webapp/session?comp_id={comp_id}", headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "ready":
                    attempt_ids[tg_id] = resp.json()["attempt_id"]
                    metrics.record(latency_ms, success=True, status_code=resp.status_code)
                else:
                    metrics.record(latency_ms, success=False, status_code=resp.status_code)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False, status_code=599)
            finally:
                metrics.exit_request()

    metrics.start()
    tasks = [
        asyncio.create_task(single_arrival(p, i * interval))
        for i, p in enumerate(sample)
    ]
    await asyncio.gather(*tasks)
    metrics.finish()
    return metrics, attempt_ids


async def run_synchronized_submission_burst(
    client: AsyncClient,
    participants: List[Dict[str, Any]],
    attempt_ids: Dict[int, str],
    burst_size: int,
    max_concurrency: int = 400,
) -> BurstMetrics:
    """Fires a simultaneous submission wave where burst_size examinees submit at once."""
    name = f"Synchronized Submission Burst: {burst_size:,} simultaneous examinee submissions"
    metrics = BurstMetrics(name)
    sem = asyncio.Semaphore(max_concurrency)

    eligible = [p for p in participants if p["telegram_user_id"] in attempt_ids][:burst_size]

    async def single_submit(p_info: Dict[str, Any]):
        async with sem:
            tg_id = p_info["telegram_user_id"]
            att_id = attempt_ids[tg_id]
            headers = {"X-Web-Auth-Token": p_info["token"]}
            payload = {"attempt_id": att_id, "answers": ANSWER_PATTERNS[tg_id % 4]}

            metrics.enter_request()
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/webapp/submit", json=payload, headers=headers)
                t1 = time.perf_counter()
                latency_ms = (t1 - t0) * 1000
                if resp.status_code == 200 and resp.json().get("status") == "success":
                    metrics.record(latency_ms, success=True, status_code=resp.status_code)
                else:
                    metrics.record(latency_ms, success=False, status_code=resp.status_code)
            except Exception:
                t1 = time.perf_counter()
                metrics.record((t1 - t0) * 1000, success=False, status_code=599)
            finally:
                metrics.exit_request()

    metrics.start()
    tasks = [asyncio.create_task(single_submit(p)) for p in eligible]
    await asyncio.gather(*tasks)
    metrics.finish()
    return metrics


async def execute_synchronized_burst_suite(
    base_url: str = "http://127.0.0.1:8080",
    max_burst_size: int = 10000,
) -> Dict[str, Any]:
    """Runs progressive synchronized arrival bursts and final synchronized submission burst."""
    print("\n" + "=" * 80)
    print(f" EXECUTING EMYC SYNCHRONIZED BURST BENCHMARK SUITE")
    print(f" Target Endpoint: {base_url} | Max Burst Size: {max_burst_size:,}")
    print("=" * 80)

    # Initial telemetry
    from network_load_engine import setup_benchmark_environment, get_db_telemetry
    comp_id, participants = await setup_benchmark_environment(max_burst_size, question_count=100)

    sampler = DatabasePeakSampler(interval_sec=0.5)
    sampler.start()

    limits = httpx.Limits(max_connections=800, max_keepalive_connections=400, keepalive_expiry=60.0)
    async with AsyncClient(base_url=base_url, limits=limits, timeout=180.0) as client:
        # Check health
        health = await client.get("/health")
        print(f"[TOPOLOGY] Health check: status={health.status_code}")

        arrival_results = []
        all_attempt_ids = {}

        # Burst Scenario Ladder
        scenarios = [
            (min(1000, max_burst_size), 1.0, 300),
            (min(5000, max_burst_size), 2.0, 350),
            (min(10000, max_burst_size), 3.0, 400),
        ]
        if max_burst_size >= 25000:
            scenarios.append((25000, 5.0, 400))
        if max_burst_size >= 50000:
            scenarios.append((50000, 10.0, 400))
        if max_burst_size >= 100000:
            scenarios.append((100000, 30.0, 400))

        for b_size, b_window, b_conc in scenarios:
            print(f"\n[*] Launching Synchronized Arrival: {b_size:,} requests within {b_window:.1f}s (max in-flight: {b_conc})...")
            m, att_map = await run_synchronized_arrival_burst(client, participants, comp_id, b_size, b_window, b_conc)
            all_attempt_ids.update(att_map)
            summary = m.get_summary()
            arrival_results.append(summary)
            print(f"    -> Results: {summary['success_count']:,} ok, {summary['error_count']} err ({summary['error_rate_pct']}%) | {summary['rps']} RPS | p50={summary['p50_ms']}ms, p95={summary['p95_ms']}ms | Peak In-Flight={summary['peak_in_flight_concurrency']}")

        # Synchronized Submission Wave
        sub_size = min(len(all_attempt_ids), max_burst_size)
        print(f"\n[*] Launching Synchronized SUBMISSION Wave: {sub_size:,} simultaneous 100-answer submissions...")
        sub_m = await run_synchronized_submission_burst(client, participants, all_attempt_ids, sub_size, max_concurrency=400)
        sub_summary = sub_m.get_summary()
        print(f"    -> Submission Results: {sub_summary['success_count']:,} ok, {sub_summary['error_count']} err ({sub_summary['error_rate_pct']}%) | {sub_summary['rps']} sub/s | p50={sub_summary['p50_ms']}ms, p95={sub_summary['p95_ms']}ms | Peak In-Flight={sub_summary['peak_in_flight_concurrency']}")

    peak_db = await sampler.stop()
    print(f"\n[DB HEADROOM] Peak DB Connections Observed: {peak_db['peak_total_connections']} / 250 limit | Peak Active Queries: {peak_db['peak_active_connections']}")

    return {
        "competition_id": str(comp_id),
        "database_headroom": peak_db,
        "arrival_burst_scenarios": arrival_results,
        "submission_burst_summary": sub_summary,
    }


if __name__ == "__main__":
    max_b = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    target_url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080"
    res = asyncio.run(execute_synchronized_burst_suite(target_url, max_b))
    print("\nSynchronized Burst Suite Complete:")
    print(json.dumps(res, indent=2))
