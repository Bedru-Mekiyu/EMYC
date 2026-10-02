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
* **Multi-Worker & Multi-Container Concurrency**: Sweepers and lifecycle state changes utilize PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` and row-level locks. Multiple application instances can run simultaneously without duplicate attempt processing or race conditions, eliminating the need for Redis or Celery.

---

## 2. Key Features

* **Strict 1-to-1 Identity & IDOR Elimination**:
  * Membership IDs (e.g. `EMYC/4055828/2026`) verified via a pluggable adapter (`MembershipVerificationService`).
  * Telegram User ID is the sole authoritative participant identity across all attempt navigation, answering, and submission operations.
  * Callback tampering is structurally rejected at both service and bot handler layers.
  * Database constraint guarantees that a single membership ID cannot be claimed by multiple Telegram accounts.
* **4-Language Localization**:
  * Full native support for **Amharic (አማርኛ)**, **Afaan Oromoo**, **Arabic (العربية)**, and **English**.
  * User language preference is persisted and drives all notifications, exam questions, and prompts.
* **Authoritative Exam Timing & Randomization**:
  * Individual exam timer: $\text{deadline} = \min(\text{started\_at} + \text{duration}, \text{competition.closes\_at})$.
  * Even if a participant disconnects or closes Telegram, the countdown continues authoritatively.
  * Two-layer expiration: lazy submission check on any interaction + background PostgreSQL `SKIP LOCKED` sweeper.
  * Question order is randomized and stored per attempt.
  * Option letters ($A, B, C, D$) are randomized per question and canonical mappings are persisted in `attempt_question_order`. Canonical answers are never leaked to participants.
* **Deterministic Scoring & Tie-Breaking**:
  * 1 point per correct answer, 0 for wrong, no negative marking.
  * 4-level deterministic ranking rule:
    1. `score DESC` (Higher score ranks first).
    2. `completion_seconds ASC` (Lower completion time ranks first).
    3. `submitted_at ASC` (Earlier submission timestamp ranks first).
    4. `attempt.id ASC` (Deterministic tie-breaker).
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
* **Database & ORM**: PostgreSQL 16+ / Supabase, SQLAlchemy 2.0 Async, asyncpg, Alembic
* **Validation**: Pydantic v2 & Pydantic-Settings
* **Testing**: pytest, pytest-asyncio, httpx
* **Containerization**: Multi-stage Dockerfile (non-root `appuser`) & docker-compose

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
├── Dockerfile                    # Multi-stage production build (non-root)
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

## 6. Runtime Transport Modes & Deployment Constraints

The platform supports two bot modes configured via `BOT_MODE`:

### A. Polling Mode (`BOT_MODE=polling`)
* **Usage**: Local development or single-container staging deployments.
* **Constraint**: Telegram Bot API prohibits multiple concurrent polling connections on the same bot token (HTTP 409 Conflict). Therefore, polling mode **MUST** run as a single process or single worker container (`--workers 1`).

### B. Webhook Mode (`BOT_MODE=webhook`)
* **Usage**: High-scale production deployments.
* **Mechanism**: Inbound updates delivered to `/api/v1/telegram/webhook` authenticated via `X-Telegram-Bot-Api-Secret-Token`.
* **Horizontal Scaling**: Fully supports arbitrary multiple FastAPI / Uvicorn workers and container replicas.

---

## 7. Membership Verification Adapter

The system abstracts membership validation via `MembershipVerificationService`:
* **Mock Adapter (`MEMBERSHIP_ADAPTER_TYPE=mock`)**: Validates format (`EMYC/\d{5,10}/\d{4}`) and simulates active status (with mock blacklist).
* **HTTP Adapter (`MEMBERSHIP_ADAPTER_TYPE=http`)**: Connects to external membership endpoint via `MEMBERSHIP_API_URL` and `MEMBERSHIP_API_KEY`.

> [!IMPORTANT]
> Real external membership verification requires the actual API contract/endpoint/authentication specification. The platform's modular adapter pattern allows drop-in integration without changes to core competition logic.

---

## 8. Testing Suite

All 28 tests execute directly against **native PostgreSQL 17** (`competition_test_db`) without SQLite mock shortcuts:

```bash
pytest -v
```

### Verified Test Suites:
* `tests/test_production_readiness_audit.py`: IDOR elimination, multi-worker sweeper safety with `SKIP LOCKED`, concurrent finalization races, answer-key structural shielding, and 4-level deterministic ranking ties.
* `tests/test_architectural_corrections.py`: Pre-LIVE validation, automatic competition closure, manual early closure policies, and PostgreSQL concurrency race guards.
* `tests/test_competition_and_exam.py`: Server-side scheduling, timer capping, randomization, and timeout auto-submission.
* `tests/test_scoring_and_ranking.py`: Server-side scoring, deterministic tie-breaking, and pre-publication result shielding.
* `tests/test_database_models.py`: Relational integrity, cascade deletions, and unique constraints.
* `tests/test_membership_service.py`: Format validation, mock/external adapters, and duplicate account prevention.
* `tests/test_telegram_bot_handlers.py`: Webhook secret verification and admin authorization guard.
* `tests/test_notifications_and_tasks.py`: Broadcast notification delivery and background deadline sweeping.
* `tests/test_end_to_end_lifecycle.py`: Complete 17-step end-to-end competition scenario.
* `tests/test_health.py`: Liveness and database readiness probes.

---

## 9. Operational Runbook

1. **Creating a Competition**: Prepared directly via PostgreSQL or Seed script.
2. **Opening the Exam**: An administrator sends `/admin` in Telegram, selects **🏆 Competition**, and taps **🟢 Open Competition**. (Enforces strict pre-LIVE validation).
3. **Participants Register & Take Exam**:
   - Participants tap `/start` and enter their Membership ID (e.g. `EMYC/4055828/2026`).
   - Tapping **▶️ Start Competition** starts their individual timer and presents Question 1.
   - Selecting options updates the question in-place.
4. **Closing & Finalizing Results**:
   - When the competition closing time arrives, the background sweeper auto-submits any remaining active attempts and closes the competition.
   - Admin taps `/admin` -> **🏆 Results** -> **📊 Finalize Scores & Rankings**.
5. **Publishing Results**:
   - Admin reviews the top leaderboard preview and taps **📢 Publish Results**.
   - Notifications are automatically delivered to all participants with a **📊 View Result** button.

---

## 10. Deploy to Render

The platform is fully prepared for a production deployment on **Render** as a single Web Service instance connected to a **Supabase PostgreSQL** database.

### Architectural Blueprint

```
Telegram Bot API
       │ (HTTPS POST Webhook)
       ▼
