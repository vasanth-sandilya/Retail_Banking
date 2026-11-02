"""Regression: the original queue and forecast behaviour, which had no tests until now."""
import unittest
from datetime import date, datetime, timedelta

import helpers  # noqa: F401  puts backend/ on the path
from server import DEFAULT_COUNTERS, MODEL, SERVICES, QueueEngine

THURSDAY, SUNDAY, SALARY_DAY = date(2026, 11, 12), date(2026, 11, 8), date(2026, 11, 2)


def minutes_ago(n):
    return (datetime.now() - timedelta(minutes=n)).isoformat(timespec="seconds")


class Tokens(unittest.TestCase):
    def setUp(self):
        self.q = QueueEngine(MODEL)

    def test_numbers_count_up_within_each_prefix(self):
        issued = [self.q.issue_token(s)["token"] for s in ("cash_deposit", "cash_withdrawal", "loan_enquiry")]
        self.assertEqual(issued, ["C001", "C002", "L001"])

    def test_only_last_four_mobile_digits_are_stored(self):
        self.assertEqual(self.q.issue_token("passbook", mobile="9876543210")["mobile"], "3210")

    def test_long_names_are_trimmed(self):
        self.assertEqual(len(self.q.issue_token("passbook", name="x" * 100)["name"]), 40)

    def test_unknown_service_or_segment_is_refused(self):
        with self.assertRaises(ValueError):
            self.q.issue_token("nope")
        with self.assertRaises(ValueError):
            self.q.issue_token("passbook", "nope")

    def test_no_open_counter_is_reported(self):
        for c in self.q.counters:
            c["status"] = "closed"
        with self.assertRaises(ValueError):
            self.q.issue_token("forex")


class Routing(unittest.TestCase):
    def setUp(self):
        self.q = QueueEngine(MODEL)

    def test_goes_to_the_shortest_backlog(self):
        first = self.q.issue_token("cash_deposit")
        second = self.q.issue_token("cash_deposit")
        self.assertNotEqual(first["counter_id"], second["counter_id"])

    def test_specialists_are_preferred_over_generalists(self):
        self.assertEqual(self.q.issue_token("forex")["counter_name"], "Desk C")

    def test_generalist_takes_over_when_specialists_are_busy(self):
        names = {self.q.issue_token("forex")["counter_name"] for _ in range(4)}
        self.assertEqual(names, {"Desk C", "Counter 5"})

    def test_priority_customers_get_a_shorter_estimate(self):
        for _ in range(3):
            self.q.issue_token("loan_enquiry")
        regular = self.q.route("loan_enquiry", "regular")[1]
        senior = self.q.issue_token("loan_enquiry", "senior")
        self.assertLess(senior["est_wait"], regular)

    def test_every_service_can_be_issued(self):
        for key in SERVICES:
            self.assertTrue(QueueEngine(MODEL).issue_token(key)["token"])

    def test_every_counter_handles_only_known_categories(self):
        categories = {s["category"] for s in SERVICES.values()}
        for c in DEFAULT_COUNTERS:
            self.assertTrue(set(c["skills"]) <= categories, c["name"])
            self.assertIn(c["primary"], c["skills"])


class Serving(unittest.TestCase):
    def setUp(self):
        self.q = QueueEngine(MODEL)

    def test_senior_is_called_before_an_earlier_regular(self):
        self.q.set_counter_status(7, "closed")  # leaves one desk, so both customers share a queue
        self.q.issue_token("loan_enquiry", "regular")
        senior = self.q.issue_token("loan_enquiry", "senior")
        self.assertEqual(self.q.call_next(6)["token"], senior["token"])

    def test_long_wait_beats_priority_so_nobody_starves(self):
        self.q.set_counter_status(7, "closed")
        old = self.q.issue_token("loan_enquiry", "regular")
        self.q.issue_token("loan_enquiry", "senior")
        old["issued_at"] = minutes_ago(45)
        self.assertEqual(self.q.call_next(6)["token"], old["token"])

    def test_call_next_finishes_the_current_customer(self):
        first = self.q.issue_token("loan_enquiry")
        self.q.call_next(6)
        self.q.issue_token("loan_enquiry")
        self.q.call_next(6)
        self.assertEqual(first["status"], "served")

    def test_empty_queue_returns_nothing(self):
        self.assertIsNone(self.q.call_next(1))

    def test_idle_counter_takes_from_a_busier_queue(self):
        t = self.q.issue_token("cash_deposit")
        other = next(c["id"] for c in self.q.counters if "CASH" in c["skills"] and c["id"] != t["counter_id"])
        self.assertEqual(self.q.call_next(other)["counter_id"], other)

    def test_counter_never_takes_a_service_it_lacks(self):
        self.q.issue_token("wealth")
        self.assertIsNone(self.q.call_next(1))

    def test_no_show_is_not_counted_as_served(self):
        t = self.q.issue_token("loan_enquiry")
        self.q.call_next(6)
        self.q.complete(6, "no_show")
        kpi = self.q.snapshot()["kpi"]
        self.assertEqual((t["status"], kpi["served"], kpi["no_show"]), ("no_show", 0, 1))

    def test_complete_needs_a_current_customer(self):
        with self.assertRaises(ValueError):
            self.q.complete(1)

    def test_closed_counter_cannot_call(self):
        self.q.set_counter_status(1, "closed")
        with self.assertRaises(ValueError):
            self.q.call_next(1)


