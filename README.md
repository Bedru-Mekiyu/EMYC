# Telegram Competitive Exam & Competition Platform

A production-ready, high-concurrency Telegram competitive examination platform built from scratch with Python, FastAPI, and PostgreSQL (Supabase-compatible).

## 1. Architectural Principles

The architecture strictly separates interface presentation from authoritative business logic and state:

```
Telegram Client (Participant & Admin)
       ↓ (Webhook / Polling)
FastAPI Backend (Authoritative Security & State Machine)
       ↓ (SQLAlchemy 2.0 Async / asyncpg)
PostgreSQL Database (Supabase Source of Truth)
```

* **Telegram = Minimal Interface**: The bot only handles commands, clean inline keyboards, and localized message rendering. It never evaluates scores, calculates rankings, or verifies timers on the client. Questions update in-place by editing the existing message, preventing chat flood.
* **FastAPI = Business Logic & Authority**: Authoritative lifecycle enforcement, timer calculation, randomized question & option mapping persistence, idempotent answer recording, and pre-publication result shielding.
* **PostgreSQL = Relational Source of Truth**: Relational integrity with database-level uniqueness constraints preventing duplicate attempts, multiple accounts per membership, or duplicate question answers.

---

## 2. Key Features

* **Strict 1-to-1 Identity & Membership Verification**:
  * Membership IDs (e.g. `EMYC/4055828/2026`) verified via a pluggable adapter (`MembershipVerificationService`).
  * Database constraint guarantees that a single membership ID cannot be claimed by multiple Telegram accounts.
* **4-Language Localization**:
  * Full native support for **Amharic (አማርኛ)**, **Afaan Oromoo**, **Arabic (العربية)**, and **English**.
  * User language preference is persisted and drives all notifications, exam questions, and prompts.
* **Authoritative Exam Timing & Randomization**:
  * Individual exam timer: $\text{deadline} = \min(\text{started\_at} + \text{duration}, \text{competition.closes\_at})$.
  * Even if a participant disconnects or closes Telegram, the countdown continues authoritatively.
  * Auto-submission on deadline expiration or via background sweeper.
  * Question order is randomized and stored per attempt.
  * Option letters ($A, B, C, D$) are randomized per question and canonical mappings are persisted in `attempt_question_order`. Canonical answers are never leaked to participants.
* **Deterministic Scoring & Tie-Breaking**:
  * 1 point per correct answer, 0 for wrong, no negative marking.
  * Ranking rule:
    1. Higher score ranks first.
    2. Lower completion time ranks first.
    3. Deterministic final tie-breaker (earlier submission timestamp and attempt ID).
* **Pre-Publication Information Shielding**:
  * During the exam and after submission, no scores, ranks, or correct answers are revealed.
  * Full cryptographic and logical shield until an administrator explicitly finalizes and publishes results.
  * Post-publication: participants can view their score, rank, and review correct and incorrect answers.
* **Dual-Role Bot (Participants & Admin)**:
  * Administrators use the exact same Telegram bot with server-side ID authorization (`ADMIN_TELEGRAM_IDS`).
  * Real-time operational metrics: total participants, started, in-progress, submitted, and not-started counts.
  * Finalize scores and publish results with automated push notifications.

---

## 3. Technology Stack

* **Language**: Python 3.12+
* **Web Framework**: FastAPI (Uvicorn)
* **Telegram Bot**: python-telegram-bot v22 (Async ApplicationBuilder)
* **Database & ORM**: PostgreSQL / Supabase, SQLAlchemy 2.0 Async, asyncpg, Alembic
* **Validation**: Pydantic v2 & Pydantic-Settings
* **Testing**: pytest, pytest-asyncio, httpx, aiosqlite
* **Containerization**: Multi-stage Dockerfile & docker-compose

---

## 4. Project Structure

```
├── alembic/                      # Database migrations
│   ├── env.py
│   └── versions/                 # Versioned migration scripts
├── app/
│   ├── api/v1/                   # FastAPI endpoints (health, webhook)
│   ├── bot/                      # Telegram bot application
│   │   ├── handlers/             # Participant & Admin handlers
│   │   ├── keyboards.py          # Minimal inline keyboards
│   │   └── bot_app.py            # Bot builder & routing
│   ├── core/                     # Config, database, logging, time utilities
│   ├── locales/                  # Localized message catalogs (am, om, ar, en)
│   ├── models/                   # SQLAlchemy declarative models
│   ├── schemas/                  # Pydantic validation schemas
│   ├── scripts/                  # Seed scripts for development
│   ├── services/                 # Authoritative business logic
│   └── tasks/                    # Background sweeper tasks
├── tests/                        # Comprehensive automated test suite
├── docker-compose.yml            # Local development orchestration
├── Dockerfile                    # Multi-stage production build
├── requirements.txt              # Production and test dependencies
└── alembic.ini                   # Migration config
```

---

## 5. Local Setup & Quickstart

### Prerequisites
* Python 3.12+
* Git
* PostgreSQL (or Docker)

### Step 1: Clone & Virtual Environment
```bash
python -m venv .venv
# On Windows:
.\.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### Step 2: Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, and `ADMIN_TELEGRAM_IDS`.

### Step 3: Run Database Migrations
```bash
alembic upgrade head
```

### Step 4: Seed Sample Questions
```bash
python -m app.scripts.seed_questions
```

### Step 5: Start Backend Application
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 6. Running with Docker Compose

To spin up both PostgreSQL and the platform backend in containers:
```bash
docker compose up --build -d
```

Check health:
```bash
curl http://localhost:8000/api/v1/health/ready
```

---

## 7. Testing Suite

The repository includes extensive automated test suites covering all lifecycle states, timer calculations, randomization, idempotency, security boundaries, and end-to-end user journeys:

```bash
pytest -v
```

### Verification Checklist:
* `tests/test_health.py`: Liveness and database readiness probes.
* `tests/test_database_models.py`: Relational integrity, cascade deletions, and unique constraints.
* `tests/test_competition_and_exam.py`: Server-side scheduling, timer capping, randomization, and timeout auto-submission.
* `tests/test_scoring_and_ranking.py`: Server-side scoring, deterministic tie-breaking, and pre-publication result shielding.
* `tests/test_membership_service.py`: Format validation, mock/external adapters, and duplicate account prevention.
* `tests/test_telegram_bot_handlers.py`: Webhook secret verification and admin authorization guard.
* `tests/test_notifications_and_tasks.py`: Broadcast notification delivery and background deadline sweeping.
* `tests/test_end_to_end_lifecycle.py`: Complete 17-step end-to-end competition scenario.

---

## 8. Operational Runbook

1. **Creating a Competition**: Prepared directly via Supabase / PostgreSQL or Admin API.
2. **Opening the Exam**: An administrator sends `/admin` in Telegram, selects **🏆 Competition**, and taps **🟢 Open Competition**.
3. **Participants Register & Take Exam**:
   - Participants tap `/start` and enter their Membership ID (e.g. `EMYC/4055828/2026`).
   - Tapping **▶️ Start Competition** starts their individual timer and presents Question 1.
   - Selecting options updates the question in-place.
4. **Closing & Finalizing Results**:
   - When the competition closing time arrives, the background sweeper auto-submits any remaining active attempts.
   - Admin taps `/admin` -> **🏆 Results** -> **📊 Finalize Scores & Rankings**.
5. **Publishing Results**:
   - Admin reviews the top leaderboard preview and taps **📢 Publish Results**.
   - Notifications are automatically delivered to all participants with a **📊 View Result** button.
