"""
BranchFlow - Intelligent queue management for retail bank branches.

Pure Python standard library (no pip install needed). Run:
    python backend/server.py
Then open http://localhost:8000
"""
import json
import math
import os
import random
import sys
import threading
from datetime import datetime, timedelta, date
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # embeddable Python omits the script folder
from assistant import Assistant, client_meta

PORT = int(os.environ.get("PORT", 8000))
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

BRANCH_OPEN_HOUR = 9
BRANCH_CLOSE_HOUR = 17  # last hour slot starts at 16:00
HOURS = list(range(BRANCH_OPEN_HOUR, BRANCH_CLOSE_HOUR))
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OPEN_DAYS = [0, 1, 2, 3, 4, 5]  # Sunday closed

# ---------------------------------------------------------------------------
# Service catalogue: category drives token prefix and counter skill matching
# avg_minutes = average handling time used for wait estimation
# ---------------------------------------------------------------------------
SERVICES = {
    "cash_deposit":    {"name": "Cash Deposit",            "category": "CASH",   "prefix": "C", "avg_minutes": 4},
    "cash_withdrawal": {"name": "Cash Withdrawal",         "category": "CASH",   "prefix": "C", "avg_minutes": 4},
    "cheque_dd":       {"name": "Cheque / Demand Draft",   "category": "CASH",   "prefix": "C", "avg_minutes": 6},
    "passbook":        {"name": "Passbook Update",         "category": "SERVICE","prefix": "S", "avg_minutes": 3},
    "account_open":    {"name": "Account Opening",         "category": "SERVICE","prefix": "S", "avg_minutes": 20},
    "kyc_update":      {"name": "KYC / Address Update",    "category": "SERVICE","prefix": "S", "avg_minutes": 8},
    "card_services":   {"name": "Debit / Credit Card",     "category": "SERVICE","prefix": "S", "avg_minutes": 10},
    "loan_enquiry":    {"name": "Loan Enquiry / Application", "category": "LOAN","prefix": "L", "avg_minutes": 25},
    "mortgage":        {"name": "Home Loan / Mortgage",    "category": "LOAN",   "prefix": "L", "avg_minutes": 35},
    "wealth":          {"name": "Investments & Wealth",    "category": "WEALTH", "prefix": "W", "avg_minutes": 30},
    "forex":           {"name": "Foreign Exchange",        "category": "FOREX",  "prefix": "F", "avg_minutes": 12},
    "locker":          {"name": "Safe Deposit Locker",     "category": "SERVICE","prefix": "S", "avg_minutes": 10},
    "general_help":    {"name": "General Help",            "category": "SERVICE","prefix": "S", "avg_minutes": 5},
}

# Customer segments add priority points (higher = served earlier among routed queue)
SEGMENTS = {
    "regular":  {"name": "Regular",             "priority": 0},
    "senior":   {"name": "Senior Citizen",      "priority": 30},
    "disabled": {"name": "Differently Abled",   "priority": 40},
    "premium":  {"name": "Premium / Privilege", "priority": 25},
    "appointment": {"name": "Pre-booked Appointment", "priority": 35},
    "urgent":   {"name": "Urgent (lost card)",  "priority": 50},  # set by the assistant, not offered at the token screen
}

# Counters with skill sets. A counter can serve any category in `skills`;
# `primary` is the category it is optimised for (routing prefers primary matches).
DEFAULT_COUNTERS = [
    {"id": 1, "name": "Counter 1", "role": "Teller",            "staff": "R. Mehta",   "skills": ["CASH"],             "primary": "CASH"},
    {"id": 2, "name": "Counter 2", "role": "Teller",            "staff": "A. Fernandes","skills": ["CASH"],            "primary": "CASH"},
    {"id": 3, "name": "Counter 3", "role": "Teller",            "staff": "S. Iyer",    "skills": ["CASH", "SERVICE"],  "primary": "CASH"},
    {"id": 4, "name": "Counter 4", "role": "Customer Service",  "staff": "P. Nair",    "skills": ["SERVICE", "CASH"],  "primary": "SERVICE"},
    {"id": 5, "name": "Counter 5", "role": "Customer Service",  "staff": "K. Das",     "skills": ["SERVICE", "FOREX"], "primary": "SERVICE"},
    {"id": 6, "name": "Desk A",    "role": "Loan Specialist",   "staff": "V. Rao",     "skills": ["LOAN"],             "primary": "LOAN"},
    {"id": 7, "name": "Desk B",    "role": "Wealth Advisor",    "staff": "N. Kapoor",  "skills": ["WEALTH", "LOAN"],   "primary": "WEALTH"},
    {"id": 8, "name": "Desk C",    "role": "Forex Specialist",  "staff": "J. Thomas",  "skills": ["FOREX", "SERVICE"], "primary": "FOREX"},
]

