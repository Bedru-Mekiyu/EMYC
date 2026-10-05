"""Locust Distributed Load-Testing Suite for EMYC 100,000 Concurrent Examinees.

Tests the Telegram Mini App high-throughput backend endpoints:
1. GET /api/v1/webapp/session (One-shot 100-question retrieval)
2. POST /api/v1/webapp/submit (Single-transaction 100-answer atomic batch submission)
3. GET /api/v1/webapp/health (Load balancer liveness probe)

Usage:
  # Local benchmark with 500 users:
  locust -f scripts/locustfile.py --headless -u 500 -r 50 --run-time 1m --host http://localhost:8000

  # High-scale distributed test with web UI:
  locust -f scripts/locustfile.py --host http://localhost:8000
"""

import json
import time
import random
import uuid
import hashlib
import hmac
from locust import HttpUser, task, between, events


def generate_mock_init_data(user_id: int, bot_token: str = "mock_token_for_tests") -> str:
    """Generates authentic Telegram WebApp initData with cryptographically valid HMAC-SHA256 signature."""
    user_json = json.dumps({"id": user_id, "username": f"student_{user_id}", "first_name": f"Examinee {user_id}"}, separators=(",", ":"))
    auth_date = str(int(time.time()))
    data_dict = {"auth_date": auth_date, "user": user_json}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"auth_date={auth_date}&user={user_json}&hash={calc_hash}"


class EMYCExamineeUser(HttpUser):
    # Examinees spend most time answering locally on phone (zero backend traffic),
    # then submit batch answers at end of test.
    wait_time = between(1, 3)

    def on_start(self):
        # Generate random unique Telegram user ID for this simulated student
        self.telegram_user_id = random.randint(10000000, 99999999)
        self.init_data = generate_mock_init_data(self.telegram_user_id)
        self.headers = {
            "Content-Type": "application/json",
            "X-Telegram-Init-Data": self.init_data,
        }
        self.attempt_id = None
        self.questions = []

    @task(3)
    def fetch_exam_session(self):
        """Simulates student opening the Mini App to download the 100 questions once."""
        with self.client.get(
            "/api/v1/webapp/session",
            headers=self.headers,
            catch_response=True,
            name="GET /session (One-shot Exam Load)",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ready":
                    self.attempt_id = data.get("attempt_id")
                    self.questions = data.get("questions", [])
                    resp.success()
                elif data.get("status") in ("already_submitted", "scheduled", "not_live"):
                    resp.success()
                else:
                    resp.failure(f"Unexpected session status: {data.get('status')}")
            else:
                resp.failure(f"HTTP {resp.status_code}: {resp.text}")

    @task(1)
    def submit_exam_batch(self):
        """Simulates student submitting all 100 answers in 1 atomic request."""
        if not self.attempt_id:
            return

        # Prepare 100 answers
        answers_payload = []
        for i, q in enumerate(self.questions, start=1):
            q_id = q.get("question_id")
            selected = random.choice(["A", "B", "C", "D"])
            answers_payload.append({
                "question_id": q_id,
                "display_order": i,
                "selected_option": selected,
            })

        submit_body = {
            "attempt_id": self.attempt_id,
            "answers": answers_payload,
        }

        with self.client.post(
            "/api/v1/webapp/submit",
            json=submit_body,
            headers=self.headers,
            catch_response=True,
            name="POST /submit (100-Answer Batch Submit)",
        ) as resp:
            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") in ("success", "already_submitted"):
                    resp.success()
                else:
                    resp.failure(f"Submission failed: {result}")
            elif resp.status_code in (403, 410):
                # Expired or already closed
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}: {resp.text}")

    @task(1)
    def health_check(self):
        """Simulates ALB / CloudWatch health probe."""
        self.client.get("/api/v1/webapp/health", name="GET /health (Liveness Probe)")
