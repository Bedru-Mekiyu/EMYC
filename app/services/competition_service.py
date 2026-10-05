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
from app.core.config import settings


# Valid state machine transitions
ALLOWED_TRANSITIONS = {
    CompetitionStatus.DRAFT: [CompetitionStatus.SCHEDULED, CompetitionStatus.LIVE, CompetitionStatus.ARCHIVED],
    CompetitionStatus.SCHEDULED: [CompetitionStatus.LIVE, CompetitionStatus.CLOSED, CompetitionStatus.ARCHIVED],
    CompetitionStatus.LIVE: [CompetitionStatus.CLOSED],
    CompetitionStatus.CLOSED: [CompetitionStatus.RESULTS_FINALIZED, CompetitionStatus.ARCHIVED],
    CompetitionStatus.RESULTS_FINALIZED: [CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED],
    CompetitionStatus.PUBLISHED: [CompetitionStatus.ARCHIVED],
    CompetitionStatus.ARCHIVED: [],
}


class CompetitionError(Exception):
    """Base competition domain exception."""
    pass


class CompetitionValidationError(CompetitionError):
    """Raised when competition fails pre-activation validation."""
    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []


class CompetitionNotOpenError(CompetitionError):
    pass


class CompetitionClosedError(CompetitionError):
    pass


class AttemptExpiredError(CompetitionError):
    pass


class AttemptAlreadySubmittedError(CompetitionError):
    pass


class DuplicateAttemptError(CompetitionError):
    pass


