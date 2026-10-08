"""Locust load-testing suite for EMYC Platform at 100,000 concurrent participant scale.

Usage:
  locust -f load-tests/locustfile.py --headless -u 1000 -r 100 --run-time 2m --host http://localhost:8000
"""

import os
import random
import time
from locust import FastHttpUser, task, between, events, tag


COMPETITION_ID = os.getenv("BENCHMARK_COMP_ID", "")
TOTAL_QUESTIONS = int(os.getenv("BENCHMARK_QUESTIONS", "100"))


class ExamineeUser(FastHttpUser):
    """Simulates an individual examinee taking the synchronized 60-minute exam via Telegram Mini App."""
    network_timeout = 30.0
    connection_timeout = 10.0

    def on_start(self):
        # Generate synthetic user credentials
        user_idx = random.randint(1, 1000000)
        self.telegram_user_id = 900000000 + user_idx
        self.full_name = f"Examinee {user_idx}"
        self.username = f"examinee_{user_idx}"

        # Generate local auth token matching backend HMAC signer
        from app.api.v1.webapp import create_web_auth_token
        self.auth_token = create_web_auth_token(
            telegram_user_id=self.telegram_user_id,
            username=self.username,
            full_name=self.full_name,
        )
        self.headers = {
            "X-Web-Auth-Token": self.auth_token,
            "Content-Type": "application/json",
        }
        self.attempt_id = None
        self.questions = []

    @task(10)
    @tag("exam_lifecycle")
    def complete_exam_flow(self):
        """Executes full exam lifecycle: Session Fetch -> Offline Solve -> Batch Submit -> Idempotent Retry."""
        # 1. Fetch Session (Surge at exam start)
        session_url = f"/api/v1/webapp/session?comp_id={COMPETITION_ID}" if COMPETITION_ID else "/api/v1/webapp/session"
        with self.client.get(session_url, headers=self.headers, catch_response=True, name="GET /session") as resp:
            if resp.status_code != 200:
                resp.failure(f"Failed to fetch session: HTTP {resp.status_code}")
                return
            data = resp.json()
            if data.get("status") != "ready":
                resp.failure(f"Session not ready: status={data.get('status')}")
                return

            # Security assertion: ensure zero answer key leakage
            q_list = data.get("questions", [])
            for q in q_list:
                if "correct_option" in q or "correct_answer" in q:
                    resp.failure("CRITICAL SECURITY VIOLATION: correct_option leaked in session response!")
                    return

            self.attempt_id = data.get("attempt_id")
            self.questions = q_list
            resp.success()

        if not self.attempt_id:
            return

        # 2. Simulate Examinee Think Time (random jitter)
        time.sleep(random.uniform(0.1, 0.5))

        # 3. Synchronized Batch Submit
        num_q = len(self.questions) if self.questions else TOTAL_QUESTIONS
        answers = [
            {
                "display_order": i + 1,
                "selected_option": random.choice(["A", "B", "C", "D"])
            }
            for i in range(num_q)
        ]

        payload = {
            "attempt_id": self.attempt_id,
            "answers": answers,
        }

        with self.client.post("/api/v1/webapp/submit", json=payload, headers=self.headers, catch_response=True, name="POST /submit") as resp:
            if resp.status_code != 200:
                resp.failure(f"Failed to submit answers: HTTP {resp.status_code} - {resp.text}")
                return
            res_data = resp.json()
            if res_data.get("status") not in ["success", "already_submitted"]:
                resp.failure(f"Unexpected submission status: {res_data.get('status')}")
                return
            resp.success()

        # 4. Immediate Duplicate Submission (Simulate Network Retries / Edge Idempotency)
        with self.client.post("/api/v1/webapp/submit", json=payload, headers=self.headers, catch_response=True, name="POST /submit (Idempotent Retry)") as resp:
            if resp.status_code == 200 and resp.json().get("status") in ["already_submitted", "success"]:
                resp.success()
            else:
                resp.failure(f"Idempotent retry failed: HTTP {resp.status_code}")

        # Stop user after completing exam
        self.stop()
