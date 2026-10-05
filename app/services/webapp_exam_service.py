"""High-throughput examination engine for Telegram Mini App.

Handles one-shot question payload generation, local-first exam sessions,
and single-transaction batch answer submission for massive scale (100,000+ users).
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from sqlalchemy import select, and_, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time_utils import now_utc, ensure_utc
from app.core.logging import logger, log_audit_event
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus, AttemptQuestionOrder, ParticipantAnswer
from app.services.competition_service import (
    CompetitionService,
    CompetitionError,
    CompetitionNotOpenError,
    DuplicateAttemptError,
    AttemptExpiredError,
    UnauthorizedAttemptAccessError,
)
from app.services.scoring_service import ScoringAndRankingService


class WebAppExamService:
    @staticmethod
    async def get_or_create_session(
        db: AsyncSession,
        telegram_user_id: int,
    ) -> Dict[str, Any]:
        """Prepares or resumes an exam session for a student in a single low-latency query.
        
        Returns all questions and attempt state in ONE payload so the student's phone
        can execute the entire exam locally with zero ongoing server requests.
        """
        now = now_utc()

        # 1. Lookup registered participant
        part_stmt = select(Participant).where(Participant.telegram_user_id == telegram_user_id)
        p_res = await db.execute(part_stmt)
        participant = p_res.scalar_one_or_none()
        if not participant:
            return {
                "status": "unregistered",
                "message": "Participant must verify membership and register first.",
            }

        # 2. Check for LIVE competition
        comp = await CompetitionService.get_active_competition(db)
        if not comp:
            scheduled_comp = await CompetitionService.get_open_or_scheduled_competition(db)
            if scheduled_comp:
                return {
                    "status": "scheduled",
                    "competition_title": scheduled_comp.title,
                    "opens_at": scheduled_comp.opens_at.isoformat() if scheduled_comp.opens_at else None,
                    "closes_at": scheduled_comp.closes_at.isoformat() if scheduled_comp.closes_at else None,
                    "duration_minutes": scheduled_comp.duration_minutes,
                    "question_count": scheduled_comp.question_count,
                    "message": "The competition is open, but the examination session has not started yet.",
                }
            return {
                "status": "not_live",
                "message": "No active competition session found at this time.",
            }

        # 3. Check for existing attempt
        att_stmt = (
            select(ExamAttempt)
            .where(
                and_(
                    ExamAttempt.competition_id == comp.id,
                    ExamAttempt.participant_id == participant.id,
                )
            )
            .order_by(ExamAttempt.started_at.desc())
            .limit(1)
        )
        existing_attempt = (await db.execute(att_stmt)).scalars().first()

        # Case A: Already submitted or expired
        if existing_attempt and existing_attempt.status in [
            AttemptStatus.SUBMITTED,
            AttemptStatus.EXPIRED,
            AttemptStatus.FINALIZED,
        ]:
            return {
                "status": "already_submitted",
                "attempt_id": str(existing_attempt.id),
                "submitted_at": existing_attempt.submitted_at.isoformat() if existing_attempt.submitted_at else None,
                "score": existing_attempt.score,
                "total_questions": comp.question_count,
                "competition_title": comp.title,
                "results_published": comp.status == CompetitionStatus.PUBLISHED,
            }

        # Case B: In-progress attempt -> verify deadline
        if existing_attempt and existing_attempt.status == AttemptStatus.IN_PROGRESS:
            deadline = ensure_utc(existing_attempt.deadline_at)
            if now > deadline:
                await CompetitionService.auto_submit_expired_attempt(db, existing_attempt)
                return {
                    "status": "already_submitted",
                    "attempt_id": str(existing_attempt.id),
                    "submitted_at": existing_attempt.submitted_at.isoformat() if existing_attempt.submitted_at else None,
                    "score": existing_attempt.score,
                    "total_questions": comp.question_count,
                    "competition_title": comp.title,
                    "results_published": comp.status == CompetitionStatus.PUBLISHED,
                }
            attempt = existing_attempt
        else:
            # Case C: Start new attempt
            attempt = await CompetitionService.start_attempt(db, comp.id, participant.id)

        # 4. Fetch all questions in student's randomized order in a single query
        orders_stmt = (
            select(AttemptQuestionOrder)
            .options(selectinload(AttemptQuestionOrder.question))
            .where(AttemptQuestionOrder.attempt_id == attempt.id)
            .order_by(AttemptQuestionOrder.display_order.asc())
        )
        orders = list((await db.execute(orders_stmt)).scalars().all())

        # 5. Fetch any already answered questions
        ans_stmt = select(ParticipantAnswer).where(ParticipantAnswer.attempt_id == attempt.id)
        saved_answers_list = list((await db.execute(ans_stmt)).scalars().all())
        saved_answers = {
            str(a.question_id): a.selected_display_option for a in saved_answers_list
        }

        # 6. Build the client-ready questions array
        questions_payload = []
        for order in orders:
            q = order.question
            canonical_options = q.options or {}
            # Map canonical option letters to randomized display letters (A, B, C, D)
            displayed_options = {}
            for display_letter, canon_letter in order.option_mapping.items():
                displayed_options[display_letter] = canonical_options.get(canon_letter, "")

            questions_payload.append({
                "display_order": order.display_order,
                "question_id": str(q.id),
                "question_text": q.question_text,
                "options": displayed_options,
            })

        deadline_utc = ensure_utc(attempt.deadline_at)
        time_left_seconds = max(0, int((deadline_utc - now).total_seconds()))

        return {
            "status": "ready",
            "competition_id": str(comp.id),
            "competition_title": comp.title,
            "attempt_id": str(attempt.id),
            "participant_name": participant.full_name,
            "deadline_at": deadline_utc.isoformat(),
            "time_left_seconds": time_left_seconds,
            "duration_minutes": comp.duration_minutes,
            "total_questions": len(questions_payload),
            "questions": questions_payload,
            "saved_answers": saved_answers,
        }

    @staticmethod
    async def submit_batch_answers(
        db: AsyncSession,
        telegram_user_id: int,
        attempt_id: uuid.UUID,
        answers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Submits all exam answers in ONE single atomic database transaction.
        
        Zero lock contention: accepts batch payload, verifies answers server-side,
        computes score, and finalizes attempt status.
        """
        now = now_utc()

        # 1. Authorize attempt belongs to this telegram user
        stmt = (
            select(ExamAttempt)
            .options(selectinload(ExamAttempt.participant), selectinload(ExamAttempt.competition))
            .where(ExamAttempt.id == attempt_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            raise CompetitionError("Attempt not found")

        if attempt.participant.telegram_user_id != telegram_user_id:
            raise UnauthorizedAttemptAccessError("Attempt does not belong to this participant")

        if attempt.status in [AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED, AttemptStatus.FINALIZED]:
            return {
                "status": "already_submitted",
                "attempt_id": str(attempt.id),
                "score": attempt.score,
                "total_questions": attempt.competition.question_count,
            }

        # 2. Fetch question orders and option mappings for this attempt
        q_order_stmt = (
            select(AttemptQuestionOrder)
            .options(selectinload(AttemptQuestionOrder.question))
            .where(AttemptQuestionOrder.attempt_id == attempt.id)
        )
        q_orders = list((await db.execute(q_order_stmt)).scalars().all())
        q_order_map = {str(qo.question_id): qo for qo in q_orders}
        display_order_map = {qo.display_order: qo for qo in q_orders}

        # 3. Process answers in a batch with zero lock contention
        correct_count = 0
        incorrect_count = 0
        persisted_answers_summary = {}
        participant_answers_to_insert = []

        for item in answers:
            # Match by question_id or display_order
            q_id_str = item.get("question_id")
            disp_order = item.get("display_order")
            selected_display_opt = str(item.get("selected_option", "")).strip().upper()

            if not selected_display_opt:
                continue

            qo = None
            if q_id_str and q_id_str in q_order_map:
                qo = q_order_map[q_id_str]
            elif disp_order and disp_order in display_order_map:
                qo = display_order_map[disp_order]

            if not qo:
                continue

            q = qo.question
            canonical_opt = qo.option_mapping.get(selected_display_opt)
            if not canonical_opt:
                continue

            is_correct = (canonical_opt == q.correct_option)
            if is_correct:
                correct_count += 1
            else:
                incorrect_count += 1

            persisted_answers_summary[str(q.id)] = {
                "display_order": qo.display_order,
                "selected_display": selected_display_opt,
                "canonical": canonical_opt,
                "is_correct": is_correct,
            }

            participant_answers_to_insert.append(
                ParticipantAnswer(
                    attempt_id=attempt.id,
                    question_id=q.id,
                    selected_display_option=selected_display_opt,
                    resolved_canonical_option=canonical_opt,
                    is_correct=is_correct,
                    answered_at=now,
                )
            )

        # Remove any previous answers for idempotent retry safety and bulk insert
        await db.execute(delete(ParticipantAnswer).where(ParticipantAnswer.attempt_id == attempt.id))
        if participant_answers_to_insert:
            db.add_all(participant_answers_to_insert)

        # 4. Finalize attempt in database
        started = ensure_utc(attempt.started_at)
        completion_seconds = max(0.0, (now - started).total_seconds())

        attempt.status = AttemptStatus.SUBMITTED
        attempt.submitted_at = now
        attempt.completion_seconds = completion_seconds
        attempt.score = correct_count
        attempt.correct_count = correct_count
        attempt.incorrect_count = incorrect_count
        attempt.answers_summary = persisted_answers_summary

        await db.commit()
        await db.refresh(attempt)

        log_audit_event("WEBAPP_EXAM_SUBMITTED", "PARTICIPANT", str(attempt.participant_id), {
            "attempt_id": str(attempt.id),
            "score": correct_count,
            "completion_seconds": completion_seconds,
        })

        return {
            "status": "success",
            "attempt_id": str(attempt.id),
            "score": correct_count,
            "correct_count": correct_count,
            "incorrect_count": incorrect_count,
            "total_questions": attempt.competition.question_count,
            "completion_seconds": completion_seconds,
            "submitted_at": now.isoformat(),
        }