SLA_MINUTES = 15  # target max wait


# ---------------------------------------------------------------------------
# Footfall forecasting
# ---------------------------------------------------------------------------
class FootfallModel:
    """
    Seasonal forecasting on synthetic branch history (12 weeks).

    forecast(day, hour) = baseline(day, hour)            # exponentially weighted avg of same slot
                          * trend                        # recent 4 weeks vs earlier
                          * calendar_factor(date)        # salary days, month-end, pension day
    A simple confidence band is derived from the slot's historical std-dev.
    """

    WEEKS = 12

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.history = self._generate_history()
        self._fit()

    # Shape of a typical day: late-morning peak and post-lunch peak
    @staticmethod
    def _hour_shape(h):
        return {9: 0.55, 10: 0.95, 11: 1.20, 12: 1.05, 13: 0.70, 14: 0.90, 15: 1.00, 16: 0.65}[h]

    @staticmethod
    def _day_shape(d):
        return [1.35, 1.00, 0.90, 0.95, 1.15, 1.25, 0.0][d]  # Mon & Sat busiest

    @staticmethod
    def calendar_factor(dt: date):
        f = 1.0
        notes = []
        if dt.day in (1, 2, 3):
            f *= 1.30; notes.append("Salary / pension days (+30%)")
        elif dt.day >= 28:
            f *= 1.15; notes.append("Month-end (+15%)")
        elif dt.day in (10, 15):
            f *= 1.08; notes.append("Bill-payment / EMI date (+8%)")
        return f, notes

    def _generate_history(self):
        base = 22  # avg customers per hour at a mid-sized branch
        today = date.today()
        start = today - timedelta(weeks=self.WEEKS)
        hist = {}  # (date) -> {hour: count}
        d = start
        while d < today:
            wd = d.weekday()
            if wd in OPEN_DAYS:
                week_idx = (d - start).days // 7
                growth = 1 + 0.012 * week_idx  # slow growth trend
                cal, _ = self.calendar_factor(d)
                hist[d] = {}
                for h in HOURS:
                    mu = base * self._day_shape(wd) * self._hour_shape(h) * growth * cal
                    noise = self.rng.gauss(0, mu * 0.12)
                    hist[d][h] = max(0, round(mu + noise))
            d += timedelta(days=1)
        return hist

    def _fit(self):
        # For each (weekday, hour) slot compute an EWMA over the weeks (newer weeks weigh more),
        # removing the calendar effect so it can be reapplied for the target date.
        self.baseline = {}
        self.std = {}
        alpha = 0.3
        for wd in OPEN_DAYS:
            for h in HOURS:
                series = []
                for d in sorted(self.history):
                    if d.weekday() == wd:
                        cal, _ = self.calendar_factor(d)
                        series.append(self.history[d][h] / cal)
                ewma = series[0]
                for v in series[1:]:
                    ewma = alpha * v + (1 - alpha) * ewma
                mean = sum(series) / len(series)
                var = sum((v - mean) ** 2 for v in series) / max(1, len(series) - 1)
                self.baseline[(wd, h)] = ewma
                self.std[(wd, h)] = math.sqrt(var)

        # trend: last 4 weeks vs the 4 weeks before
        days = sorted(self.history)
        def total(ds):
            return sum(sum(self.history[d].values()) for d in ds)
        recent = [d for d in days if d >= days[-1] - timedelta(days=27)]
        prior = [d for d in days if days[-1] - timedelta(days=55) <= d < days[-1] - timedelta(days=27)]
        self.trend = (total(recent) / max(1, total(prior))) ** 0.5 if prior else 1.0
        self.trend = max(0.9, min(1.1, self.trend))

    def predict(self, dt: date, hour: int):
        wd = dt.weekday()
        if wd not in OPEN_DAYS or hour not in HOURS:
            return {"expected": 0, "low": 0, "high": 0}
        cal, _ = self.calendar_factor(dt)
        exp = self.baseline[(wd, hour)] * self.trend * cal
        band = 1.28 * self.std[(wd, hour)] * cal  # ~80% interval
        return {"expected": round(exp), "low": max(0, round(exp - band)), "high": round(exp + band)}

    def week_heatmap(self, start: date):
        out = []
        for i in range(7):
            dt = start + timedelta(days=i)
            _, notes = self.calendar_factor(dt)
            row = {
                "date": dt.isoformat(),
                "day": DAYS[dt.weekday()],
                "open": dt.weekday() in OPEN_DAYS,
                "notes": notes,
                "hours": [dict(hour=h, **self.predict(dt, h)) for h in HOURS],
            }
            row["total"] = sum(x["expected"] for x in row["hours"])
            out.append(row)
        return out

    @staticmethod
    def staffing_for(expected_per_hour):
        """Recommend number of counters using the service mix & avg time (Erlang-lite)."""
        mix = {"CASH": 0.55, "SERVICE": 0.28, "LOAN": 0.07, "WEALTH": 0.05, "FOREX": 0.05}
        avg_by_cat = {}
        for s in SERVICES.values():
            avg_by_cat.setdefault(s["category"], []).append(s["avg_minutes"])
        rec = {}
        for cat, share in mix.items():
            mins = sum(avg_by_cat[cat]) / len(avg_by_cat[cat])
            workload = expected_per_hour * share * mins / 60  # erlangs
            # target 80% utilisation, at least 1 for core categories
            rec[cat] = max(1 if cat in ("CASH", "SERVICE") else 0, math.ceil(workload / 0.8))
        return rec


