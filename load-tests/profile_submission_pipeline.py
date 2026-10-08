import asyncio
import os
import sys
import time
import uuid
import hmac
import hashlib
from typing import Dict, Any, List
import statistics

# Set environment
sys.path.insert(0, ".")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgrespassword@localhost:5434/competition_test_db"

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, engine, Base
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.api.v1.webapp import (
    AnswerItem,
    BatchSubmitRequest,
    verify_web_auth_token,
    create_web_auth_token,
)
from app.services.competition_service import CompetitionService
from app.services.webapp_exam_service import WebAppExamService
from app.core.time_utils import now_utc


async def run_profiling(n_samples: int = 200):
    print("=" * 80)
    print(f" SUBMISSION PIPELINE BOTTLENECK EMPIRICAL PROFILING ({n_samples} SAMPLES)")
    print("=" * 80)

    # 1. Setup isolated test competition with 100 questions
    comp_id = uuid.uuid4()
    now = now_utc()
    from datetime import timedelta
    opens = now - timedelta(hours=1)
    closes = now + timedelta(hours=2)

    async with AsyncSessionLocal() as session:
        # Create schema if not exists
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        comp = Competition(
            id=comp_id,
            title="Bottleneck Profiling Exam",
            description="Diagnostic test",
            status=CompetitionStatus.LIVE,
            opens_at=opens,
            closes_at=closes,
            duration_minutes=60,
            question_count=100,
        )
        session.add(comp)

        # 100 questions
        questions = []
        for i in range(1, 101):
            q = CompetitionQuestion(
                id=uuid.uuid4(),
                competition_id=comp_id,
                order_index=i,
                question_text=f"Sample diagnostic question #{i} for latency profiling?",
                options={"A": "Option Alpha", "B": "Option Beta", "C": "Option Gamma", "D": "Option Delta"},
                correct_option="A",
            )
            session.add(q)
            questions.append(q)

        # Participants and attempts
        participants = []
        attempts = []
        for i in range(n_samples):
            p = Participant(
                id=uuid.uuid4(),
                telegram_user_id=880000000 + i,
                telegram_username=f"profile_user_{i}",
                full_name=f"Profile User {i}",
                membership_id=f"PROFILE/{i:06d}/2026",
            )
            session.add(p)
            participants.append(p)

            # Randomized question sequence
            q_seq = [
                {
                    "question_id": str(q.id),
                    "display_order": q.order_index,
                    "option_mapping": {"A": "A", "B": "B", "C": "C", "D": "D"},
                }
                for q in questions
            ]

            att = ExamAttempt(
                id=uuid.uuid4(),
                competition_id=comp_id,
                participant_id=p.id,
                status=AttemptStatus.IN_PROGRESS,
                started_at=now,
                deadline_at=now + timedelta(minutes=60),
                question_sequence=q_seq,
            )
            session.add(att)
            attempts.append(att)

        await session.commit()
    print("[INIT] Test dataset created: 1 competition, 100 questions, %d participants & attempts" % n_samples)

    # Prepare raw payload dict
    answers_raw = [
        {"display_order": idx, "question_id": str(questions[idx - 1].id), "selected_option": "A"}
        for idx in range(1, 101)
    ]

    pydantic_times = []
    auth_times = []
    db_select_times = []
    scoring_logic_times = []
    db_commit_times = []
    full_service_times = []

    # Warm up question cache
    async with AsyncSessionLocal() as session:
        await CompetitionService.get_cached_competition_questions(session, comp_id)

    print("\n[PROFILING] Executing %d instrumented submissions..." % n_samples)

    for i in range(n_samples):
        att = attempts[i]
        p = participants[i]
        token = create_web_auth_token(p.telegram_user_id, p.telegram_username, p.full_name)

        # Stage 1: Auth token verification (HMAC-SHA256)
        t0 = time.perf_counter()
        user_info = verify_web_auth_token(token)
        t_auth = (time.perf_counter() - t0) * 1000
        auth_times.append(t_auth)

        # Stage 2: Pydantic Parsing & Model Dump
        t0 = time.perf_counter()
        parsed_payload = BatchSubmitRequest(
            attempt_id=str(att.id),
            answers=[AnswerItem(**ans) for ans in answers_raw],
        )
        answers_dict_list = [a.model_dump() for a in parsed_payload.answers]
        t_pydantic = (time.perf_counter() - t0) * 1000
        pydantic_times.append(t_pydantic)

        # Stage 3: Granular breakdown inside service
        async with AsyncSessionLocal() as db:
            # Step 3a: DB SELECT FOR UPDATE + selectinload relations
            t0 = time.perf_counter()
            stmt = (
                select(ExamAttempt)
                .options(selectinload(ExamAttempt.participant), selectinload(ExamAttempt.competition))
                .where(ExamAttempt.id == att.id)
                .with_for_update()
            )
            res = await db.execute(stmt)
            attempt_obj = res.scalar_one_or_none()
            _ = attempt_obj.participant
            _ = attempt_obj.competition
            t_select = (time.perf_counter() - t0) * 1000
            db_select_times.append(t_select)

            # Step 3b: Question cache fetch + in-memory evaluation
            t0 = time.perf_counter()
            cached_qs = await CompetitionService.get_cached_competition_questions(db, attempt_obj.competition_id)
            q_map = {str(q.id): q for q in cached_qs}
            q_order_map = {str(item["question_id"]): item for item in attempt_obj.question_sequence}
            display_order_map = {item["display_order"]: item for item in attempt_obj.question_sequence}

            correct_count = 0
            incorrect_count = 0
            persisted_answers_summary = {}

            for item in answers_dict_list:
                q_id_str = item.get("question_id")
                disp_order = item.get("display_order")
                selected_display_opt = str(item.get("selected_option", "")).strip().upper()

                if not selected_display_opt:
                    continue

                entry = None
                if q_id_str and q_id_str in q_order_map:
                    entry = q_order_map[q_id_str]
                elif disp_order and disp_order in display_order_map:
                    entry = display_order_map[disp_order]

                if not entry:
                    continue

                q = q_map.get(str(entry["question_id"]))
                if not q:
                    continue

                mapping = entry.get("option_mapping", {})
                canonical_opt = mapping.get(selected_display_opt)
                if not canonical_opt:
                    continue

                is_correct = (canonical_opt == q.correct_option)
                if is_correct:
                    correct_count += 1
                else:
                    incorrect_count += 1

                disp_ord = entry["display_order"]
                persisted_answers_summary[str(q.id)] = {
                    "question_id": str(q.id),
                    "display_order": disp_ord,
                    "selected_display": selected_display_opt,
                    "canonical": canonical_opt,
                    "is_correct": is_correct,
                }

            attempt_obj.status = AttemptStatus.SUBMITTED
            attempt_obj.submitted_at = now_utc()
            attempt_obj.score = correct_count
            attempt_obj.correct_count = correct_count
            attempt_obj.incorrect_count = incorrect_count
            attempt_obj.answers_summary = persisted_answers_summary
            t_scoring = (time.perf_counter() - t0) * 1000
            scoring_logic_times.append(t_scoring)

            # Step 3c: DB UPDATE + COMMIT (fsync / WAL write)
            t0 = time.perf_counter()
            await db.commit()
            t_commit = (time.perf_counter() - t0) * 1000
            db_commit_times.append(t_commit)

            full_service_times.append(t_auth + t_pydantic + t_select + t_scoring + t_commit)

    print("\n" + "=" * 80)
    print(" EMPIRICAL LATENCY BREAKDOWN (PER SINGLE SUBMISSION TRANSACTION)")
    print("=" * 80)
    print(f"{'Component / Subsystem':<42} | {'Mean (ms)':<10} | {'p50 (ms)':<10} | {'p95 (ms)':<10} | {'Share (%)':<10}")
    print("-" * 80)

    mean_total = statistics.mean(full_service_times)
    metrics_list = [
        ("1. HMAC Token Auth Verification", auth_times),
        ("2. Pydantic 100-Item Parse + Model Dump", pydantic_times),
        ("3. DB SELECT FOR UPDATE + Relations", db_select_times),
        ("4. In-Memory Answer Evaluation & Mapping", scoring_logic_times),
        ("5. DB UPDATE + COMMIT (PostgreSQL WAL/fsync)", db_commit_times),
    ]

    for name, times in metrics_list:
        m = statistics.mean(times)
        p50 = statistics.median(times)
        sorted_times = sorted(times)
        p95 = sorted_times[int(0.95 * len(sorted_times))]
        share = (m / mean_total) * 100
        print(f"{name:<42} | {m:>8.3f}ms | {p50:>8.3f}ms | {p95:>8.3f}ms | {share:>8.1f}%")

    print("-" * 80)
    total_p50 = statistics.median(full_service_times)
    sorted_tot = sorted(full_service_times)
    total_p95 = sorted_tot[int(0.95 * len(sorted_tot))]
    print(f"{'TOTAL Single-Threaded Processing Time':<42} | {mean_total:>8.3f}ms | {total_p50:>8.3f}ms | {total_p95:>8.3f}ms | {'100.0%':>10}")
    print("=" * 80)

    # Compute theoretical single-worker and cluster throughput
    single_worker_max_rps = 1000.0 / mean_total
    cluster_16_workers_max_rps = single_worker_max_rps * 16
    print(f"\n[ANALYSIS]")
    print(f" -> Mean time per submission: {mean_total:.2f} ms")
    print(f" -> Theoretical max throughput per single worker: {single_worker_max_rps:.1f} submissions/sec")
    print(f" -> Theoretical max cluster throughput (16 workers): {cluster_16_workers_max_rps:.1f} submissions/sec")
    print(f" -> Actual measured cluster throughput in burst test: 48.5 submissions/sec")
    print(f" -> Cluster efficiency factor: {(48.5 / cluster_16_workers_max_rps) * 100:.1f}%\n")


if __name__ == "__main__":
    asyncio.run(run_profiling(200))
