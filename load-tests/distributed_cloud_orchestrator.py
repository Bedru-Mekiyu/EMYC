"""EMYC Distributed Cloud Capacity Benchmark Orchestrator.

Executes progressive capacity ladder stages (1k, 10k, 25k, 50k, 75k, 100k)
against an AWS Staging Application Load Balancer endpoint with automated
post-stage mathematical database reconciliation and strict SLA enforcement.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from synchronized_burst_engine import execute_synchronized_burst_suite
from reconcile_db import reconcile_database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger("emyc_cloud_orchestrator")


STAGE_CONFIGURATIONS = {
    "1k": {"scale": 1000, "expected_p95_sec": 5.0, "min_sub_rate": 40.0},
    "10k": {"scale": 10000, "expected_p95_sec": 8.0, "min_sub_rate": 60.0},
    "25k": {"scale": 25000, "expected_p95_sec": 10.0, "min_sub_rate": 80.0},
    "50k": {"scale": 50000, "expected_p95_sec": 12.0, "min_sub_rate": 150.0},
    "75k": {"scale": 75000, "expected_p95_sec": 15.0, "min_sub_rate": 200.0},
    "100k": {"scale": 100000, "expected_p95_sec": 15.0, "min_sub_rate": 250.0},
}


async def run_single_stage(target_url: str, stage_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    scale = config["scale"]
    logger.info("=" * 80)
    logger.info(f" EXECUTING CLOUD STAGE: {stage_name.upper()} ({scale:,} EXAMINEES)")
    logger.info(f" Target Endpoint: {target_url}")
    logger.info("=" * 80)

    # 1. Execute synchronized burst benchmark suite
    t0 = time.perf_counter()
    results = await execute_synchronized_burst_suite(
        base_url=target_url,
        max_burst_size=scale,
    )
    duration = time.perf_counter() - t0
    comp_id = results.get("competition_id")

    # 2. Extract metrics
    sub_summary = results.get("submission_burst_summary", {})
    sub_rate = sub_summary.get("rps", 0.0)
    p50_ms = sub_summary.get("p50_ms", 0.0)
    p95_ms = sub_summary.get("p95_ms", 0.0)
    err_rate = sub_summary.get("error_rate_pct", 0.0)

    logger.info(f"[STAGE METRICS] Throughput: {sub_rate} sub/s | p50: {p50_ms}ms | p95: {p95_ms}ms | Errors: {err_rate}%")

    # 3. Mathematical Database Reconciliation
    reconciled = False
    if comp_id:
        logger.info(f"[*] Running automated database reconciliation for competition {comp_id}...")
        try:
            reconciled = await reconcile_database(target_comp_id=comp_id)
        except Exception as e:
            logger.error(f"Reconciliation error: {e}")
            reconciled = False

    # 4. Evaluate Pass/Fail SLA
    sla_passed = True
    sla_violations = []

    if err_rate > 0.01:
        sla_passed = False
        sla_violations.append(f"HTTP error rate exceeded threshold: {err_rate}% > 0.01%")

    if (p95_ms / 1000.0) > config["expected_p95_sec"]:
        sla_passed = False
        sla_violations.append(f"p95 latency exceeded SLA: {p95_ms / 1000.0:.2f}s > {config['expected_p95_sec']}s")

    if not reconciled:
        sla_passed = False
        sla_violations.append("Database reconciliation invariant check failed")

    stage_verdict = {
        "stage": stage_name,
        "scale": scale,
        "total_duration_sec": round(duration, 2),
        "submission_throughput_sub_per_sec": sub_rate,
        "latency_p50_ms": p50_ms,
        "latency_p95_ms": p95_ms,
        "error_rate_pct": err_rate,
        "reconciliation_passed": reconciled,
        "sla_passed": sla_passed,
        "sla_violations": sla_violations,
    }

    status_str = "PASSED" if sla_passed else "FAILED"
    logger.info(f">>> STAGE {stage_name.upper()} VERDICT: {status_str} <<<")
    if sla_violations:
        for v in sla_violations:
            logger.warning(f"  -> SLA Violation: {v}")

    return stage_verdict


async def main():
    parser = argparse.ArgumentParser(description="EMYC Distributed Cloud Capacity Benchmark Orchestrator")
    parser.add_argument("--target-url", default="http://127.0.0.1:8080", help="Staging ALB target endpoint URL")
    parser.add_argument("--stage", default="1k", choices=["1k", "10k", "25k", "50k", "75k", "100k", "all"], help="Stage to execute")
    parser.add_argument("--worker-id", type=int, default=0, help="0-indexed worker ID for distributed execution")
    parser.add_argument("--total-workers", type=int, default=1, help="Total distributed worker nodes (e.g. 8 for 100k)")
    parser.add_argument("--sync-epoch", type=float, default=0.0, help="Target UTC epoch seconds to fire synchronized burst")
    args = parser.parse_args()

    # NTP Clock Synchronization Check
    if args.sync_epoch > 0:
        now = time.time()
        wait_sec = args.sync_epoch - now
        if wait_sec > 0:
            logger.info(f"[NTP SYNC] Worker {args.worker_id}/{args.total_workers} synchronizing to epoch {args.sync_epoch}. Sleeping {wait_sec:.2f}s...")
            await asyncio.sleep(wait_sec)
            logger.info(f"[NTP SYNC] Sync epoch reached. Firing synchronized wave at {datetime.now(timezone.utc).isoformat()}!")
        else:
            logger.warning(f"[NTP SYNC] Target sync epoch {args.sync_epoch} already passed by {-wait_sec:.2f}s. Firing immediately.")

    stages_to_run = (
        ["1k", "10k", "25k", "50k", "75k", "100k"]
        if args.stage == "all"
        else [args.stage]
    )

    stage_reports = []
    for s_name in stages_to_run:
        cfg = STAGE_CONFIGURATIONS[s_name].copy()
        # Scale partitioned for distributed workers
        if args.total_workers > 1:
            cfg["scale"] = cfg["scale"] // args.total_workers
            logger.info(f"[DISTRIBUTED] Worker {args.worker_id} assigned slice: {cfg['scale']:,} examinees")

        rep = await run_single_stage(args.target_url, s_name, cfg)
        stage_reports.append(rep)
        if not rep["sla_passed"]:
            logger.error(f"[CIRCUIT BREAKER] Stage {s_name} failed SLA. Halting progression to protect environment.")
            break

    print("\n" + "=" * 80)
    print(f" EMYC CLOUD CAPACITY VALIDATION SUMMARY REPORT (Worker {args.worker_id}/{args.total_workers})")
    print("=" * 80)
    print(json.dumps(stage_reports, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
