import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select, update, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import (
    ExamAttempt,
    AttemptStatus,
    AttemptQuestionOrder,
    ParticipantAnswer,
)
from app.core.logging import logger, log_audit_event
from app.core.time_utils import now_utc, ensure_utc


# Valid state machine transitions
ALLOWED_TRANSITIONS = {
    CompetitionStatus.DRAFT: [CompetitionStatus.SCHEDULED, CompetitionStatus.LIVE],
    CompetitionStatus.SCHEDULED: [CompetitionStatus.LIVE, CompetitionStatus.CLOSED],
    CompetitionStatus.LIVE: [CompetitionStatus.CLOSED],
    CompetitionStatus.CLOSED: [CompetitionStatus.RESULTS_FINALIZED],
    CompetitionStatus.RESULTS_FINALIZED: [CompetitionStatus.PUBLISHED],
    CompetitionStatus.PUBLISHED: [],
}


class CompetitionError(Exception):
    """Base competition domain exception."""
    pass


class CompetitionNotOpenError(CompetitionError):
    pass


class AttemptExpiredError(CompetitionError):
    pass


class AttemptAlreadySubmittedError(CompetitionError):
    pass


class DuplicateAttemptError(CompetitionError):
    pass


class CompetitionService:
    @staticmethod
    async def create_competition(
        db: AsyncSession,
        title: str,
        opens_at: datetime,
        closes_at: datetime,
        duration_minutes: int = 120,
        question_count: int = 100,
        description: Optional[str] = None,
        status: CompetitionStatus = CompetitionStatus.DRAFT,
    ) -> Competition:
        """Creates a new competition."""
        if opens_at >= closes_at:
            raise ValueError("Competition opens_at must be before closes_at")

        competition = Competition(
            title=title,
            description=description,
            status=status,
            opens_at=opens_at,
            closes_at=closes_at,
            duration_minutes=duration_minutes,
            question_count=question_count,
        )
        db.add(competition)
        await db.commit()
        await db.refresh(competition)
        log_audit_event("COMPETITION_CREATED", "ADMIN", "system", {"id": str(competition.id), "title": title})
        return competition

    @staticmethod
    async def update_status(
        db: AsyncSession, competition_id: uuid.UUID, new_status: CompetitionStatus, admin_id: str = "admin"
    ) -> Competition:
        """Transitions competition status following strict lifecycle rules."""
        comp = await db.get(Competition, competition_id)
        if not comp:
            raise CompetitionError("Competition not found")

        current_status = comp.status
        if new_status not in ALLOWED_TRANSITIONS.get(current_status, []):
            raise CompetitionError(
                f"Invalid transition from {current_status} to {new_status}"
            )

        comp.status = new_status
        await db.commit()
        await db.refresh(comp)
        log_audit_event("COMPETITION_STATUS_UPDATED", "ADMIN", admin_id, {
            "competition_id": str(competition_id),
            "from": current_status,
            "to": new_status,
        })
        return comp

    @staticmethod
    async def get_active_competition(db: AsyncSession) -> Optional[Competition]:
        """Returns current LIVE competition within schedule window."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(Competition)
            .where(
                and_(
                    Competition.status == CompetitionStatus.LIVE,
                    Competition.opens_at <= now,
                    Competition.closes_at >= now,
                )
            )
            .order_by(Competition.opens_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def start_attempt(
        db: AsyncSession, competition_id: uuid.UUID, participant_id: uuid.UUID
    ) -> ExamAttempt:
        """Starts an exam attempt for a participant, enforcing schedule, deadlines, and randomization."""
        now = now_utc()
        comp = await db.get(Competition, competition_id)
        if not comp:
            raise CompetitionError("Competition not found")

        comp_opens = ensure_utc(comp.opens_at)
        comp_closes = ensure_utc(comp.closes_at)

        # 1. Authoritative server-side schedule check
        if comp.status != CompetitionStatus.LIVE:
            raise CompetitionNotOpenError(f"Competition status is {comp.status}, not LIVE")
        if now < comp_opens:
            raise CompetitionNotOpenError(f"Competition does not open until {comp.opens_at}")
        if now > comp_closes:
            raise CompetitionNotOpenError(f"Competition closed at {comp.closes_at}")

        # 2. Check for existing attempt
        existing_stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == competition_id,
                ExamAttempt.participant_id == participant_id,
            )
        )
        existing_result = await db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        if existing:
            raise DuplicateAttemptError("Participant already has an official attempt for this competition")

        # 3. Calculate deadline = min(started_at + duration, competition.closes_at)
        normal_deadline = now + timedelta(minutes=comp.duration_minutes)
        deadline_at = min(normal_deadline, comp_closes)

        attempt = ExamAttempt(
            competition_id=competition_id,
            participant_id=participant_id,
            status=AttemptStatus.IN_PROGRESS,
            started_at=now,
            deadline_at=deadline_at,
        )
        db.add(attempt)
        await db.flush()

        # 4. Fetch competition questions
        q_stmt = (
            select(CompetitionQuestion)
            .where(CompetitionQuestion.competition_id == competition_id)
            .order_by(CompetitionQuestion.order_index)
        )
        q_res = await db.execute(q_stmt)
        questions = list(q_res.scalars().all())

        if not questions:
            raise CompetitionError("No questions configured for this competition")

        # 5. Question order randomization per participant
        shuffled_questions = list(questions)
        random.shuffle(shuffled_questions)

        # 6. Option randomization per question and mapping persistence
        option_keys = ["A", "B", "C", "D"]
        for display_idx, q in enumerate(shuffled_questions, start=1):
            shuffled_keys = list(option_keys)
            random.shuffle(shuffled_keys)
            # mapping: display letter -> canonical option letter
            mapping = {display_letter: canon_letter for display_letter, canon_letter in zip(option_keys, shuffled_keys)}

            order_entry = AttemptQuestionOrder(
                attempt_id=attempt.id,
                display_order=display_idx,
                question_id=q.id,
                option_mapping=mapping,
            )
            db.add(order_entry)

        await db.commit()
        await db.refresh(attempt)
        log_audit_event("ATTEMPT_STARTED", "PARTICIPANT", str(participant_id), {
            "attempt_id": str(attempt.id),
            "competition_id": str(competition_id),
            "deadline_at": deadline_at.isoformat(),
        })
        return attempt

    @staticmethod
    async def get_question_for_attempt(
        db: AsyncSession, attempt_id: uuid.UUID, display_order: int
    ) -> Dict:
        """Retrieves a single question for an attempt with randomized options mapped, zero answer leaks."""
        now = now_utc()
        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt:
            raise CompetitionError("Attempt not found")

        deadline = ensure_utc(attempt.deadline_at)

        # Expiry check
        if attempt.status == AttemptStatus.IN_PROGRESS and now > deadline:
            await CompetitionService.auto_submit_expired_attempt(db, attempt)
            raise AttemptExpiredError("Exam time has expired")

        stmt = (
            select(AttemptQuestionOrder)
            .options(selectinload(AttemptQuestionOrder.question))
            .where(
                and_(
                    AttemptQuestionOrder.attempt_id == attempt_id,
                    AttemptQuestionOrder.display_order == display_order,
                )
            )
        )
        res = await db.execute(stmt)
        order_entry = res.scalar_one_or_none()
        if not order_entry:
            raise CompetitionError(f"Question {display_order} not found for this attempt")

        q = order_entry.question
        # Canonical options from question: {"A": "text A", "B": "text B", ...}
        canonical_options = q.options
        mapping = order_entry.option_mapping  # {"A": "C", "B": "A", ...}

        # Build randomized options as participant sees them:
        display_options = {}
        for display_letter, canon_letter in mapping.items():
            display_options[display_letter] = canonical_options.get(canon_letter, "")

        # Check if already answered
        ans_stmt = select(ParticipantAnswer).where(
            and_(
                ParticipantAnswer.attempt_id == attempt_id,
                ParticipantAnswer.question_id == q.id,
            )
        )
        ans_res = await db.execute(ans_stmt)
        existing_ans = ans_res.scalar_one_or_none()

        # Count total questions for this attempt
        count_stmt = select(func.count(AttemptQuestionOrder.id)).where(
            AttemptQuestionOrder.attempt_id == attempt_id
        )
        total_q = (await db.execute(count_stmt)).scalar() or 0

        time_left_seconds = max(0, int((deadline - now).total_seconds()))

        return {
            "attempt_id": attempt.id,
            "display_order": display_order,
            "total_questions": total_q,
            "question_id": q.id,
            "question_text": q.question_text,
            "options": display_options,
            "time_left_seconds": time_left_seconds,
            "already_answered_option": existing_ans.selected_display_option if existing_ans else None,
            "status": attempt.status,
        }

    @staticmethod
    async def submit_answer(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        question_id: uuid.UUID,
        selected_display_option: str,
    ) -> Dict:
        """Idempotently records an answer, resolves canonical mapping and correctness server-side."""
        now = now_utc()
        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt:
            raise CompetitionError("Attempt not found")

        if attempt.status != AttemptStatus.IN_PROGRESS:
            raise AttemptAlreadySubmittedError("Attempt is already closed or submitted")

        deadline = ensure_utc(attempt.deadline_at)
        if now > deadline:
            await CompetitionService.auto_submit_expired_attempt(db, attempt)
            raise AttemptExpiredError("Exam deadline has passed")

        # Check if answer already exists (idempotency)
        existing_stmt = select(ParticipantAnswer).where(
            and_(
                ParticipantAnswer.attempt_id == attempt_id,
                ParticipantAnswer.question_id == question_id,
            )
        )
        res = await db.execute(existing_stmt)
        existing_answer = res.scalar_one_or_none()
        if existing_answer:
            return {
                "status": "already_recorded",
                "selected_display_option": existing_answer.selected_display_option,
            }

        # Retrieve mapping for this question
        mapping_stmt = (
            select(AttemptQuestionOrder)
            .options(selectinload(AttemptQuestionOrder.question))
            .where(
                and_(
                    AttemptQuestionOrder.attempt_id == attempt_id,
                    AttemptQuestionOrder.question_id == question_id,
                )
            )
        )
        m_res = await db.execute(mapping_stmt)
        order_entry = m_res.scalar_one_or_none()
        if not order_entry:
            raise CompetitionError("Question does not belong to this attempt")

        q = order_entry.question
        canonical_option = order_entry.option_mapping.get(selected_display_option)
        if not canonical_option:
            raise CompetitionError(f"Invalid option selection: {selected_display_option}")

        is_correct = (canonical_option == q.correct_option)

        answer = ParticipantAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
            selected_display_option=selected_display_option,
            resolved_canonical_option=canonical_option,
            is_correct=is_correct,
            answered_at=now,
        )
        db.add(answer)
        await db.commit()

        # NOTE: Do NOT return is_correct to avoid live correctness feedback
        return {
            "status": "recorded",
            "selected_display_option": selected_display_option,
        }

    @staticmethod
    async def submit_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> ExamAttempt:
        """Explicitly submits the attempt."""
        now = now_utc()
        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt:
            raise CompetitionError("Attempt not found")

        if attempt.status in [AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED, AttemptStatus.FINALIZED]:
            return attempt  # Idempotent

        started = ensure_utc(attempt.started_at)
        attempt.status = AttemptStatus.SUBMITTED
        attempt.submitted_at = now
        attempt.completion_seconds = (now - started).total_seconds()

        await db.commit()
        await db.refresh(attempt)
        log_audit_event("ATTEMPT_SUBMITTED", "PARTICIPANT", str(attempt.participant_id), {
            "attempt_id": str(attempt.id),
            "completion_seconds": attempt.completion_seconds,
        })
        return attempt

    @staticmethod
    async def auto_submit_expired_attempt(db: AsyncSession, attempt: ExamAttempt) -> ExamAttempt:
        """Internal helper to automatically submit an expired attempt at deadline."""
        if attempt.status != AttemptStatus.IN_PROGRESS:
            return attempt

        deadline = ensure_utc(attempt.deadline_at)
        started = ensure_utc(attempt.started_at)

        attempt.status = AttemptStatus.EXPIRED
        attempt.submitted_at = deadline
        attempt.completion_seconds = (deadline - started).total_seconds()
        await db.commit()
        await db.refresh(attempt)
        log_audit_event("ATTEMPT_AUTO_SUBMITTED_EXPIRED", "SYSTEM", str(attempt.id), {
            "deadline_at": deadline.isoformat()
        })
        return attempt

    @staticmethod
    async def sweep_expired_attempts(db: AsyncSession) -> int:
        """Background maintenance: sweeps all expired attempts."""
        now = datetime.now(timezone.utc)
        stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                ExamAttempt.deadline_at < now,
            )
        )
        res = await db.execute(stmt)
        expired_attempts = list(res.scalars().all())

        count = 0
        for att in expired_attempts:
            await CompetitionService.auto_submit_expired_attempt(db, att)
            count += 1
        return count
