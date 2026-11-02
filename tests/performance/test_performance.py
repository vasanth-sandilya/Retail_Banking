"""
Performance: time budgets for a busy branch. Budgets are generous on purpose, so
they catch real slowdowns rather than a busy machine. PERF_BUDGET_SCALE relaxes them.
"""
import os
import statistics
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from helpers import ApiCase, FIXED_NOW, TOMORROW  # also puts backend/ on the path
import appointments as AP
import assistant as A
from server import MODEL, QueueEngine

SCALE = float(os.environ.get("PERF_BUDGET_SCALE", "1"))
BUSY_QUEUE = 300  # far more customers than a branch has waiting at once


def timed(fn):
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


class Budget:
    def within(self, label, measured_ms, budget_ms):
        print(f"  {label}: {measured_ms:.1f} ms (budget {budget_ms * SCALE:.0f} ms)")
        self.assertLess(measured_ms, budget_ms * SCALE, label)


def busy_queue():
    q = QueueEngine(MODEL)
    services = ["cash_deposit", "passbook", "loan_enquiry", "forex", "wealth", "rm_meeting"]
    for i in range(BUSY_QUEUE):
        q.issue_token(services[i % len(services)])
    return q


class EngineSpeed(Budget, unittest.TestCase):
    def test_issuing_a_busy_morning_of_tokens(self):
        self.within(f"issue {BUSY_QUEUE} tokens", timed(busy_queue), 1500)

    def test_dashboard_snapshot_with_a_busy_queue(self):
        q = busy_queue()
        self.within("dashboard snapshot", timed(q.snapshot), 100)

    def test_token_issue_stays_fast_with_a_busy_queue(self):
        q = busy_queue()
        self.within("one more token", timed(lambda: q.issue_token("cash_deposit")), 25)

    def test_model_training_keeps_startup_short(self):
        self.within("no-show model training", timed(AP.NoShowModel), 4000)

    def test_scoring_is_instant(self):
        x = TRAINED.features(7, False, 3, 1, False, FIXED_NOW.date(), "11:00", "app")
        self.within("1000 risk scores", timed(lambda: [TRAINED.score(x) for _ in range(1000)]), 50)

    def test_full_day_calendar(self):
        book = AP.AppointmentBook(QueueEngine(MODEL), TRAINED)
        book.clock = lambda: FIXED_NOW
        for adv_type in ("rm", "rm", "loan", "wealth", "forex"):
            for t in AP.TIMES:
                book.create(adv_type, TOMORROW, t, "Anita Rao")
        self.within("full day calendar", timed(lambda: book.day_view(TOMORROW)), 100)

    def test_kiosk_conversation(self):
        q = QueueEngine(MODEL)
        a = A.Assistant(q)

        def journey():
            for _ in range(100):
                sid = a.start("ta")["session"]
                a.message(sid, "என் இருப்பு எவ்வளவு")
                a.message(sid, action="cancel")
                a.message(sid, action="svc:passbook")
                a.message(sid, action="seg:regular")
                a.end(sid)
        self.within("100 kiosk conversations", timed(journey), 500)

    def test_many_open_sessions_do_not_slow_a_reply(self):
        a = A.Assistant(QueueEngine(MODEL))
        for _ in range(2000):
            sid = a.start("en")["session"]
        self.within("reply with 2000 open sessions", timed(lambda: a.message(sid, "balance")), 25)


TRAINED = AP.NoShowModel()


class ServerLoad(Budget, ApiCase):
    def load(self, label, call, clients, requests, p95_budget_ms):
        def one(_):
            started = time.perf_counter()
            status = call().status
            return status, (time.perf_counter() - started) * 1000
        wall = time.perf_counter()
        with ThreadPoolExecutor(clients) as pool:
            results = list(pool.map(one, range(requests)))
        rate = requests / (time.perf_counter() - wall)
        times = sorted(ms for _, ms in results)
        print(f"  {label}: {rate:.0f} requests/s, median {statistics.median(times):.1f} ms")
        self.assertEqual({s for s, _ in results}, {200}, label)
        self.within(f"{label} p95", times[int(len(times) * 0.95)], p95_budget_ms)

    def test_staff_screens_under_load(self):
        for i in range(60):
            self.api.post("/api/tokens", {"service": "cash_deposit"})
        self.load("dashboard polling, 20 clients", lambda: self.api.get("/api/state"), 20, 300, 200)

    def test_kiosks_under_load(self):
        self.load("kiosk start, 20 clients", lambda: self.api.post("/api/assist/start", {"lang": "ta"}), 20, 300, 200)

    def test_token_issue_under_load(self):
        self.load("token issue, 10 clients", lambda: self.api.post("/api/tokens", {"service": "passbook"}), 10, 200, 200)

    def test_calendar_under_load(self):
        self.load("calendar, 10 clients", lambda: self.api.get(f"/api/appointments?date={TOMORROW}"), 10, 200, 200)

    def test_home_page_under_load(self):
        self.load("home page, 10 clients", lambda: self.api.get("/"), 10, 200, 200)
