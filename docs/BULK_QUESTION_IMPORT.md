# Bulk Question Import Guide (Excel / Google Sheets → EMYC)

This guide explains how administrators and question authors can write examination questions in standard spreadsheet software (Excel, Google Sheets, LibreOffice Calc) and bulk-load them directly into the EMYC platform database without using Telegram or writing database queries.

---

### Step A — Prepare Questions in Excel or Google Sheets

Create a spreadsheet using the canonical column headers:

| Column | Description | Example |
|---|---|---|
| `order_index` | Sequential integer order (1, 2, 3...) | `1` |
| `question_text` | The question prompt | `What is the capital of Ethiopia?` |
| `option_a` | Text for Option A | `Addis Ababa` |
| `option_b` | Text for Option B | `Dire Dawa` |
| `option_c` | Text for Option C | `Mekelle` |
| `option_d` | Text for Option D | `Hawassa` |
| `correct_option` | Single letter: `A`, `B`, `C`, or `D` | `A` |

> [!TIP]
> A ready-to-use template populated with 20 sample questions is located at [`questions_template.csv`](file:///C:/Users/AFRO-EAGLE/EMYC/questions_template.csv). You can open and edit it directly.

---

### Step B — Export as CSV UTF-8

1. In Excel: Click **File** → **Save As** → Select **CSV UTF-8 (Comma delimited) (*.csv)**.
2. In Google Sheets: Click **File** → **Download** → **Comma Separated Values (.csv)**.

---

### Step C — Validate with Dry-Run Mode

Before modifying the database, run a dry-run to validate all question rows, options, answer keys, and competition eligibility:

```bash
python -m app.scripts.import_questions_csv \
  --file questions_template.csv \
  --competition "EMYC October Competition" \
  --dry-run
```

**Expected output:**
```text
EMYC Question Import

Competition: EMYC October Competition
Questions found: 20

✓ CSV structure valid
✓ 20 questions validated
✓ 20 correct-answer keys valid
✓ No duplicate order indexes
✓ No empty options
✓ Competition is eligible for import

Dry run complete.
No database changes made.
```

---

### Step D — Perform Real Import

Once dry-run validation succeeds, execute the real import:

```bash
python -m app.scripts.import_questions_csv \
  --file questions_template.csv \
  --competition "EMYC October Competition"
```

**If the competition already has existing questions**, use the `--replace` flag to overwrite them safely in one atomic transaction:

```bash
python -m app.scripts.import_questions_csv \
  --file questions_template.csv \
  --competition "EMYC October Competition" \
  --replace
```

**Targeting by Competition UUID (Alternative):**
```bash
python -m app.scripts.import_questions_csv \
  --file questions_template.csv \
  --competition-id <UUID>
```

**Expected output:**
```text
EMYC Question Import

Competition: EMYC October Competition
Questions imported: 20
Question count: 20

✓ Database transaction committed
✓ Competition updated
✓ Import completed successfully
```

---

### Step E — Verify in Telegram

The Telegram bot requires **zero configuration or manual entry**:
1. Open Telegram and run `/admin` → tap `[🏆 Competition Control]`.
2. The bot automatically reads the questions and displays:
   `• Format: 30 minutes · 20 questions`
3. Tap `[🟢 Start Competition (Set LIVE)]`. The pre-activation gateway will verify all questions and transition the competition to `LIVE`.
