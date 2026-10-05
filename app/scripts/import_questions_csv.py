"""Bulk question import utility for EMYC competition platform.

Supports two CSV formats:
1. Flat Spreadsheet Format:
   order_index,question_text,option_a,option_b,option_c,option_d,correct_option

2. Supabase Direct Format:
   competition_id,order_index,question_text,options,correct_option

Usage Examples:
    # Dry-run validation (no DB changes):
    python -m app.scripts.import_questions_csv --file questions_flat_template.csv --competition-id <UUID> --dry-run

    # Import questions (replace existing):
    python -m app.scripts.import_questions_csv --file questions_flat_template.csv --competition-id <UUID> --replace

    # Import Supabase JSON format:
    python -m app.scripts.import_questions_csv --file questions_supabase_template.csv --competition-id <UUID>
"""
import argparse
import asyncio
import csv
import json
import os
import sys
import uuid
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion


def parse_csv_records(
    file_path: str, default_competition_id: Optional[uuid.UUID] = None
) -> Tuple[List[Dict], List[str]]:
    """Parses a CSV file and converts records to canonical question dictionaries.

    Returns:
        (questions, errors) tuple.
    """
    questions: List[Dict] = []
    errors: List[str] = []

    if not os.path.exists(file_path):
        return [], [f"File not found: {file_path}"]

    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], ["CSV file is empty or has no header row."]

        # Normalize fieldnames to lowercase trimmed
        field_map = {fn.strip().lower(): fn for fn in reader.fieldnames if fn}

        is_flat_format = all(
            k in field_map for k in ["option_a", "option_b", "option_c", "option_d"]
        )
        is_json_format = "options" in field_map

        if not is_flat_format and not is_json_format:
            return [], [
                "Unrecognized CSV format. Headers must include either "
                "('option_a', 'option_b', 'option_c', 'option_d') or ('options')."
            ]

        seen_indices = set()

        for line_num, row in enumerate(reader, start=2):
            row_has_err = False

            # Extract question text
            q_text_raw = row.get(field_map.get("question_text", ""), "")
            q_text = q_text_raw.strip() if q_text_raw else ""
            if not q_text:
                errors.append(f"Line {line_num}: question_text is empty.")
                row_has_err = True

            # Extract order index
            order_raw = row.get(field_map.get("order_index", ""), "")
            order_index = None
            try:
                order_index = int(order_raw.strip()) if order_raw else line_num - 1
                if order_index <= 0:
                    errors.append(f"Line {line_num}: order_index must be >= 1.")
                    row_has_err = True
                elif order_index in seen_indices:
                    errors.append(f"Line {line_num}: duplicate order_index {order_index} detected.")
                    row_has_err = True
                else:
                    seen_indices.add(order_index)
            except ValueError:
                errors.append(f"Line {line_num}: invalid integer for order_index: '{order_raw}'.")
                row_has_err = True

            # Extract correct option
            corr_raw = row.get(field_map.get("correct_option", ""), "")
            corr_opt = corr_raw.strip().upper() if corr_raw else ""
            if corr_opt not in {"A", "B", "C", "D"}:
                errors.append(
                    f"Line {line_num}: correct_option '{corr_raw}' must be one of 'A', 'B', 'C', 'D'."
                )
                row_has_err = True

            # Extract options dictionary
            options_dict: Dict[str, str] = {}
            if is_flat_format:
                opt_a = (row.get(field_map["option_a"]) or "").strip()
                opt_b = (row.get(field_map["option_b"]) or "").strip()
                opt_c = (row.get(field_map["option_c"]) or "").strip()
                opt_d = (row.get(field_map["option_d"]) or "").strip()
                if not (opt_a and opt_b and opt_c and opt_d):
                    errors.append(f"Line {line_num}: options A, B, C, and D must all be non-empty.")
                    row_has_err = True
                else:
                    options_dict = {"A": opt_a, "B": opt_b, "C": opt_c, "D": opt_d}
            else:
                raw_options = row.get(field_map["options"], "").strip()
                try:
                    loaded = json.loads(raw_options)
                    if not isinstance(loaded, dict) or set(loaded.keys()) != {"A", "B", "C", "D"}:
                        errors.append(
                            f"Line {line_num}: 'options' JSON must have exact keys 'A', 'B', 'C', 'D'."
                        )
                        row_has_err = True
                    elif any(not str(v).strip() for v in loaded.values()):
                        errors.append(f"Line {line_num}: all option values must be non-empty.")
                        row_has_err = True
                    else:
                        options_dict = {k: str(v).strip() for k, v in loaded.items()}
                except json.JSONDecodeError as jde:
                    errors.append(f"Line {line_num}: invalid JSON in 'options' column: {jde}.")
                    row_has_err = True

            # Competition ID resolution
            row_comp_id_str = row.get(field_map.get("competition_id", ""), "").strip()
            row_comp_id = None
            if row_comp_id_str and row_comp_id_str != "00000000-0000-0000-0000-000000000000":
                try:
                    row_comp_id = uuid.UUID(row_comp_id_str)
                except ValueError:
                    errors.append(f"Line {line_num}: invalid UUID for competition_id: '{row_comp_id_str}'.")
                    row_has_err = True
            comp_id_to_use = row_comp_id or default_competition_id

            if not row_has_err:
                questions.append(
                    {
                        "competition_id": comp_id_to_use,
                        "order_index": order_index,
                        "question_text": q_text,
                        "options": options_dict,
                        "correct_option": corr_opt,
                    }
                )

    return questions, errors


