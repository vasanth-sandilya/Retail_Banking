"""
Mitra appointments - RM and specialist booking, no-show prediction, overbooking.

Standard library only. The model trains on synthetic history at start-up, like
FootfallModel. To use real data, replace NoShowModel._generate_history().

Rules that protect customers:
  - the model uses booking behaviour only, never name, language, age, disability
    or customer segment
  - risk scores are for staff; they never reach customer-facing replies
  - senior and differently abled customers are never put in or joined to an
    overbooked slot
"""
import math
import random
import secrets
from datetime import date, datetime, timedelta

SLOT_MINUTES = 30
LUNCH = ("13:00", "13:30")
TIMES = [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 30) if f"{h:02d}:{m:02d}" not in LUNCH]
OPEN_DAYS = (0, 1, 2, 3, 4, 5)  # Sunday closed
BOOKING_HORIZON_DAYS = 30
LATE_AFTER_MINUTES = 15
HIGH_RISK = 0.30
MEDIUM_RISK = 0.15

ADVISORS = [
    {"id": "rm1",    "name": "R. Menon",  "role": "Relationship Manager", "type": "rm",     "desk": "Desk D", "counter_id": 9},
    {"id": "rm2",    "name": "S. Bose",   "role": "Relationship Manager", "type": "rm",     "desk": "Desk E", "counter_id": 10},
    {"id": "loan",   "name": "V. Rao",    "role": "Loan Specialist",      "type": "loan",   "desk": "Desk A", "counter_id": 6},
    {"id": "wealth", "name": "N. Kapoor", "role": "Wealth Advisor",       "type": "wealth", "desk": "Desk B", "counter_id": 7},
    {"id": "forex",  "name": "J. Thomas", "role": "Forex Specialist",     "type": "forex",  "desk": "Desk C", "counter_id": 8},
]
TYPES = {
    "rm":     {"name": "Relationship Manager", "service": "rm_meeting"},
    "loan":   {"name": "Loan Specialist",      "service": "loan_enquiry"},
    "wealth": {"name": "Wealth Advisor",       "service": "wealth"},
    "forex":  {"name": "Forex Specialist",     "service": "forex"},
}
CHANNELS = ("branch", "kiosk", "phone", "app")
SEGMENTS = ("regular", "senior", "disabled")

# Overbook when the chance a slot sits idle beats the cost of two customers
# arriving together: P(idle) > wait_cost / (wait_cost + idle_cost). These are that
# ratio for a wait costing about 1, 2 and 3 times an idle slot.
POLICY_LEVELS = {"aggressive": 0.25, "balanced": 0.33, "cautious": 0.50}
MAX_OVERBOOKED_PER_DAY = 2  # per advisor; adjacent overbooked slots are also refused


