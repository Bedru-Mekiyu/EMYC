import uuid
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.competition import Competition, CompetitionStatus
from app.models.attempt import (
    ExamAttempt,
    AttemptStatus,
    AttemptQuestionOrder,
    ParticipantAnswer,
)
from app.models.question import CompetitionQuestion
from app.services.competition_service import CompetitionService, CompetitionError
from app.core.logging import logger, log_audit_event


class ResultsNotPublishedError(CompetitionError):
    pass


class ScoringAndRankingService:
    @staticmethod
    async def score_attempt(db: AsyncSession, attempt: ExamAttempt) -> Tuple[int, int, int]:
        """Calculates score, correct count, incorrect count for an attempt."""
        # Query all answers for this attempt
        stmt = select(ParticipantAnswer).where(ParticipantAnswer.attempt_id == attempt.id)
        res = await db.execute(stmt)
        answers = list(res.scalars().all())

        correct_count = sum(1 for a in answers if a.is_correct)
        incorrect_count = sum(1 for a in answers if not a.is_correct)
        # 1 point per correct answer, 0 for wrong, no negative marking
        score = correct_count

        attempt.score = score
        attempt.correct_count = correct_count
        attempt.incorrect_count = incorrect_count

        return score, correct_count, incorrect_count

    @staticmethod
    async def finalize_competition_results(
        db: AsyncSession, competition_id: uuid.UUID, admin_id: str = "admin"
    ) -> List[ExamAttempt]:
        """Closes any remaining attempts, computes all scores and deterministic rankings with row lock."""
        stmt = select(Competition).where(Competition.id == competition_id).with_for_update()
        res = await db.execute(stmt)
        comp = res.scalar_one_or_none()
        if not comp:
            raise CompetitionError("Competition not found")

        if comp.status != CompetitionStatus.CLOSED:
            # If competition is still LIVE, transition to CLOSED first
            if comp.status == CompetitionStatus.LIVE:
                await CompetitionService.update_status(
                    db, competition_id, CompetitionStatus.CLOSED, admin_id=admin_id
                )
                # Re-fetch locked row after status update
                res = await db.execute(stmt)
                comp = res.scalar_one_or_none()
            else:
                raise CompetitionError(f"Cannot finalize results when competition status is {comp.status}")

        # 1. Sweep any active or expired attempts
        await CompetitionService.sweep_expired_attempts(db)

        # Force-submit any remaining IN_PROGRESS attempts
        active_stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == competition_id,
                ExamAttempt.status == AttemptStatus.IN_PROGRESS,
            )
        )
        active_res = await db.execute(active_stmt)
        for act in active_res.scalars().all():
            await CompetitionService.auto_submit_expired_attempt(db, act)

        # 2. Fetch all completed attempts (SUBMITTED or EXPIRED)
        attempts_stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == competition_id,
                ExamAttempt.status.in_([AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED, AttemptStatus.FINALIZED]),
            )
        )
        res = await db.execute(attempts_stmt)
        attempts = list(res.scalars().all())

        # 3. Score all attempts
        for attempt in attempts:
            # Compute score
            await ScoringAndRankingService.score_attempt(db, attempt)

        # 4. Deterministic ranking:
        # Sort key:
        # - score descending (-attempt.score)
        # - completion_seconds ascending (attempt.completion_seconds or infinity)
        # - submitted_at ascending (attempt.submitted_at)
        # - attempt.id ascending (deterministic tie-breaker)
        def sort_key(att: ExamAttempt):
            score_val = att.score if att.score is not None else 0
            time_val = att.completion_seconds if att.completion_seconds is not None else 9999999.0
            sub_val = att.submitted_at.isoformat() if att.submitted_at else ""
            return (-score_val, time_val, sub_val, str(att.id))

        attempts.sort(key=sort_key)

        # Assign ranks
        for rank_idx, attempt in enumerate(attempts, start=1):
            attempt.rank = rank_idx
            attempt.status = AttemptStatus.FINALIZED

        # Transition competition status to RESULTS_FINALIZED
        comp.status = CompetitionStatus.RESULTS_FINALIZED
        await db.commit()

        log_audit_event("RESULTS_FINALIZED", "ADMIN", admin_id, {
            "competition_id": str(competition_id),
            "finalized_attempts_count": len(attempts),
        })
        return attempts

    @staticmethod
    async def publish_results(
        db: AsyncSession, competition_id: uuid.UUID, admin_id: str = "admin"
    ) -> Competition:
        """Publishes official results, making scores and reviews visible to participants with row lock."""
        stmt = select(Competition).where(Competition.id == competition_id).with_for_update()
        res = await db.execute(stmt)
        comp = res.scalar_one_or_none()
        if not comp:
            raise CompetitionError("Competition not found")

        if comp.status != CompetitionStatus.RESULTS_FINALIZED:
            raise CompetitionError(
                f"Cannot publish results: competition status is {comp.status}, must be RESULTS_FINALIZED"
            )

        comp.status = CompetitionStatus.PUBLISHED
        await db.commit()
        await db.refresh(comp)

        log_audit_event("RESULTS_PUBLISHED", "ADMIN", admin_id, {
            "competition_id": str(competition_id)
        })
        return comp

    @staticmethod
    async def get_participant_result(
        db: AsyncSession, competition_id: uuid.UUID, participant_id: uuid.UUID
    ) -> Dict:
        """Retrieves participant result. SHIELDED until competition status is PUBLISHED."""
        comp = await db.get(Competition, competition_id)
        if not comp:
            raise CompetitionError("Competition not found")

        if comp.status != CompetitionStatus.PUBLISHED:
            raise ResultsNotPublishedError("Official results have not been published yet")

        stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == competition_id,
                ExamAttempt.participant_id == participant_id,
            )
        )
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            raise CompetitionError("No attempt found for this participant")

        # Format completion time
        seconds = int(attempt.completion_seconds or 0)
        minutes, secs = divmod(seconds, 60)
        formatted_time = f"{minutes:02d}:{secs:02d}"

        return {
            "competition_id": comp.id,
            "competition_title": comp.title,
            "score": attempt.score,
            "total_questions": comp.question_count,
            "rank": attempt.rank,
            "completion_time": formatted_time,
            "correct_count": attempt.correct_count,
            "incorrect_count": attempt.incorrect_count,
        }

    @staticmethod
    async def get_answer_review(
        db: AsyncSession, competition_id: uuid.UUID, participant_id: uuid.UUID, review_type: str = "all"
    ) -> List[Dict]:
        """Provides correct or incorrect answer review. STRICTLY BLOCKED until PUBLISHED."""
        comp = await db.get(Competition, competition_id)
        if not comp:
            raise CompetitionError("Competition not found")

        if comp.status != CompetitionStatus.PUBLISHED:
            raise ResultsNotPublishedError("Answer reviews are shielded until official results publication")

        stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == competition_id,
                ExamAttempt.participant_id == participant_id,
            )
        )
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            raise CompetitionError("No attempt found for this participant")

        # Query all answers for this attempt with question loaded
        ans_stmt = (
            select(ParticipantAnswer)
            .options(selectinload(ParticipantAnswer.question))
            .where(ParticipantAnswer.attempt_id == attempt.id)
            .order_by(ParticipantAnswer.answered_at)
        )
        ans_res = await db.execute(ans_stmt)
        answers = list(ans_res.scalars().all())

        review_items = []
        for ans in answers:
            if review_type == "correct" and not ans.is_correct:
                continue
            if review_type == "incorrect" and ans.is_correct:
                continue

            q = ans.question
            canonical_options = q.options  # {"A": "text A", ...}

            # Retrieve option mapping for this question in this attempt to display choices clearly
            map_stmt = select(AttemptQuestionOrder).where(
                and_(
                    AttemptQuestionOrder.attempt_id == attempt.id,
                    AttemptQuestionOrder.question_id == q.id,
                )
            )
            map_res = await db.execute(map_stmt)
            order_entry = map_res.scalar_one_or_none()

            display_correct = None
            if order_entry:
                # Find which displayed letter corresponded to q.correct_option
                for d_letter, c_letter in order_entry.option_mapping.items():
                    if c_letter == q.correct_option:
                        display_correct = d_letter
                        break

            correct_text = canonical_options.get(q.correct_option, "")
            user_selected_text = canonical_options.get(ans.resolved_canonical_option, "")

            review_items.append({
                "question_text": q.question_text,
                "is_correct": ans.is_correct,
                "selected_display_option": ans.selected_display_option,
                "user_answer_text": user_selected_text,
                "correct_display_option": display_correct or q.correct_option,
                "correct_answer_text": correct_text,
            })

        return review_items