# ---------------------------------------------------------------------------
# Queue engine
# ---------------------------------------------------------------------------
class QueueEngine:
    def __init__(self, model: FootfallModel):
        self.lock = threading.RLock()
        self.model = model
        self.counters = []
        for c in DEFAULT_COUNTERS:
            self.counters.append({**c, "status": "open", "current": None, "served": 0, "busy_minutes": 0.0})
        self.tokens = {}       # token_no -> token dict
        self.seq = {p: 0 for p in {s["prefix"] for s in SERVICES.values()}}
        self.events = []       # recent activity log
        self.day = date.today()

    # -- helpers ------------------------------------------------------------
    def _now(self):
        return datetime.now()

    def _log(self, msg):
        self.events.insert(0, {"time": self._now().strftime("%H:%M:%S"), "msg": msg})
        del self.events[60:]

    def _waiting(self):
        return [t for t in self.tokens.values() if t["status"] == "waiting"]

    def _priority_score(self, t, now=None):
        """Segment priority + aging (1 point/min) so nobody starves."""
        now = now or self._now()
        waited = (now - datetime.fromisoformat(t["issued_at"])).total_seconds() / 60
        return SEGMENTS[t["segment"]]["priority"] + waited

    def _counter_backlog_minutes(self, c):
        """Minutes of work already committed to a counter."""
        mins = 0.0
        if c["current"]:
            cur = self.tokens[c["current"]]
            elapsed = (self._now() - datetime.fromisoformat(cur["called_at"])).total_seconds() / 60
            mins += max(0.5, cur["est_service"] - elapsed)
        for t in self._waiting():
            if t["counter_id"] == c["id"]:
                mins += t["est_service"]
        return mins

    def _eligible_counters(self, category):
        return [c for c in self.counters if c["status"] == "open" and category in c["skills"]]

    def route(self, service_key, segment):
        """
        Pick the counter that minimises expected wait, with a small penalty for
        non-primary (generalist) counters so specialists stay free for their work.
        """
        cat = SERVICES[service_key]["category"]
        options = self._eligible_counters(cat)
        if not options:
            return None, None, []
        scored = []
        for c in options:
            backlog = self._counter_backlog_minutes(c)
            penalty = 0 if c["primary"] == cat else 6  # minutes-equivalent
            scored.append((backlog + penalty, backlog, c))
        scored.sort(key=lambda x: x[0])
        best = scored[0]
        reasons = [
            f"{c['name']} ({c['role']}): {round(b)} min backlog{'' if c['primary'] == cat else ', generalist'}"
            for _, b, c in scored
        ]
        return best[2], best[1], reasons

    # -- public API ---------------------------------------------------------
    def issue_token(self, service_key, segment="regular", name="", mobile="", language="", ref=""):
        if service_key not in SERVICES:
            raise ValueError("Unknown service")
        if segment not in SEGMENTS:
            raise ValueError("Unknown segment")
        with self.lock:
            svc = SERVICES[service_key]
            counter, backlog, reasons = self.route(service_key, segment)
            if counter is None:
                raise ValueError(f"No open counter currently handles {svc['category'].title()} services")
            prefix = svc["prefix"]
            self.seq[prefix] += 1
            token_no = f"{prefix}{self.seq[prefix]:03d}"
            now = self._now()
            # Priority customers effectively skip ahead: subtract a share of the backlog
            seg_boost = SEGMENTS[segment]["priority"] / 60
            est_wait = max(0, round(backlog * (1 - min(0.7, seg_boost))))
            ahead = len([t for t in self._waiting() if t["counter_id"] == counter["id"]])
            t = {
                "token": token_no,
                "service": service_key,
                "service_name": svc["name"],
                "category": svc["category"],
                "segment": segment,
                "segment_name": SEGMENTS[segment]["name"],
                "name": name.strip()[:40],
                "mobile": mobile.strip()[-4:],  # store only last 4 digits
                "language": language,           # customer's language, shown to staff
                "ref": ref,                     # assistant form or request behind this token
                "counter_id": counter["id"],
                "counter_name": counter["name"],
                "status": "waiting",
                "issued_at": now.isoformat(timespec="seconds"),
                "called_at": None,
                "completed_at": None,
                "est_service": svc["avg_minutes"],
                "est_wait": est_wait,
                "ahead": ahead,
                "routing": reasons,
            }
            self.tokens[token_no] = t
            self._log(f"Token {token_no} issued for {svc['name']} → {counter['name']}")
            return t

    def call_next(self, counter_id):
        with self.lock:
            c = self._get_counter(counter_id)
            if c["status"] != "open":
                raise ValueError("Counter is not open")
            if c["current"]:
                self._finish(c, "served")
            now = self._now()
            # Own queue first; otherwise steal the best eligible token from an overloaded counter
            own = [t for t in self._waiting() if t["counter_id"] == c["id"]]
            pool = own or [t for t in self._waiting() if t["category"] in c["skills"]]
            if not pool:
                self._log(f"{c['name']}: no customers waiting")
                return None
            nxt = max(pool, key=lambda t: self._priority_score(t, now))
            if nxt["counter_id"] != c["id"]:
                self._log(f"Token {nxt['token']} re-routed {nxt['counter_name']} → {c['name']} (load balancing)")
            nxt.update(status="serving", counter_id=c["id"], counter_name=c["name"],
                       called_at=now.isoformat(timespec="seconds"))
            c["current"] = nxt["token"]
            self._log(f"{c['name']} now serving {nxt['token']}")
            return nxt

    def complete(self, counter_id, outcome="served"):
        with self.lock:
            c = self._get_counter(counter_id)
            if not c["current"]:
                raise ValueError("Counter is not serving anyone")
            return self._finish(c, outcome)

    def _finish(self, c, outcome):
        t = self.tokens[c["current"]]
        now = self._now()
        t["status"] = outcome  # served | no_show
        t["completed_at"] = now.isoformat(timespec="seconds")
        if outcome == "served":
            c["served"] += 1
            c["busy_minutes"] += (now - datetime.fromisoformat(t["called_at"])).total_seconds() / 60
        c["current"] = None
        self._log(f"{c['name']}: {t['token']} {'completed' if outcome == 'served' else 'marked no-show'}")
        return t

    def transfer(self, token_no, service_key):
        """Customer needs a different service: re-route (keeps original issue time for fairness)."""
        with self.lock:
            t = self.tokens.get(token_no)
            if not t or t["status"] not in ("waiting", "serving"):
                raise ValueError("Token not active")
            if t["status"] == "serving":
                c = self._get_counter(t["counter_id"])
                c["current"] = None
            svc = SERVICES[service_key]
            counter, backlog, reasons = self.route(service_key, t["segment"])
            if counter is None:
                raise ValueError("No open counter for that service")
            t.update(service=service_key, service_name=svc["name"], category=svc["category"],
                     counter_id=counter["id"], counter_name=counter["name"], status="waiting",
                     called_at=None, est_service=svc["avg_minutes"], routing=reasons)
            self._log(f"Token {token_no} transferred to {svc['name']} → {counter['name']}")
            return t

    def set_counter_status(self, counter_id, status):
        if status not in ("open", "break", "closed"):
            raise ValueError("Invalid status")
        with self.lock:
            c = self._get_counter(counter_id)
            if status != "open" and c["current"]:
                raise ValueError("Finish the current customer first")
            c["status"] = status
            moved = 0
            if status != "open":
                # Re-route waiting customers of a closing counter
                for t in self._waiting():
                    if t["counter_id"] == c["id"]:
                        nc, _, reasons = self.route(t["service"], t["segment"])
                        if nc:
                            t.update(counter_id=nc["id"], counter_name=nc["name"], routing=reasons)
                            moved += 1
            self._log(f"{c['name']} set to {status}" + (f"; {moved} customer(s) re-routed" if moved else ""))
            return c

    def _get_counter(self, counter_id):
        for c in self.counters:
            if c["id"] == int(counter_id):
                return c
        raise ValueError("Counter not found")

    # -- read models --------------------------------------------------------
    def snapshot(self):
        with self.lock:
            now = self._now()
            waiting = sorted(self._waiting(), key=lambda t: -self._priority_score(t, now))
            for t in waiting:
                t["waited"] = round((now - datetime.fromisoformat(t["issued_at"])).total_seconds() / 60, 1)
            counters = []
            for c in self.counters:
                q = [t["token"] for t in waiting if t["counter_id"] == c["id"]]
                cur = self.tokens.get(c["current"]) if c["current"] else None
                counters.append({**c, "queue": q, "queue_len": len(q),
                                 "backlog": round(self._counter_backlog_minutes(c)),
                                 "current_token": cur})
            done = [t for t in self.tokens.values() if t["status"] == "served"]
            waits = [(datetime.fromisoformat(t["called_at"]) - datetime.fromisoformat(t["issued_at"])).total_seconds() / 60
                     for t in self.tokens.values() if t["called_at"]]
            breaches = len([w for w in waits if w > SLA_MINUTES]) + len([t for t in waiting if t["waited"] > SLA_MINUTES])
            hour = now.hour
            fc = self.model.predict(now.date(), hour) if hour in HOURS else {"expected": 0, "low": 0, "high": 0}
            issued_this_hour = len([t for t in self.tokens.values()
                                    if datetime.fromisoformat(t["issued_at"]).hour == hour])
            by_cat = {}
            for t in waiting:
                by_cat[t["category"]] = by_cat.get(t["category"], 0) + 1
            return {
                "time": now.strftime("%H:%M:%S"),
                "kpi": {
                    "waiting": len(waiting),
                    "serving": len([c for c in self.counters if c["current"]]),
                    "served": len(done),
                    "no_show": len([t for t in self.tokens.values() if t["status"] == "no_show"]),
                    "avg_wait": round(sum(waits) / len(waits), 1) if waits else 0,
                    "sla_breaches": breaches,
                    "issued_total": len(self.tokens),
                    "open_counters": len([c for c in self.counters if c["status"] == "open"]),
                    "forecast_this_hour": fc,
                    "issued_this_hour": issued_this_hour,
                },
                "waiting_by_category": by_cat,
                "waiting": waiting,
                "counters": counters,
                "events": self.events[:25],
                "alerts": self._alerts(counters, fc, issued_this_hour),
            }

    def _alerts(self, counters, fc, issued_this_hour):
        alerts = []
        for c in counters:
            if c["status"] == "open" and c["backlog"] > SLA_MINUTES * 1.5:
                alerts.append({"level": "warn", "msg": f"{c['name']} backlog is {c['backlog']} min — consider opening another {c['primary'].lower()} counter."})
        cats_open = {s for c in counters if c["status"] == "open" for s in c["skills"]}
        for cat in ("CASH", "SERVICE", "LOAN", "WEALTH", "FOREX"):
            if cat not in cats_open:
                alerts.append({"level": "error", "msg": f"No open counter handles {cat.title()} services."})
        rec = self.model.staffing_for(fc["expected"])
        open_primary = {}
        for c in counters:
            if c["status"] == "open":
                open_primary[c["primary"]] = open_primary.get(c["primary"], 0) + 1
        for cat, n in rec.items():
            if open_primary.get(cat, 0) < n:
                alerts.append({"level": "info", "msg": f"Forecast suggests {n} {cat.title()} counter(s) this hour; {open_primary.get(cat, 0)} open."})
        return alerts

    def display_board(self):
        with self.lock:
            serving = [{"counter": c["name"], "token": c["current"]} for c in self.counters if c["current"]]
            serving.sort(key=lambda x: self.tokens[x["token"]]["called_at"], reverse=True)
            nxt = sorted(self._waiting(), key=lambda t: -self._priority_score(t))[:8]
            return {"serving": serving, "next": [{"token": t["token"], "counter": t["counter_name"]} for t in nxt]}

    def seed_demo(self, n=14):
        """Populate a realistic morning so the dashboard is not empty."""
        rng = random.Random()
        weights = {"cash_deposit": 20, "cash_withdrawal": 18, "cheque_dd": 8, "passbook": 8, "account_open": 5,
                   "kyc_update": 6, "card_services": 6, "loan_enquiry": 4, "mortgage": 2, "wealth": 3,
                   "forex": 3, "locker": 2}
        segs = ["regular"] * 8 + ["senior", "premium", "disabled", "appointment"]
        names = ["Anita", "Rahul", "Meera", "John", "Fatima", "Arjun", "Lakshmi", "David", "Priya", "Imran",
                 "Kavya", "George", "Sunita", "Vikram", "Neha", "Ramesh"]
        with self.lock:
            base = self._now() - timedelta(minutes=25)
            for i in range(n):
                svc = rng.choices(list(weights), weights=list(weights.values()))[0]
                t = self.issue_token(svc, rng.choice(segs), rng.choice(names), "")
                t["issued_at"] = (base + timedelta(minutes=i * 1.6)).isoformat(timespec="seconds")
            for c in self.counters[:5]:
                self.call_next(c["id"])
            self._log("Demo data loaded")


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
MODEL = FootfallModel()
ENGINE = QueueEngine(MODEL)
ASSIST = Assistant(ENGINE)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FRONTEND_DIR, **kw)

    def log_message(self, fmt, *args):
        pass  # keep console quiet

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        p = url.path
        try:
            if p == "/api/meta":
                return self._json({
                    "services": SERVICES, "segments": SEGMENTS, "hours": HOURS,
                    "sla_minutes": SLA_MINUTES, "branch": "Main Street Branch",
                })
            if p == "/api/state":
                return self._json(ENGINE.snapshot())
            if p == "/api/display":
                return self._json(ENGINE.display_board())
            if p == "/api/forecast":
                start = date.fromisoformat(q["start"][0]) if "start" in q else date.today()
                week = MODEL.week_heatmap(start)
                for day in week:
                    for h in day["hours"]:
                        h["staffing"] = MODEL.staffing_for(h["expected"])
                return self._json({"week": week, "trend": round(MODEL.trend, 3),
                                   "method": "Seasonal EWMA by weekday×hour, trend-adjusted, with calendar effects"})
            if p.startswith("/api/token/"):
                t = ENGINE.tokens.get(p.rsplit("/", 1)[-1].upper())
                return self._json(t) if t else self._json({"error": "Token not found"}, 404)
            if p == "/api/assist/meta":
                return self._json(client_meta())
            if p == "/api/requests":
                return self._json(ASSIST.staff_view())
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        if p.startswith("/api/"):
            return self._json({"error": "Not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            b = self._body()
            if p == "/api/tokens":
                return self._json(ENGINE.issue_token(b.get("service"), b.get("segment", "regular"),
                                                     b.get("name", ""), b.get("mobile", "")))
            if p.startswith("/api/counters/"):
                parts = p.strip("/").split("/")  # api counters {id} {action}
                cid, action = parts[2], parts[3]
                if action == "next":
                    return self._json({"token": ENGINE.call_next(cid)})
                if action == "complete":
                    return self._json(ENGINE.complete(cid, b.get("outcome", "served")))
                if action == "status":
                    return self._json(ENGINE.set_counter_status(cid, b.get("status")))
            if p == "/api/transfer":
                return self._json(ENGINE.transfer(b.get("token", "").upper(), b.get("service")))
            if p == "/api/assist/start":
                return self._json(ASSIST.start(b.get("lang", "en")))
            if p == "/api/assist/message":
                return self._json(ASSIST.message(b.get("session", ""), b.get("text", ""), b.get("action", ""), b.get("lang", "en")))
            if p == "/api/assist/secure":
                return self._json(ASSIST.secure(b.get("session", ""), b.get("value", ""), b.get("lang", "en")))
            if p == "/api/assist/reveal":
                return self._json(ASSIST.reveal(b.get("session", ""), b.get("lang", "en")))
            if p == "/api/assist/end":
                return self._json(ASSIST.end(b.get("session", "")))
            if p.startswith("/api/requests/"):
                return self._json(ASSIST.set_status(p.strip("/").split("/")[2], b.get("status")))
            if p == "/api/demo":
                ENGINE.seed_demo()
                return self._json({"ok": True})
            if p == "/api/reset":
                with ENGINE.lock:
                    ENGINE.__init__(MODEL)
                ASSIST.__init__(ENGINE)
                return self._json({"ok": True})
            return self._json({"error": "Not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"Server error: {e}"}, 500)


if __name__ == "__main__":
    print(f"BranchFlow running at http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