class Transfers(unittest.TestCase):
    def setUp(self):
        self.q = QueueEngine(MODEL)

    def test_transfer_keeps_the_original_issue_time(self):
        t = self.q.issue_token("cash_deposit")
        issued = t["issued_at"]
        self.q.call_next(t["counter_id"])
        moved = self.q.transfer(t["token"], "kyc_update")
        self.assertEqual((moved["issued_at"], moved["status"], moved["category"]), (issued, "waiting", "SERVICE"))

    def test_transfer_frees_the_counter(self):
        t = self.q.issue_token("cash_deposit")
        counter = t["counter_id"]
        self.q.call_next(counter)
        self.q.transfer(t["token"], "kyc_update")
        self.assertIsNone(self.q._get_counter(counter)["current"])

    def test_finished_tokens_cannot_transfer(self):
        t = self.q.issue_token("loan_enquiry")
        self.q.call_next(6)
        self.q.complete(6)
        with self.assertRaises(ValueError):
            self.q.transfer(t["token"], "forex")

    def test_closing_a_counter_reroutes_its_customers(self):
        t = self.q.issue_token("cash_deposit")
        counter = t["counter_id"]
        self.q.set_counter_status(counter, "break")
        self.assertNotEqual(t["counter_id"], counter)

    def test_counter_cannot_close_while_serving(self):
        t = self.q.issue_token("cash_deposit")
        self.q.call_next(t["counter_id"])
        with self.assertRaises(ValueError):
            self.q.set_counter_status(t["counter_id"], "closed")


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.q = QueueEngine(MODEL)

    def test_waits_over_the_target_count_as_breaches(self):
        self.q.issue_token("loan_enquiry")["issued_at"] = minutes_ago(20)
        self.q.issue_token("forex")
        self.assertEqual(self.q.snapshot()["kpi"]["sla_breaches"], 1)

    def test_waiting_list_is_ordered_by_priority(self):
        self.q.issue_token("loan_enquiry", "regular")
        self.q.issue_token("loan_enquiry", "disabled")
        self.assertEqual(self.q.snapshot()["waiting"][0]["segment"], "disabled")

    def test_closing_all_loan_desks_raises_an_alert(self):
        for cid in (6, 7):
            self.q.set_counter_status(cid, "closed")
        self.assertTrue(any("Loan" in a["msg"] and a["level"] == "error" for a in self.q.snapshot()["alerts"]))

    def test_activity_log_is_capped(self):
        for _ in range(80):
            self.q._log("event")
        self.assertEqual(len(self.q.events), 60)

    def test_display_board_shows_who_is_being_served(self):
        t = self.q.issue_token("loan_enquiry")
        self.q.call_next(6)
        self.assertEqual(self.q.display_board()["serving"][0], {"counter": "Desk A", "token": t["token"]})

    def test_demo_customers_fill_the_dashboard(self):
        self.q.seed_demo()
        kpi = self.q.snapshot()["kpi"]
        self.assertGreaterEqual(kpi["issued_total"], 14)
        self.assertGreater(kpi["serving"], 0)


class Forecast(unittest.TestCase):
    def test_sunday_is_closed(self):
        self.assertEqual(MODEL.predict(SUNDAY, 11), {"expected": 0, "low": 0, "high": 0})

    def test_hours_outside_the_branch_day_are_empty(self):
        self.assertEqual(MODEL.predict(THURSDAY, 18)["expected"], 0)

    def test_range_contains_the_estimate(self):
        for h in range(9, 17):
            p = MODEL.predict(THURSDAY, h)
            self.assertLessEqual(p["low"], p["expected"])
            self.assertLessEqual(p["expected"], p["high"])

    def test_calendar_effects(self):
        self.assertEqual(MODEL.calendar_factor(date(2026, 11, 2))[0], 1.30)
        self.assertEqual(MODEL.calendar_factor(date(2026, 11, 29))[0], 1.15)
        self.assertEqual(MODEL.calendar_factor(date(2026, 11, 10))[0], 1.08)
        self.assertEqual(MODEL.calendar_factor(date(2026, 11, 12))[0], 1.0)

    def test_late_morning_is_busier_than_opening(self):
        self.assertGreater(MODEL.predict(THURSDAY, 11)["expected"], MODEL.predict(THURSDAY, 9)["expected"])

    def test_week_view_has_seven_days_and_closed_sunday(self):
        week = MODEL.week_heatmap(date(2026, 11, 2))
        self.assertEqual(len(week), 7)
        self.assertFalse(week[6]["open"])
        self.assertEqual(week[6]["total"], 0)

    def test_staffing_keeps_core_counters_open(self):
        rec = MODEL.staffing_for(0)
        self.assertEqual((rec["CASH"], rec["SERVICE"], rec["LOAN"]), (1, 1, 0))

    def test_staffing_grows_with_demand(self):
        self.assertGreater(MODEL.staffing_for(60)["CASH"], MODEL.staffing_for(10)["CASH"])

    def test_forecast_is_repeatable(self):
        self.assertEqual(type(MODEL)().baseline, MODEL.baseline)
