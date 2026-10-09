import asyncio
import ssl
import uuid
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# Supabase direct pooling connection
SUPABASE_URL = "postgresql+asyncpg://postgres.vsormssszsfpuwiarowl:nToHSKLHRAdhR1MT@aws-0-eu-west-2.pooler.supabase.com:5432/postgres"

import sys
sys.path.insert(0, ".")
from scripts.seed_50_questions import QUESTIONS_DATA

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

async def reset_and_seed_supabase():
    print("=" * 70)
    print("SUPABASE PRODUCTION DATABASE RESET & 50-QUESTION SEEDING")
    print("=" * 70)
    print("Target: aws-0-eu-west-2.pooler.supabase.com (Project: vsormssszsfpuwiarowl)")

    engine = create_async_engine(SUPABASE_URL, connect_args={"ssl": ctx})
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session() as session:
        # Step 1: Delete all existing answers, attempts, and question orders
        print("[1/5] Clearing all existing answers and exam attempts...")
        await session.execute(text("DELETE FROM participant_answers;"))
        try:
            await session.execute(text("DELETE FROM attempt_question_order;"))
        except Exception as e:
            print(f"      (attempt_question_order: {e})")
        await session.execute(text("DELETE FROM exam_attempts;"))
        print("      [OK] Cleaned participant_answers and exam_attempts.")

        # Step 2: Delete existing questions from all competitions
        print("[2/5] Deleting all existing questions in Supabase...")
        await session.execute(text("DELETE FROM competition_questions;"))
        print("      [OK] Deleted all existing competition_questions.")

        # Step 3: Clean up old archived competitions, keeping or targeting 'like test 50'
        print("[3/5] Resolving target competition 'like test 50'...")
        # Check if 'like test 50' exists
        res = await session.execute(text("SELECT id, title, status FROM competitions WHERE title ILIKE '%like test 50%' LIMIT 1;"))
        row = res.fetchone()
        
        now = datetime.now(timezone.utc)
        opens_at = now - timedelta(hours=1)
        closes_at = now + timedelta(days=7)

        if row:
            comp_id = row[0]
            print(f"      [OK] Found existing competition: '{row[1]}' (ID: {comp_id})")
            # Update to 50 questions, 120 mins duration, set to LIVE so it's ready for testing
            await session.execute(text("""
                UPDATE competitions 
                SET question_count = 50,
                    duration_minutes = 120,
                    status = 'LIVE',
                    opens_at = :opens_at,
                    closes_at = :closes_at,
                    actual_exam_started_at = :now,
                    actual_exam_ends_at = :closes_at,
                    updated_at = :now
                WHERE id = :id;
            """), {"id": comp_id, "opens_at": opens_at, "closes_at": closes_at, "now": now})
            print(f"      [OK] Updated competition '{row[1]}' status to LIVE with 50 questions & 120m duration.")
        else:
            comp_id = uuid.uuid4()
            await session.execute(text("""
                INSERT INTO competitions (
                    id, title, description, status, opens_at, closes_at,
                    duration_minutes, question_count, actual_exam_started_at, actual_exam_ends_at,
                    created_at, updated_at
                ) VALUES (
                    :id, 'like test 50', 'Official EMYC 50-Question Examination', 'LIVE',
                    :opens_at, :closes_at, 120, 50, :now, :closes_at, :now, :now
                );
            """), {"id": comp_id, "opens_at": opens_at, "closes_at": closes_at, "now": now})
            print(f"      [OK] Created competition 'like test 50' (ID: {comp_id}, Status: LIVE).")

        # Delete any other old test competitions
        await session.execute(text("DELETE FROM competitions WHERE id != :id;"), {"id": comp_id})
        print("      [OK] Cleaned old unused competitions.")

        # Step 4: Insert all 50 professional questions
        print(f"[4/5] Inserting {len(QUESTIONS_DATA)} professional questions into Supabase...")
        q_insert = text("""
            INSERT INTO competition_questions (
                id, competition_id, question_text, options, correct_option, order_index, created_at
            ) VALUES (
                :id, :comp_id, :question_text, :options, :correct_option, :order_index, :now
            );
        """)

        for q in QUESTIONS_DATA:
            q_uuid = uuid.uuid4()
            await session.execute(q_insert, {
                "id": q_uuid,
                "comp_id": comp_id,
                "question_text": q["question_text"],
                "options": json.dumps(q["options"]),
                "correct_option": q["correct_option"],
                "order_index": q["order_index"],
                "now": now,
            })

        await session.commit()
        print(f"      [OK] Successfully inserted {len(QUESTIONS_DATA)} questions into competition '{comp_id}'.")

        # Step 5: Verification in Supabase
        print("[5/5] Verifying Supabase database records...")
        q_count = (await session.execute(text("SELECT count(*) FROM competition_questions;"))).scalar()
        comp_res = await session.execute(text("SELECT id, title, status, question_count, duration_minutes FROM competitions;"))
        comp_info = comp_res.fetchall()
        p_count = (await session.execute(text("SELECT count(*) FROM participants;"))).scalar()
        a_count = (await session.execute(text("SELECT count(*) FROM exam_attempts;"))).scalar()

        print(f"      [OK] Total Competitions in Supabase: {len(comp_info)}")
        for ci in comp_info:
            print(f"        • Title: '{ci[1]}' | Status: {ci[2]} | Questions: {ci[3]} | Duration: {ci[4]}m")
        print(f"      [OK] Total Questions in Supabase:    {q_count}")
        print(f"      [OK] Registered Participants:        {p_count} (Preserved)")
        print(f"      [OK] Active / Previous Attempts:     {a_count} (Fresh start)")

    await engine.dispose()
    print("=" * 70)
    print("SUCCESS: SUPABASE IS FULLY UPDATED WITH 50 PROFESSIONAL QUESTIONS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(reset_and_seed_supabase())
