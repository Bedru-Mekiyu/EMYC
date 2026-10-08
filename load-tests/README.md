# EMYC Platform — High-Throughput Load Testing & Capacity Engineering

This directory contains the automated performance benchmarking and database reconciliation suite designed to measure and certify the EMYC Examination Platform for synchronized examinations with up to **100,000 concurrent participants**.

---

## 1. Workload Architecture

During an EMYC synchronized competitive examination:
1. **Live Exam Start Surge (Minute 00:00)**: All registered examinees launch the Telegram Mini App and request `GET /api/v1/webapp/session`. Questions and randomized option mappings are delivered in a single payload.
2. **In-Exam Autonomous Execution (Minutes 00:01 - 59:59)**: The frontend executes entirely offline-first in the browser/WebView with zero server polling. All answers are buffered locally in `IndexedDB`/`localStorage`.
3. **Synchronized Submission Spike (Minute 60:00)**: When the 60-minute window closes, all examinees post their complete answer sheet via `POST /api/v1/webapp/submit`.
4. **Network Retry Storms**: Mobile connectivity drops trigger automated client retries, requiring strict atomic idempotency.
5. **Continuous Admin Monitoring**: Admins monitor live participation counts, submitted percentages, and score distributions without starving examinee database connections.

---

## 2. Tools in this Directory

| File | Purpose | Concurrency Target |
| :--- | :--- | :--- |
| `load_engine.py` | Asynchronous in-process ASGI benchmarking engine (HTTPX) | 100 to 10,000+ virtual users |
| `locustfile.py` | Distributed multi-machine HTTP load generator (Locust) | 1,000 to 100,000 distributed users |
| `reconcile_db.py` | Post-test mathematical database reconciliation & security audit | Complete DB validation |

---

## 3. How to Run Benchmarks

### Option A: Fast Local ASGI Benchmark (`load_engine.py`)

Run staged ladder tests using the built-in async engine:

```bash
# 1. Benchmark 100 examinees (Warm-up / Baseline)
python load-tests/load_engine.py 100 50

# 2. Benchmark 1,000 examinees (Concurrency: 200)
python load-tests/load_engine.py 1000 200

# 3. Benchmark 5,000 examinees (Concurrency: 250)
python load-tests/load_engine.py 5000 250
```

### Option B: Post-Load Database Reconciliation (`reconcile_db.py`)

After executing any benchmark run, audit the database to mathematically prove data integrity:

```bash
# Audits the most recent benchmark competition
python load-tests/reconcile_db.py

# Audits a specific competition by ID
python load-tests/reconcile_db.py <COMPETITION_UUID>
```

#### Invariants Verified:
- `Attempt Generation`: 1-to-1 matching between examinees and recorded attempts.
- `Zero Duplicate Attempts`: `GROUP BY participant_id HAVING count > 1 == 0`.
- `Zero Unscored Submissions`: All submitted attempts have computed integer scores.
- `Score Bounds Compliance`: All scores satisfy `0 <= score <= question_count`.
- `Answers Summary Persistence`: Complete answer arrays persisted atomically.
- `Zero Answer Key Leakage`: `correct_option` is absent from all participant summaries.

---

### Option C: Distributed Multi-Host Testing (`locustfile.py`)

To simulate 100,000 users distributed across multiple load-generator machines:

```bash
# On Master Node
locust -f load-tests/locustfile.py --master --host http://<TARGET_HOST>:8000

# On Worker Nodes (e.g. 4-8 cloud instances)
locust -f load-tests/locustfile.py --worker --master-host <MASTER_IP>
```

Or run headless in a single instance:
```bash
locust -f load-tests/locustfile.py --headless -u 1000 -r 100 --run-time 3m --host http://localhost:8000
```