Render Web Service (FastAPI / Uvicorn on 0.0.0.0:$PORT)
       │  ├── GET /health (Liveness Probe)
       │  ├── POST /api/v1/telegram/webhook (Secret Token Validation)
       │  └── Background Sweeper (30s interval, SKIP LOCKED)
       ▼
Supabase PostgreSQL 16+ (SSL / Transactional Pool)
```

### 10.1 Prerequisites

1. **Supabase Database**: A PostgreSQL database on [Supabase](https://supabase.com). Copy the connection URI from **Project Settings** -> **Database** -> **Connection string** (URI).
2. **Telegram Bot Token**: Created via [@BotFather](https://t.me/BotFather).
3. **Admin User ID**: Your personal numeric Telegram User ID (obtain from [@userinfobot](https://t.me/userinfobot)).
4. **Render Account**: A free or paid account on [Render](https://render.com).

### 10.2 Deployment via Render Blueprint (`render.yaml`)

1. Connect your GitHub repository to Render.
2. Select **New** -> **Blueprint**.
3. Choose the repository containing `render.yaml`.
4. Render automatically configures:
   * **Runtime**: Python 3.12+
   * **Build Command**: `pip install -r requirements.txt && alembic upgrade head`
   * **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   * **Health Check Path**: `/health`
5. Fill in the sensitive environment variables in the prompt before deployment.

### 10.3 Manual Service Setup (Alternative)

If setting up manually in the Render Dashboard:

1. **Create Web Service**: Click **New +** -> **Web Service**.
2. **Environment**: `Python 3`.
3. **Region**: Choose the region closest to your Supabase database.
4. **Branch**: `master` (or your production branch).
5. **Build Command**:
   ```bash
   pip install -r requirements.txt && alembic upgrade head
   ```
6. **Start Command**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
7. **Health Check Path**: `/health`

### 10.4 Required Environment Variables

| Variable | Description | Example / Recommended Value |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Runtime environment name | `production` |
| `DEBUG` | Enable verbose debugging (MUST be false in prod) | `false` |
| `BOT_MODE` | Bot transport mode (MUST be webhook on Render) | `webhook` |
| `DATABASE_URL` | Supabase connection string (automatically normalizes to `+asyncpg`) | `postgresql://postgres:[PASSWORD]@[HOST]:5432/postgres` |
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather | `7123456789:AAH...` |
| `WEBHOOK_URL` | Public webhook endpoint URL on Render | `https://<service-name>.onrender.com/api/v1/telegram/webhook` |
| `WEBHOOK_SECRET` | Cryptographically random string (1-256 alphanumeric characters) | e.g. `openssl rand -hex 32` |
| `ADMIN_TELEGRAM_IDS` | Comma-separated numeric Telegram IDs of administrators | `123456789,987654321` |
| `MEMBERSHIP_ADAPTER_TYPE` | Membership verification adapter | `mock` (or `http` when live API is available) |
| `MEMBERSHIP_API_URL` | Live membership verification endpoint (if `http`) | `https://membership.emyc.org/api/v1/verify` |
| `MEMBERSHIP_API_KEY` | API authentication key for membership portal | Secret key |
| `DEFAULT_LANGUAGE` | Default locale for unregistered users | `en` |