def _sigmoid(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def _edge_of_day(time):
    return time == TIMES[0] or time == TIMES[-1]


class NoShowModel:
    """
    Logistic regression on synthetic appointment history.

    Each score comes with the factors that raised it, so staff can see why an
    appointment is flagged and act on it, for example by calling to confirm.
    """
    FEATURES = ["lead", "first_visit", "prior_rate", "not_confirmed", "edge_slot", "saturday", "salary_day", "remote"]
    TRUE_WEIGHTS = [0.55, 0.50, 2.40, 0.90, 0.30, 0.25, 0.35, 0.30]
    TRUE_BIAS = -3.1
    TRAIN_ROWS, TEST_ROWS, ITERATIONS, LEARNING_RATE = 800, 400, 200, 0.4

    def __init__(self, seed=11):
        self.rng = random.Random(seed)
        rows = self._generate_history(self.TRAIN_ROWS + self.TEST_ROWS)
        train, test = rows[:self.TRAIN_ROWS], rows[self.TRAIN_ROWS:]
        self._fit(train)
        self.auc = round(self._auc(test), 3)
        self.base_rate = round(sum(y for _, y in rows) / len(rows), 3)

    @staticmethod
    def features(lead_days, first_visit, prior_visits, prior_no_shows, confirmed, day, time, channel):
        prior_rate = prior_no_shows / (prior_visits + 2)  # smoothed: one miss in one visit is weak evidence
        return [
            math.log1p(max(0, lead_days)),
            1.0 if first_visit else 0.0,
            prior_rate,
            0.0 if confirmed else 1.0,
            1.0 if _edge_of_day(time) else 0.0,
            1.0 if day.weekday() == 5 else 0.0,
            1.0 if day.day in (1, 2, 3) else 0.0,
            1.0 if channel in ("phone", "app") else 0.0,
        ]

    def _generate_history(self, n):
        rng = self.rng
        rows = []
        for _ in range(n):
            first = rng.random() < 0.25
            visits = 0 if first else rng.randint(1, 12)
            missed = sum(rng.random() < 0.12 for _ in range(visits))
            day = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
            x = self.features(rng.choice([0, 1, 1, 2, 3, 5, 7, 10, 14, 21]), first, visits, missed, rng.random() < 0.55,
                              day, rng.choice(TIMES), rng.choice(CHANNELS))
            z = self.TRUE_BIAS + sum(w * v for w, v in zip(self.TRUE_WEIGHTS, x))
            rows.append((x, 1 if rng.random() < _sigmoid(z) else 0))
        return rows

    def _fit(self, rows):
        n, k = len(rows), len(self.FEATURES)
        self.means = [sum(x[j] for x, _ in rows) / n for j in range(k)]
        # Scale each feature: unscaled, small-range ones like prior_rate barely learn
        self.scales = [math.sqrt(sum((x[j] - self.means[j]) ** 2 for x, _ in rows) / n) or 1.0 for j in range(k)]
        data = [(self._scaled(x), y) for x, y in rows]
        self.weights, self.bias = [0.0] * k, 0.0
        for _ in range(self.ITERATIONS):
            grad, grad_bias = [0.0] * k, 0.0
            for x, y in data:
                err = _sigmoid(self.bias + sum(w * v for w, v in zip(self.weights, x))) - y
                grad_bias += err
                for j in range(k):
                    grad[j] += err * x[j]
            self.bias -= self.LEARNING_RATE * grad_bias / n
            self.weights = [w - self.LEARNING_RATE * g / n for w, g in zip(self.weights, grad)]

    def _scaled(self, x):
        return [(v - m) / s for v, m, s in zip(x, self.means, self.scales)]

    def score(self, x):
        return _sigmoid(self.bias + sum(w * v for w, v in zip(self.weights, self._scaled(x))))

    def _auc(self, rows):
        scored = sorted((self.score(x), y) for x, y in rows)
        positives = sum(y for _, y in scored)
        negatives = len(scored) - positives
        rank_sum = sum(i + 1 for i, (_, y) in enumerate(scored) if y)
        return (rank_sum - positives * (positives + 1) / 2) / max(1, positives * negatives)

    def reasons(self, x, lead_days):
        labels = {
            "lead": f"Booked {lead_days} days ahead",
            "first_visit": "First visit",
            "prior_rate": "Missed earlier appointments",
            "not_confirmed": "Not confirmed yet",
            "edge_slot": "First or last slot of the day",
            "saturday": "Saturday slot",
            "salary_day": "Salary and pension days",
            "remote": "Booked by phone or app",
        }
        impact = [(w * v, f) for w, v, f in zip(self.weights, self._scaled(x), self.FEATURES)]
        return [labels[f] for gain, f in sorted(impact, reverse=True)[:3] if gain > 0.05]

    def describe(self):
        return {"method": "Logistic regression on synthetic history", "auc": self.auc, "base_rate": self.base_rate}


class AppointmentBook:
    def __init__(self, engine, model):
        self.engine = engine
        self.model = model
        self.lock = engine.lock  # shared: check-in and token completion cross both objects
        self.items = {}
        self.policy = {"enabled": True, "level": "balanced"}
        self.clock = datetime.now
        engine.on_finish = self._token_finished

    # -- helpers ------------------------------------------------------------
    def _today(self):
        return self.clock().date()

    def _advisors(self, kind):
        return [a for a in ADVISORS if a["type"] == kind]

    def _at(self, advisor_id, day, time):
        return [a for a in self.items.values()
                if a["advisor_id"] == advisor_id and a["date"] == day and a["time"] == time
                and a["status"] in ("booked", "checked_in")]

    def _new_ref(self):
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O or 1/I, and hard to guess
        while True:
            ref = "AP-" + "".join(secrets.choice(alphabet) for _ in range(4))
            if ref not in self.items:
                return ref

    def _rescore(self, a):
        day = date.fromisoformat(a["date"])
        x = self.model.features(a["lead_days"], a["first_visit"], a["prior_visits"], a["prior_no_shows"],
                                a["confirmed"], day, a["time"], a["channel"])
        a["risk"] = round(self.model.score(x), 3)
        a["reasons"] = self.model.reasons(x, a["lead_days"])

    def _get(self, ref):
        a = self.items.get(ref)
        if not a:
            raise ValueError("Appointment not found")
        return a

    def _slot_started(self, a):
        return self.clock() >= datetime.fromisoformat(f"{a['date']}T{a['time']}")

    # -- availability -------------------------------------------------------
    def open_days(self, count=6):
        """Next open days that still have slots left."""
        now = self.clock()
        out, day = [], now.date()
        while len(out) < count:
            still_open = day > now.date() or now.strftime("%H:%M") < TIMES[-1]
            if day.weekday() in OPEN_DAYS and still_open:
                out.append(day.isoformat())
            day += timedelta(days=1)
        return out

    def overbook_ok(self, advisor_id, day, time):
        if not self.policy["enabled"]:
            return False
        present = self._at(advisor_id, day, time)
        if len(present) != 1 or present[0]["status"] != "booked" or present[0]["segment"] != "regular":
            return False
        if present[0]["risk"] < POLICY_LEVELS[self.policy["level"]]:
            return False
        i = TIMES.index(time)
        for neighbour in TIMES[max(0, i - 1):i] + TIMES[i + 1:i + 2]:
            if len(self._at(advisor_id, day, neighbour)) > 1:
                return False
        doubled = sum(len(self._at(advisor_id, day, t)) > 1 for t in TIMES)
        return doubled < MAX_OVERBOOKED_PER_DAY

    def available(self, kind, day, segment="regular"):
        """
        Open slots first. Overbooking is offered only once a day has no free slot
        left for this appointment type, and never to priority customers.
        """
        if kind not in TYPES:
            raise ValueError("Unknown appointment type")
        day_obj = date.fromisoformat(day)
        now = self.clock()
        if day_obj.weekday() not in OPEN_DAYS or day_obj < now.date():
            return []
        times = [t for t in TIMES if day_obj > now.date() or t > now.strftime("%H:%M")]
        free = {}
        for t in times:
            for adv in self._advisors(kind):
                if not self._at(adv["id"], day, t):
                    free.setdefault(t, adv)
        if free or segment != "regular":
            return [{"time": t, "advisor_id": a["id"], "advisor": a["name"], "overbook": False} for t, a in free.items()]
        extra = {}
        for t in times:
            for adv in self._advisors(kind):
                if self.overbook_ok(adv["id"], day, t):
                    extra.setdefault(t, adv)
        return [{"time": t, "advisor_id": a["id"], "advisor": a["name"], "overbook": True} for t, a in extra.items()]

    # -- booking ------------------------------------------------------------
    def create(self, kind, day, time, name, mobile="", segment="regular", channel="branch", language="",
               first_visit=True, prior_visits=0, prior_no_shows=0, confirmed=False):
        if segment not in SEGMENTS:
            raise ValueError("Unknown customer type")
        if channel not in CHANNELS:
            raise ValueError("Unknown booking channel")
        name = str(name or "").strip()[:40]
        if len(name) < 2:
            raise ValueError("Please enter the customer's name")
        with self.lock:
            day_obj = date.fromisoformat(day)
            if (day_obj - self._today()).days > BOOKING_HORIZON_DAYS:
                raise ValueError(f"Appointments can be booked up to {BOOKING_HORIZON_DAYS} days ahead")
            slot = next((s for s in self.available(kind, day, segment) if s["time"] == time), None)
            if not slot:
                raise ValueError("That slot is no longer available")
            ref = self._new_ref()
            a = {
                "ref": ref, "type": kind, "type_name": TYPES[kind]["name"], "advisor_id": slot["advisor_id"],
                "advisor": slot["advisor"], "date": day, "time": time, "name": name,
                "mobile": str(mobile or "").strip()[-4:], "segment": segment, "channel": channel,
                "language": language, "lead_days": (day_obj - self._today()).days, "first_visit": first_visit,
                "prior_visits": prior_visits, "prior_no_shows": prior_no_shows, "confirmed": confirmed,
                "status": "booked", "token": "",
            }
            self._rescore(a)
            self.items[ref] = a
            return a

    def confirm(self, ref):
        """Customer confirmed by phone or message. Lowers the score for the same customer and slot."""
        with self.lock:
            a = self._get(ref)
            if a["status"] != "booked":
                raise ValueError("Only booked appointments can be confirmed")
            a["confirmed"] = True
            self._rescore(a)
            return a

    def cancel(self, ref):
        with self.lock:
            a = self._get(ref)
            if a["status"] != "booked":
                raise ValueError("Only booked appointments can be cancelled")
            a["status"] = "cancelled"
            return a

    def check_in(self, ref):
        """Puts the customer in the live queue, at their advisor's desk when it is open."""
        with self.lock:
            a = self._get(ref)
            if a["status"] != "booked":
                raise ValueError("This appointment is not waiting for check-in")
            if a["date"] != self._today().isoformat():
                raise ValueError("Check-in is only possible on the day of the appointment")
            advisor = next(x for x in ADVISORS if x["id"] == a["advisor_id"])
            t = self.engine.issue_token(TYPES[a["type"]]["service"], "appointment", a["name"], "", a["language"], a["ref"],
                                        counter_id=advisor["counter_id"])
            a.update(status="checked_in", token=t["token"])
            return a, t

    def complete(self, ref):
        with self.lock:
            a = self._get(ref)
            if a["status"] != "checked_in":
                raise ValueError("Check the customer in first")
            a["status"] = "completed"
            return a

    def mark_no_show(self, ref):
        with self.lock:
            a = self._get(ref)
            if a["status"] != "booked":
                raise ValueError("Only booked appointments can be marked as no-show")
            if not self._slot_started(a):
                raise ValueError("This appointment has not started yet")
            a["status"] = "no_show"
            return a

    def _token_finished(self, token, outcome):
        a = self.items.get(token.get("ref"))
        if a and a["status"] == "checked_in":
            a["status"] = "completed" if outcome == "served" else "no_show"

    def set_policy(self, enabled=None, level=None):
        with self.lock:
            if level is not None:
                if level not in POLICY_LEVELS:
                    raise ValueError("Unknown overbooking level")
                self.policy["level"] = level
            if enabled is not None:
                self.policy["enabled"] = bool(enabled)
            return self.policy

    # -- read models --------------------------------------------------------
    def _view(self, a):
        now = self.clock()
        start = datetime.fromisoformat(f"{a['date']}T{a['time']}")
        return {
            **a,
            "band": "high" if a["risk"] >= HIGH_RISK else "medium" if a["risk"] >= MEDIUM_RISK else "low",
            "late": a["status"] == "booked" and now > start + timedelta(minutes=LATE_AFTER_MINUTES),
            "started": now >= start,
            "overbooked": len(self._at(a["advisor_id"], a["date"], a["time"])) > 1,
        }

    def day_view(self, day):
        with self.lock:
            date.fromisoformat(day)
            rows = [self._view(a) for a in self.items.values() if a["date"] == day and a["status"] != "cancelled"]
            rows.sort(key=lambda a: (a["time"], a["advisor_id"]))
            booked = [a for a in rows if a["status"] == "booked"]
            idle = 0.0
            for adv in ADVISORS:
                for t in TIMES:
                    present = [a for a in booked if a["advisor_id"] == adv["id"] and a["time"] == t]
                    if present:
                        idle += math.prod(a["risk"] for a in present) * SLOT_MINUTES
            call_list = sorted((a for a in booked if a["band"] == "high" and not a["confirmed"] and not a["started"]),
                               key=lambda a: -a["risk"])
            candidates = [{"advisor_id": s["advisor_id"], "advisor": s["advisor"], "time": s["time"]}
                          for kind in TYPES for s in self.available(kind, day) if s["overbook"]]
            return {
                "date": day, "advisors": ADVISORS, "times": TIMES, "appointments": rows, "call_list": call_list,
                "candidates": candidates, "policy": self.policy, "levels": list(POLICY_LEVELS),
                "types": {k: v["name"] for k, v in TYPES.items()}, "model": self.model.describe(),
                "kpi": {
                    "booked": len(booked),
                    "predicted_no_shows": round(sum(a["risk"] for a in booked), 1),
                    "idle_minutes": round(idle),
                    "high_risk": sum(a["band"] == "high" for a in booked),
                    "overbooked_slots": len({(a["advisor_id"], a["time"]) for a in rows if a["overbooked"]}),
                },
            }

    def seed_demo(self):
        """A plausible week of synthetic bookings, so the screen is not empty."""
        rng = random.Random(23)
        names = ["Anita Rao", "Rahul Shah", "Meera Pillai", "John Mathew", "Fatima Khan", "Arjun Reddy", "Lakshmi Iyer",
                 "David Dsouza", "Priya Nair", "Imran Ali", "Kavya Hegde", "George Thomas", "Sunita Patil", "Vikram Sen"]
        now = self.clock()
        with self.lock:
            days = self.open_days()
            for day in days:
                for adv in ADVISORS:
                    # One fully booked specialist day, so overbooking can be seen in the demo
                    full = adv["id"] == "forex" and day == days[1]
                    for t in TIMES:
                        if not full and rng.random() > 0.55:
                            continue
                        first = rng.random() < 0.2
                        visits = 0 if first else rng.randint(1, 10)
                        a = self._seed_one(rng, adv, day, t, rng.choice(names), first, visits)
                        if datetime.fromisoformat(f"{day}T{t}") + timedelta(minutes=SLOT_MINUTES) <= now:
                            a["status"] = "no_show" if rng.random() < a["risk"] else "completed"

    def _seed_one(self, rng, adv, day, t, name, first, visits):
        day_obj = date.fromisoformat(day)
        lead = (day_obj - self._today()).days + rng.choice([0, 1, 2, 4, 7, 12])
        ref = self._new_ref()
        a = {
            "ref": ref, "type": adv["type"], "type_name": TYPES[adv["type"]]["name"], "advisor_id": adv["id"],
            "advisor": adv["name"], "date": day, "time": t, "name": name, "mobile": f"{rng.randint(0, 9999):04d}",
            "segment": rng.choice(["regular"] * 7 + ["senior", "disabled"]), "channel": rng.choice(CHANNELS),
            "language": "", "lead_days": lead, "first_visit": first, "prior_visits": visits,
            "prior_no_shows": sum(rng.random() < 0.15 for _ in range(visits)), "confirmed": rng.random() < 0.45,
            "status": "booked", "token": "",
        }
        self._rescore(a)
        self.items[ref] = a
        return a