async def bulk_import_questions(
    file_path: str,
    competition_id: Optional[uuid.UUID],
    replace_existing: bool = False,
    dry_run: bool = False,
    force: bool = False,
    session: Optional[AsyncSession] = None,
) -> Tuple[bool, str, int]:
    """Imports questions from CSV into the database.

    Returns:
        (success: bool, message: str, imported_count: int)
    """
    records, parse_errors = parse_csv_records(file_path, default_competition_id=competition_id)
    if parse_errors:
        err_msg = "Validation failed with errors:\n" + "\n".join(f" - {e}" for e in parse_errors[:10])
        if len(parse_errors) > 10:
            err_msg += f"\n ... and {len(parse_errors) - 10} more errors."
        return False, err_msg, 0

    if not records:
        return False, "No valid question records found in file.", 0

    # Ensure competition ID is resolved
    first_comp_id = records[0]["competition_id"]
    if not first_comp_id:
        return (
            False,
            "No competition ID found. Provide --competition-id or specify competition_id in CSV.",
            0,
        )

    for idx, r in enumerate(records):
        if r["competition_id"] != first_comp_id:
            return (
                False,
                f"Question at index {idx + 1} has mismatched competition_id: {r['competition_id']}",
                0,
            )

    target_comp_id = first_comp_id

    # If dry-run, output success without touching DB
    if dry_run:
        return (
            True,
            f"[DRY-RUN SUCCESS] Parsed and validated {len(records)} questions successfully for competition {target_comp_id}.",
            len(records),
        )

    # Database operation helper
    async def _execute_import(db: AsyncSession) -> Tuple[bool, str, int]:
        # 1. Verify competition exists
        stmt = select(Competition).where(Competition.id == target_comp_id)
        comp = (await db.execute(stmt)).scalar_one_or_none()
        if not comp:
            return False, f"Target competition with ID '{target_comp_id}' does not exist in database.", 0

        # Safety check: Prevent modifying LIVE, CLOSED, or PUBLISHED competitions unless force=True
        closed_or_active_states = {
            CompetitionStatus.LIVE,
            CompetitionStatus.CLOSED,
            CompetitionStatus.RESULTS_FINALIZED,
            CompetitionStatus.PUBLISHED,
            CompetitionStatus.ARCHIVED,
        }
        if comp.status in closed_or_active_states and not force:
            return (
                False,
                f"Competition is currently '{comp.status.value}'. Importing questions into active/closed competitions is prohibited without --force.",
                0,
            )

        # 2. Check existing questions
        count_stmt = select(func.count()).select_from(CompetitionQuestion).where(
            CompetitionQuestion.competition_id == target_comp_id
        )
        existing_count = (await db.execute(count_stmt)).scalar() or 0

        if existing_count > 0 and not replace_existing:
            return (
                False,
                f"Competition already has {existing_count} questions. Use --replace to overwrite them.",
                0,
            )

        # 3. Replace if requested
        if existing_count > 0 and replace_existing:
            del_stmt = delete(CompetitionQuestion).where(
                CompetitionQuestion.competition_id == target_comp_id
            )
            await db.execute(del_stmt)

        # 4. Insert new questions
        for r in records:
            q = CompetitionQuestion(
                competition_id=target_comp_id,
                order_index=r["order_index"],
                question_text=r["question_text"],
                options=r["options"],
                correct_option=r["correct_option"],
            )
            db.add(q)

        # 5. Automatically sync competition question_count
        comp.question_count = len(records)

        await db.commit()
        return (
            True,
            f"Successfully imported {len(records)} questions for '{comp.title}' ({target_comp_id}).",
            len(records),
        )

    if session:
        return await _execute_import(session)
    else:
        async with AsyncSessionLocal() as db:
            return await _execute_import(db)


def main():
    parser = argparse.ArgumentParser(
        description="Bulk import competition examination questions into EMYC database from CSV."
    )
    parser.add_argument(
        "--file", "-f", required=True, help="Path to the CSV questions file."
    )
    parser.add_argument(
        "--competition-id",
        "-c",
        required=False,
        type=uuid.UUID,
        help="Target competition UUID (optional if competition_id is in the CSV).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing questions for this competition before importing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate CSV questions without writing to the database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force import even if competition status is LIVE or COMPLETED.",
    )

    args = parser.parse_args()

    success, message, count = asyncio.run(
        bulk_import_questions(
            file_path=args.file,
            competition_id=args.competition_id,
            replace_existing=args.replace,
            dry_run=args.dry_run,
            force=args.force,
        )
    )

    if success:
        print(f"✅ {message}")
        sys.exit(0)
    else:
        print(f"❌ {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