### 10.5 Webhook Registration Lifecycle

When the application starts on Render:
1. `lifespan` initializes the Telegram application.
2. The bot verifies that `BOT_MODE=webhook` and `WEBHOOK_URL` is set.
3. It automatically registers the webhook with Telegram via `set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=Update.ALL_TYPES)`.
4. Telegram delivers all incoming user messages to `POST /api/v1/telegram/webhook`.
5. The endpoint validates the `x-telegram-bot-api-secret-token` header against `WEBHOOK_SECRET` and returns 200 OK.

### 10.6 Database Migration Procedure

Alembic migrations are executed automatically during the build step:
```bash
alembic upgrade head
```
* **Production Safety**: The migration runner **never** drops tables or runs downgrades.
* **Dialect Handling**: Supabase URLs provided with `postgresql://` or `postgres://` are automatically normalized to `postgresql+asyncpg://`.

### 10.7 20-Point Production Smoke-Test Checklist

After deploying to Render, verify the service using this checklist:

1. [ ] **Container Startup**: Render service status displays `Live` on port `$PORT`.
2. [ ] **Liveness Check**: `curl -s https://<service>.onrender.com/health` returns `{"status":"healthy",...}`.
3. [ ] **Readiness Check**: `curl -s https://<service>.onrender.com/api/v1/health/ready` returns `{"status":"ready","database":"healthy",...}`.
4. [ ] **Webhook Registered**: Render logs show `Successfully registered Telegram webhook`.
5. [ ] **Bot Start**: Send `/start` to the bot; receive personalized Islamic greeting and 3-button menu.
6. [ ] **Language Selection**: Tap `[🌐 Change Language]`; select language; verify menu reloads in that language.
7. [ ] **Membership Prompt**: Tap `[▶️ Start Competition]`; verify format instructions `EMYC/4055828/2026`.
8. [ ] **Membership Validation**: Submit an invalid ID (e.g. `INVALID`); verify friendly error with registration guidance.
9. [ ] **Membership Verification**: Submit a valid ID; verify confirmed membership linked to your Telegram account.
10. [ ] **Competition Details**: Verify title, opens_at, closes_at, duration, question count, and eligibility badge appear.
11. [ ] **Pre-LIVE Guard**: Verify participant cannot start before competition is marked LIVE.
12. [ ] **Exam Start**: When competition is LIVE, tap `[▶️ Start Competition]`; verify Question 1 loads cleanly.
13. [ ] **Distraction-Free UI**: Verify only `Question X / Y`, `Time remaining`, question text, options, and `[🏁 Finish Examination]` display.
14. [ ] **In-Place Updates**: Tap option `[B]`; verify question saves and advances without creating new messages.
15. [ ] **Authoritative Timer**: Verify countdown proceeds server-side.
16. [ ] **Submission Shield**: Submit examination; verify message confirms submission and results remain hidden.
17. [ ] **Admin Operations**: Send `/admin`; verify metrics (Registered, Started, In Progress, Submitted).
18. [ ] **Results Finalization**: As admin, tap `[🏆 Results]` -> `[📊 Finalize Scores & Rankings]`.
19. [ ] **Results Publication**: Admin taps `[📢 Publish Results]`; verify push notification is delivered to participant.
20. [ ] **Segregated Review**: Participant views result; taps `[✅ Correct Answers]` and `[❌ Incorrect Answers]`.