class UnauthorizedAttemptAccessError(CompetitionError):
    """Raised when a participant attempts to access or manipulate another participant's attempt."""
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
    async def validate_competition_for_live(db: AsyncSession, comp: Competition) -> None:
        """Strict pre-activation validation for competitions transitioning to LIVE status.
        
        Guarantees that a competition cannot go LIVE unless questions, schedule,
        and options are completely valid and verified.
        """
        errors = []

        # 1. Schedule checks
        if not comp.opens_at:
            errors.append("Competition opens_at is missing")
        if not comp.closes_at:
            errors.append("Competition closes_at is missing")
        if comp.opens_at and comp.closes_at:
            opens_utc = ensure_utc(comp.opens_at)
            closes_utc = ensure_utc(comp.closes_at)
            if opens_utc >= closes_utc:
                errors.append(f"opens_at ({opens_utc}) must be strictly earlier than closes_at ({closes_utc})")

        # 2. Duration and question count
        if (comp.duration_minutes or 0) <= 0:
            errors.append(f"duration_minutes must be > 0, got {comp.duration_minutes}")
        if (comp.question_count or 0) <= 0:
            errors.append(f"question_count must be > 0, got {comp.question_count}")

        # 3. Status checks
        if comp.status in [CompetitionStatus.CLOSED, CompetitionStatus.RESULTS_FINALIZED, CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
            errors.append(f"Cannot activate competition that is already {comp.status}")


        # 4. Fetch questions from database
        q_stmt = (
            select(CompetitionQuestion)
            .where(CompetitionQuestion.competition_id == comp.id)
            .order_by(CompetitionQuestion.order_index)
        )
        q_res = await db.execute(q_stmt)
        questions = list(q_res.scalars().all())

        if not questions and comp.question_count > 0:
            errors.append(
                f"Question count mismatch: configured {comp.question_count} questions, but found {len(questions)} in database"
            )
        elif not questions and (comp.question_count or 0) <= 0:
            errors.append("No questions found in database for this competition. Please insert questions into Supabase first.")
        else:
            comp.question_count = len(questions)

        seen_orders = set()
        required_keys = {"A", "B", "C", "D"}

        for idx, q in enumerate(questions, start=1):
            # Check question order uniqueness
            if q.order_index in seen_orders:
                errors.append(f"Duplicate question order_index {q.order_index} on question '{q.question_text[:30]}'")
            seen_orders.add(q.order_index)

            # Check question text
            if not (q.question_text and q.question_text.strip()):
                errors.append(f"Question #{q.order_index} has empty question_text")

            # Check options: exactly 4 options with non-empty string values
            if not isinstance(q.options, dict):
                errors.append(f"Question #{q.order_index} options must be a dictionary")
            else:
                if set(q.options.keys()) != required_keys:
                    errors.append(f"Question #{q.order_index} must have exactly options A, B, C, D (found {set(q.options.keys())})")
                for key in required_keys:
                    val = q.options.get(key)
                    if not (val and str(val).strip()):
                        errors.append(f"Question #{q.order_index} option {key} is empty")

            # Check correct_option
            if q.correct_option not in required_keys:
                errors.append(f"Question #{q.order_index} correct_option '{q.correct_option}' is invalid (must be one of A, B, C, D)")

        if errors:
            raise CompetitionValidationError(
                f"Competition cannot go LIVE due to {len(errors)} validation failure(s): {'; '.join(errors)}",
                errors=errors,
            )

    @staticmethod
    async def update_status(
        db: AsyncSession,
        competition_id: uuid.UUID,
        new_status: CompetitionStatus,
        admin_id: str = "admin",
        early_closure_policy: Optional[str] = None,
    ) -> Competition:
        """Transitions competition status following strict lifecycle rules, validation, and row-level locking."""
        now = now_utc()
        stmt = select(Competition).where(Competition.id == competition_id).with_for_update()
        res = await db.execute(stmt)
        comp = res.scalar_one_or_none()
        if not comp:
            raise CompetitionError("Competition not found")

        current_status = comp.status
        if new_status not in ALLOWED_TRANSITIONS.get(current_status, []):
            raise CompetitionError(
                f"Invalid transition from {current_status} to {new_status}"
            )

        # Pre-activation validation
        if new_status == CompetitionStatus.LIVE:
            if not comp.opens_at or comp.opens_at > now or comp.closes_at <= now:
                comp.opens_at = now
                comp.closes_at = now + timedelta(minutes=comp.duration_minutes or 60)
            await CompetitionService.validate_competition_for_live(db, comp)

        # Handle manual early closure when transitioning from LIVE to CLOSED before closes_at
        if current_status == CompetitionStatus.LIVE and new_status == CompetitionStatus.CLOSED:
            closes_utc = ensure_utc(comp.closes_at)
            policy = early_closure_policy or getattr(settings, "MANUAL_EARLY_CLOSURE_POLICY", "truncate_to_close_time")
            is_early_close = now < closes_utc

            if is_early_close and policy == "truncate_to_close_time":
                # Truncate all in-progress attempts' deadlines to now and auto-submit with skip locked
                active_stmt = select(ExamAttempt).where(
                    and_(
                        ExamAttempt.competition_id == comp.id,
                        ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                    )
                ).with_for_update(skip_locked=True)
                res = await db.execute(active_stmt)
                for att in res.scalars().all():
                    att.deadline_at = now
                    await CompetitionService.auto_submit_expired_attempt(db, att)

            log_audit_event("COMPETITION_MANUALLY_CLOSED", "ADMIN", admin_id, {
                "competition_id": str(competition_id),
                "is_early_close": is_early_close,
                "policy": policy,
            })

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
    async def check_and_auto_close_competitions(db: AsyncSession) -> int:
        """Automatically transitions LIVE competitions to CLOSED when closing time is reached."""
        now = now_utc()
        stmt = select(Competition).where(
            and_(
                Competition.status == CompetitionStatus.LIVE,
                Competition.closes_at <= now,
            )
        ).with_for_update(skip_locked=True)
        res = await db.execute(stmt)
        competitions = list(res.scalars().all())

        closed_count = 0
        for comp in competitions:
            comp.status = CompetitionStatus.CLOSED
            # Sweep remaining active attempts
            active_stmt = select(ExamAttempt).where(
                and_(
                    ExamAttempt.competition_id == comp.id,
                    ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                )
            ).with_for_update(skip_locked=True)
            act_res = await db.execute(active_stmt)
            for att in act_res.scalars().all():
                await CompetitionService.auto_submit_expired_attempt(db, att, commit=False)

            log_audit_event("COMPETITION_AUTO_CLOSED", "SYSTEM", "backend", {
                "competition_id": str(comp.id),
                "closed_at": now.isoformat(),
            })
            closed_count += 1

        if closed_count > 0:
            await db.commit()
        return closed_count

    @staticmethod
    async def get_active_competition(db: AsyncSession) -> Optional[Competition]:
        """Returns current LIVE competition within schedule window, executing automatic closure sweep first."""
        # 1. Check and automatically close any competitions that reached their closes_at time
        await CompetitionService.check_and_auto_close_competitions(db)

        now = now_utc()
        stmt = (
            select(Competition)
            .where(
                and_(
                    Competition.status == CompetitionStatus.LIVE,
                    Competition.opens_at <= now,
                    Competition.closes_at > now,
                )
            )
            .order_by(Competition.opens_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def start_attempt(
        db: AsyncSession,
        competition_id: uuid.UUID,
        participant_id: uuid.UUID,
        now_override: Optional[datetime] = None,
    ) -> ExamAttempt:
        """Starts an exam attempt for a participant, enforcing schedule, deadlines, and randomization."""
        now = ensure_utc(now_override) if now_override else now_utc()
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
        try:
            await db.flush()
        except Exception:
            await db.rollback()
            raise DuplicateAttemptError("Participant already has an official attempt for this competition")

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

        try:
            await db.commit()
            await db.refresh(attempt)
        except Exception:
            await db.rollback()
            raise DuplicateAttemptError("Participant already has an official attempt for this competition")

        log_audit_event("ATTEMPT_STARTED", "PARTICIPANT", str(participant_id), {
            "attempt_id": str(attempt.id),
            "competition_id": str(competition_id),
            "deadline_at": deadline_at.isoformat(),
        })
        return attempt

    @staticmethod
    async def get_question_for_attempt(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        display_order: int,
        participant_id: Optional[uuid.UUID] = None,
    ) -> Dict:
        """Retrieves a single question for an attempt with randomized options mapped, zero answer leaks."""
        now = now_utc()
        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt:
            raise CompetitionError("Attempt not found")

        if participant_id and attempt.participant_id != participant_id:
            raise UnauthorizedAttemptAccessError("Attempt does not belong to this participant")

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
        question_id: Optional[uuid.UUID] = None,
        selected_display_option: str = "A",
        participant_id: Optional[uuid.UUID] = None,
        display_order: Optional[int] = None,
    ) -> Dict:
        """Idempotently records an answer, resolves canonical mapping and correctness server-side."""
        now = now_utc()
        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt:
            raise CompetitionError("Attempt not found")

        if participant_id and attempt.participant_id != participant_id:
            raise UnauthorizedAttemptAccessError("Attempt does not belong to this participant")

        if attempt.status != AttemptStatus.IN_PROGRESS:
            raise AttemptAlreadySubmittedError("Attempt is already closed or submitted")

        deadline = ensure_utc(attempt.deadline_at)
        if now > deadline:
            await CompetitionService.auto_submit_expired_attempt(db, attempt)
            raise AttemptExpiredError("Exam deadline has passed")

        # Retrieve mapping for this question by question_id or display_order
        where_clause = [AttemptQuestionOrder.attempt_id == attempt_id]
        if question_id is not None:
            where_clause.append(AttemptQuestionOrder.question_id == question_id)
        elif display_order is not None:
            where_clause.append(AttemptQuestionOrder.display_order == display_order)
        else:
            raise CompetitionError("Either question_id or display_order must be provided")

        mapping_stmt = (
            select(AttemptQuestionOrder)
            .options(selectinload(AttemptQuestionOrder.question))
            .where(and_(*where_clause))
        )
        m_res = await db.execute(mapping_stmt)
        order_entry = m_res.scalar_one_or_none()
        if not order_entry:
            raise CompetitionError("Question does not belong to this attempt")

        resolved_q_id = order_entry.question_id
        q = order_entry.question

        # Check if answer already exists (idempotency)
        existing_stmt = select(ParticipantAnswer).where(
            and_(
                ParticipantAnswer.attempt_id == attempt_id,
                ParticipantAnswer.question_id == resolved_q_id,
            )
        )
        res = await db.execute(existing_stmt)
        existing_answer = res.scalar_one_or_none()
        if existing_answer:
            if existing_answer.selected_display_option == selected_display_option:
                return {
                    "status": "already_recorded",
                    "selected_display_option": existing_answer.selected_display_option,
                }
            canonical_option = order_entry.option_mapping.get(selected_display_option)
            if not canonical_option:
                raise CompetitionError(f"Invalid option selection: {selected_display_option}")
            existing_answer.selected_display_option = selected_display_option
            existing_answer.resolved_canonical_option = canonical_option
            existing_answer.is_correct = (canonical_option == q.correct_option)
            existing_answer.answered_at = now
            await db.commit()
            return {
                "status": "updated",
                "selected_display_option": selected_display_option,
            }

        canonical_option = order_entry.option_mapping.get(selected_display_option)
        if not canonical_option:
            raise CompetitionError(f"Invalid option selection: {selected_display_option}")

        is_correct = (canonical_option == q.correct_option)

        answer = ParticipantAnswer(
            attempt_id=attempt_id,
            question_id=resolved_q_id,
            selected_display_option=selected_display_option,
            resolved_canonical_option=canonical_option,
            is_correct=is_correct,
            answered_at=now,
        )
        db.add(answer)
        try:
            await db.commit()
            return {
                "status": "recorded",
                "selected_display_option": selected_display_option,
            }
        except Exception:
            await db.rollback()
            # Concurrency race handled gracefully: check if answer was recorded by racing task
            scalar_stmt = (
                select(ParticipantAnswer.selected_display_option)
                .where(
                    ParticipantAnswer.attempt_id == attempt_id,
                    ParticipantAnswer.question_id == resolved_q_id,
                )
            )
            res_retry = await db.execute(scalar_stmt)
            recorded_opt = res_retry.scalar_one_or_none()
            return {
                "status": "already_recorded",
                "selected_display_option": recorded_opt if recorded_opt is not None else selected_display_option,
            }

    @staticmethod
    async def submit_attempt(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        participant_id: Optional[uuid.UUID] = None,
    ) -> ExamAttempt:
        """Explicitly submits the attempt with row-level lock against concurrent sweeper races."""
        now = now_utc()
        stmt = select(ExamAttempt).where(ExamAttempt.id == attempt_id).with_for_update()
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            raise CompetitionError("Attempt not found")

        if participant_id and attempt.participant_id != participant_id:
            raise UnauthorizedAttemptAccessError("Attempt does not belong to this participant")

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
    async def auto_submit_expired_attempt(
        db: AsyncSession, attempt: ExamAttempt, commit: bool = True
    ) -> ExamAttempt:
        """Internal helper to automatically submit an expired attempt at deadline."""
        if attempt.status != AttemptStatus.IN_PROGRESS:
            return attempt

        deadline = ensure_utc(attempt.deadline_at)
        started = ensure_utc(attempt.started_at)

        attempt.status = AttemptStatus.EXPIRED
        attempt.submitted_at = deadline
        attempt.completion_seconds = (deadline - started).total_seconds()
        if commit:
            await db.commit()
            await db.refresh(attempt)
        log_audit_event("ATTEMPT_AUTO_SUBMITTED_EXPIRED", "SYSTEM", str(attempt.id), {
            "deadline_at": deadline.isoformat()
        })
        return attempt

    @staticmethod
    async def sweep_expired_attempts(db: AsyncSession) -> int:
        """Background maintenance: sweeps all expired attempts with row-level locks."""
        now = now_utc()
        stmt = (
            select(ExamAttempt)
            .where(
                and_(
                    ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                    ExamAttempt.deadline_at < now,
                )
            )
            .with_for_update(skip_locked=True)
        )
        res = await db.execute(stmt)
        expired_attempts = list(res.scalars().all())

        count = 0
        for att in expired_attempts:
            await CompetitionService.auto_submit_expired_attempt(db, att, commit=False)
            count += 1

        if count > 0:
            await db.commit()
        return count
