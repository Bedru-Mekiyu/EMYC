"""Bulk question import utility for EMYC competition platform.

Supports human-authoring CSV format:
   order_index,question_text,option_a,option_b,option_c,option_d,correct_option

Also supports Supabase JSON format:
   competition_id,order_index,question_text,options,correct_option

Usage Examples:
    # Dry-run validation by competition title:
    python -m app.scripts.import_questions_csv --file questions_template.csv --competition "EMYC October Competition" --dry-run

    # Real import by competition title:
    python -m app.scripts.import_questions_csv --file questions_template.csv --competition "EMYC October Competition"

    # Real import with replace:
    python -m app.scripts.import_questions_csv --file questions_template.csv --competition "EMYC October Competition" --replace

    # Real import by competition UUID:
    python -m app.scripts.import_questions_csv --file questions_template.csv --competition-id <UUID>
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

    try:
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
                    "('order_index', 'question_text', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_option') "
                    "or ('options')."
                ]

            seen_indices = set()

            for line_num, row in enumerate(reader, start=2):
                # Skip entirely empty rows
                if not any(v and str(v).strip() for v in row.values()):
                    continue

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

                # Competition ID resolution from CSV column if present
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
    except Exception as e:
        return [], [f"Failed to read CSV file: {e}"]

    return questions, errors


async def resolve_competition(
    db: AsyncSession,
    competition_id: Optional[uuid.UUID] = None,
    competition_name: Optional[str] = None,
) -> Tuple[Optional[Competition], Optional[str]]:
    """Resolves target competition by UUID or human title."""
    if competition_id:
        stmt = select(Competition).where(Competition.id == competition_id)
        comp = (await db.execute(stmt)).scalar_one_or_none()
        if not comp:
            return None, f"Competition with ID '{competition_id}' not found."
        return comp, None

    if competition_name:
        clean_name = competition_name.strip()
        # 1. Exact match (case-insensitive)
        stmt_exact = select(Competition).where(func.lower(Competition.title) == clean_name.lower())
        exact_matches = (await db.execute(stmt_exact)).scalars().all()
        if len(exact_matches) == 1:
            return exact_matches[0], None
        elif len(exact_matches) > 1:
            candidates = [f"'{c.title}' (ID: {c.id})" for c in exact_matches]
            return (
                None,
                f"Multiple competitions found matching '{clean_name}': {', '.join(candidates)}. Please specify --competition-id.",
            )

        # 2. Substring match (case-insensitive)
        stmt_sub = select(Competition).where(Competition.title.ilike(f"%{clean_name}%"))
        sub_matches = (await db.execute(stmt_sub)).scalars().all()
        if len(sub_matches) == 1:
            return sub_matches[0], None
        elif len(sub_matches) > 1:
            candidates = [f"'{c.title}' (ID: {c.id})" for c in sub_matches]
            return (
                None,
                f"Multiple competitions found matching '{clean_name}': {', '.join(candidates)}. Please specify --competition-id.",
            )
        else:
            return None, f"No competition found with title matching '{clean_name}'."

    return (
        None,
        "No competition specified. Provide --competition '<NAME>' or --competition-id <UUID>.",
    )


async def bulk_import_questions(
    file_path: str,
    competition_id: Optional[uuid.UUID] = None,
    competition_name: Optional[str] = None,
    replace_existing: bool = False,
    dry_run: bool = False,
    force: bool = False,
    session: Optional[AsyncSession] = None,
) -> Tuple[bool, str, int]:
    """Imports or validates questions from CSV into the database.

    Returns:
        (success: bool, output_message: str, question_count: int)
    """
    # Database execution inner routine
    async def _execute(db: AsyncSession) -> Tuple[bool, str, int]:
        # 1. Parse CSV
        records, parse_errors = parse_csv_records(file_path, default_competition_id=competition_id)
        if parse_errors:
            err_lines = "\n".join(f"✗ {e}" for e in parse_errors[:15])
            if len(parse_errors) > 15:
                err_lines += f"\n... and {len(parse_errors) - 15} more error(s)."
            msg = (
                f"EMYC Question Import\n\n"
                f"Validation Failed:\n"
                f"{err_lines}\n\n"
                f"Import aborted. No database changes made."
            )
            return False, msg, 0

        if not records:
            msg = (
                f"EMYC Question Import\n\n"
                f"Validation Failed:\n"
                f"✗ No valid question records found in file.\n\n"
                f"Import aborted. No database changes made."
            )
            return False, msg, 0

        # 2. Resolve competition
        target_uuid = competition_id or records[0]["competition_id"]
        comp, comp_err = await resolve_competition(
            db, competition_id=target_uuid, competition_name=competition_name
        )
        if comp_err or not comp:
            msg = (
                f"EMYC Question Import\n\n"
                f"Competition Error:\n"
                f"✗ {comp_err}\n\n"
                f"Import aborted. No database changes made."
            )
            return False, msg, 0

        # Ensure all records have resolved competition ID
        for r in records:
            r["competition_id"] = comp.id

        # 3. Check Lifecycle Eligibility
        closed_or_active_states = {
            CompetitionStatus.LIVE,
            CompetitionStatus.CLOSED,
            CompetitionStatus.RESULTS_FINALIZED,
            CompetitionStatus.PUBLISHED,
            CompetitionStatus.ARCHIVED,
        }
        if comp.status in closed_or_active_states and not force:
            msg = (
                f"EMYC Question Import\n\n"
                f"Competition: {comp.title} ({comp.status.value})\n"
                f"✗ Competition is currently {comp.status.value}. Modifying questions for active or finalized competitions is prohibited.\n\n"
                f"Import aborted. No database changes made."
            )
            return False, msg, 0

        # 4. Check existing questions count
        count_stmt = select(func.count()).select_from(CompetitionQuestion).where(
            CompetitionQuestion.competition_id == comp.id
        )
        existing_count = (await db.execute(count_stmt)).scalar() or 0

        if existing_count > 0 and not replace_existing:
            msg = (
                f"EMYC Question Import\n\n"
                f"Competition: {comp.title}\n"
                f"✗ Competition already has {existing_count} questions. Use --replace to overwrite them.\n\n"
                f"Import aborted. No database changes made."
            )
            return False, msg, 0

        # 5. Handle Dry-Run Mode
        if dry_run:
            msg = (
                f"EMYC Question Import\n\n"
                f"Competition: {comp.title}\n"
                f"Questions found: {len(records)}\n\n"
                f"✓ CSV structure valid\n"
                f"✓ {len(records)} questions validated\n"
                f"✓ {len(records)} correct-answer keys valid\n"
                f"✓ No duplicate order indexes\n"
                f"✓ No empty options\n"
                f"✓ Competition is eligible for import\n\n"
                f"Dry run complete.\n"
                f"No database changes made."
            )
            return True, msg, len(records)

        # 6. Execute Atomic Transaction (Real Import)
        try:
            # Replace existing if requested
            if existing_count > 0 and replace_existing:
                del_stmt = delete(CompetitionQuestion).where(
                    CompetitionQuestion.competition_id == comp.id
                )
                await db.execute(del_stmt)

            # Insert all questions
            for r in records:
                q = CompetitionQuestion(
                    competition_id=comp.id,
                    order_index=r["order_index"],
                    question_text=r["question_text"],
                    options=r["options"],
                    correct_option=r["correct_option"],
                )
                db.add(q)

            # Synchronize competition.question_count
            comp.question_count = len(records)

            await db.commit()
            await db.refresh(comp)

            msg = (
                f"EMYC Question Import\n\n"
                f"Competition: {comp.title}\n"
                f"Questions imported: {len(records)}\n"
                f"Question count: {comp.question_count}\n\n"
                f"✓ Database transaction committed\n"
                f"✓ Competition updated\n"
                f"✓ Import completed successfully"
            )
            return True, msg, len(records)
        except Exception as e:
            await db.rollback()
            msg = (
                f"EMYC Question Import\n\n"
                f"Transaction Error:\n"
                f"✗ Database write failed: {str(e).splitlines()[0]}\n\n"
                f"Import aborted. All changes rolled back."
            )
            return False, msg, 0

    if session:
        return await _execute(session)
    else:
        async with AsyncSessionLocal() as db:
            return await _execute(db)


def main():
    parser = argparse.ArgumentParser(
        description="Bulk import competition examination questions into EMYC database from CSV."
    )
    parser.add_argument(
        "--file", "-f", required=True, help="Path to the CSV questions file (e.g. questions_template.csv)."
    )
    parser.add_argument(
        "--competition",
        "-n",
        required=False,
        help="Target competition title or name (e.g. 'EMYC October Competition').",
    )
    parser.add_argument(
        "--competition-id",
        "-c",
        required=False,
        type=uuid.UUID,
        help="Target competition UUID (optional if --competition is specified).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing questions for this competition before importing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate CSV questions and check eligibility without modifying database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force import even if competition status is LIVE or COMPLETED.",
    )

    args = parser.parse_args()

    if not args.competition and not args.competition_id:
        print(
            "❌ Error: Please specify the target competition using either --competition '<TITLE>' or --competition-id <UUID>.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        success, message, count = asyncio.run(
            bulk_import_questions(
                file_path=args.file,
                competition_id=args.competition_id,
                competition_name=args.competition,
                replace_existing=args.replace,
                dry_run=args.dry_run,
                force=args.force,
            )
        )
    except Exception as e:
        print(f"❌ Unexpected error during import: {e}", file=sys.stderr)
        sys.exit(1)

    if success:
        print(message)
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
