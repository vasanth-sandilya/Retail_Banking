"""Run with: python -m unittest discover tests"""
import inspect
import json
import math
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import appointments as AP
import assistant as A
from assistant_i18n import LANG_CODES, T
from server import MODEL, QueueEngine

MONDAY_10AM = datetime(2026, 11, 2, 10, 0)
TUESDAY, WEDNESDAY, SUNDAY = "2026-11-03", "2026-11-04", "2026-11-08"
NO_SHOW_MODEL = AP.NoShowModel()
RISKY = dict(first_visit=False, prior_visits=2, prior_no_shows=2)
SAFE = dict(first_visit=False, prior_visits=20, prior_no_shows=0, confirmed=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.engine = QueueEngine(MODEL)
        self.book = AP.AppointmentBook(self.engine, NO_SHOW_MODEL)
        self.book.clock = lambda: MONDAY_10AM

    def fill(self, kind="forex", day=TUESDAY, **history):
        return [self.book.create(kind, day, t, "Anita Rao", **history) for t in AP.TIMES]


class Model(unittest.TestCase):
    def test_learns_the_known_pattern(self):
        m = NO_SHOW_MODEL
        per_unit = dict(zip(m.FEATURES, (w / s for w, s in zip(m.weights, m.scales))))
        self.assertGreater(per_unit["prior_rate"], 1.5)
        self.assertGreater(per_unit["not_confirmed"], 0.5)
        self.assertGreater(per_unit["lead"], 0.3)
        self.assertGreater(m.auc, 0.6)

    def test_scores_are_calibrated(self):
        m = NO_SHOW_MODEL
        rows = m._generate_history(1500)
        predicted = sum(m.score(x) for x, _ in rows) / len(rows)
        actual = sum(y for _, y in rows) / len(rows)
        self.assertLess(abs(predicted - actual), 0.05)

    def test_inputs_exclude_protected_details(self):
        names = set(inspect.signature(AP.NoShowModel.features).parameters)
        self.assertFalse(names & {"name", "language", "segment", "age", "gender", "mobile"})

    def test_reasons_explain_a_risky_booking(self):
        x = NO_SHOW_MODEL.features(14, False, 2, 2, False, datetime(2026, 11, 3).date(), "11:00", "branch")
        self.assertIn("Missed earlier appointments", NO_SHOW_MODEL.reasons(x, 14))


class Booking(Base):
    def test_risk_ignores_who_the_customer_is(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao", segment="senior", language="தமிழ்")
        b = self.book.create("rm", TUESDAY, "11:00", "John Mathew", segment="regular", language="English")
        self.assertEqual(a["risk"], b["risk"])

    def test_two_rms_share_the_type(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao")
        b = self.book.create("rm", TUESDAY, "11:00", "John Mathew")
        self.assertEqual({a["advisor_id"], b["advisor_id"]}, {"rm1", "rm2"})

    def test_confirming_lowers_risk(self):
        a = self.book.create("rm", WEDNESDAY, "11:00", "Anita Rao")
        before = a["risk"]
        self.assertLess(self.book.confirm(a["ref"])["risk"], before)

    def test_slots_exclude_lunch_past_and_closed_days(self):
        today = [s["time"] for s in self.book.available("rm", "2026-11-02")]
        self.assertNotIn("09:30", today)
        self.assertNotIn("10:00", today)
        self.assertNotIn("13:00", today)
        self.assertIn("10:30", today)
        self.assertEqual(self.book.available("rm", SUNDAY), [])

    def test_invalid_bookings_are_refused(self):
        for args in (("rm", "2026-12-20", "11:00", "Anita Rao"), ("rm", TUESDAY, "13:00", "Anita Rao"),
                     ("rm", TUESDAY, "11:00", "A"), ("rm", "2026-11-02", "09:00", "Anita Rao")):
            with self.assertRaises(ValueError):
                self.book.create(*args)
        with self.assertRaises(ValueError):
            self.book.available("nope", TUESDAY)

    def test_taken_slot_cannot_be_double_booked(self):
        self.book.create("forex", TUESDAY, "11:00", "Anita Rao")
        with self.assertRaises(ValueError):
            self.book.create("forex", TUESDAY, "11:00", "John Mathew")

    def test_cancel_frees_the_slot(self):
        a = self.book.create("forex", TUESDAY, "11:00", "Anita Rao")
        self.book.cancel(a["ref"])
        self.book.create("forex", TUESDAY, "11:00", "John Mathew")

    def test_only_last_four_mobile_digits_are_kept(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao", "9876543210")
        self.assertEqual(a["mobile"], "3210")

    def test_references_are_not_sequential(self):
        refs = [self.book.create("rm", TUESDAY, t, "Anita Rao")["ref"] for t in AP.TIMES[:6]]
        self.assertEqual(len(set(refs)), 6)
        for ref in refs:
            self.assertRegex(ref, r"^AP-[2-9A-HJ-NP-Z]{4}$")


class Overbooking(Base):
    def test_not_offered_while_a_normal_slot_is_free(self):
        self.book.create("forex", TUESDAY, "11:00", "Anita Rao", **RISKY)
        self.assertFalse(any(s["overbook"] for s in self.book.available("forex", TUESDAY)))

    def test_offered_on_high_risk_slots_when_the_day_is_full(self):
        booked = self.fill(**RISKY)
        self.assertTrue(all(a["risk"] >= AP.POLICY_LEVELS["balanced"] for a in booked))
        slots = self.book.available("forex", TUESDAY)
        self.assertTrue(slots and all(s["overbook"] for s in slots))

    def test_not_offered_on_low_risk_slots(self):
        booked = self.fill(**SAFE)
        self.assertTrue(all(a["risk"] < AP.POLICY_LEVELS["balanced"] for a in booked))
        self.assertEqual(self.book.available("forex", TUESDAY), [])

    def test_priority_customers_never_get_an_overbooked_slot(self):
        self.fill(**RISKY)
        for segment in ("senior", "disabled"):
            self.assertEqual(self.book.available("forex", TUESDAY, segment), [])

    def test_never_joins_a_priority_customer(self):
        booked = self.fill(segment="senior", **RISKY)
        self.assertTrue(booked)
        self.assertEqual(self.book.available("forex", TUESDAY), [])

    def test_policy_switch_turns_it_off(self):
        self.fill(**RISKY)
        self.book.set_policy(enabled=False)
        self.assertEqual(self.book.available("forex", TUESDAY), [])

    def test_stricter_policy_offers_less(self):
        self.fill(**RISKY)
        self.book.set_policy(level="aggressive")
        loose = len(self.book.available("forex", TUESDAY))
        self.book.set_policy(level="cautious")
        self.assertLessEqual(len(self.book.available("forex", TUESDAY)), loose)
        with self.assertRaises(ValueError):
            self.book.set_policy(level="reckless")

    def test_daily_cap_and_no_adjacent_slots(self):
        self.fill(**RISKY)
        taken = []
        while True:
            slots = self.book.available("forex", TUESDAY)
            if not slots:
                break
            taken.append(slots[0]["time"])
            self.book.create("forex", TUESDAY, slots[0]["time"], "John Mathew")
        self.assertEqual(len(taken), AP.MAX_OVERBOOKED_PER_DAY)
        i, j = (AP.TIMES.index(t) for t in taken)
        self.assertGreater(abs(i - j), 1)

    def test_third_customer_never_joins_a_slot(self):
        self.fill(**RISKY)
        time = self.book.available("forex", TUESDAY)[0]["time"]
        self.book.create("forex", TUESDAY, time, "John Mathew")
        with self.assertRaises(ValueError):
            self.book.create("forex", TUESDAY, time, "Meera Pillai")

    def test_calendar_hint_matches_what_can_be_booked(self):
        self.book.create("forex", TUESDAY, "11:00", "Anita Rao", **RISKY)
        self.assertEqual(self.book.day_view(TUESDAY)["candidates"], [])
        for t in AP.TIMES:
            if t != "11:00":
                self.book.create("forex", TUESDAY, t, "Anita Rao", **RISKY)
        hints = [c["time"] for c in self.book.day_view(TUESDAY)["candidates"]]
        self.assertEqual(hints, [s["time"] for s in self.book.available("forex", TUESDAY)])

    def test_day_view_reports_expected_idle_time(self):
        a = self.book.create("forex", TUESDAY, "11:00", "Anita Rao", **RISKY)
        kpi = self.book.day_view(TUESDAY)["kpi"]
        self.assertEqual(kpi["idle_minutes"], round(a["risk"] * AP.SLOT_MINUTES))
        self.assertEqual(kpi["high_risk"], 1)

    def test_overbooked_slot_expects_less_idle_time(self):
        self.fill(**RISKY)
        before = self.book.day_view(TUESDAY)["kpi"]
        self.book.create("forex", TUESDAY, self.book.available("forex", TUESDAY)[0]["time"], "John Mathew")
        after = self.book.day_view(TUESDAY)["kpi"]
        self.assertLess(after["idle_minutes"], before["idle_minutes"])
        self.assertEqual(after["overbooked_slots"], 1)

    def test_call_list_has_unconfirmed_high_risk_only(self):
        a = self.book.create("forex", TUESDAY, "11:00", "Anita Rao", **RISKY)
        self.assertEqual([x["ref"] for x in self.book.day_view(TUESDAY)["call_list"]], [a["ref"]])
        self.book.confirm(a["ref"])
        self.assertEqual(self.book.day_view(TUESDAY)["call_list"], [])


class QueueLink(Base):
    def today(self, kind="rm"):
        return self.book.create(kind, "2026-11-02", "11:00", "Anita Rao", language="தமிழ்")

    def test_check_in_goes_to_the_advisors_own_desk(self):
        for _ in range(3):  # enough backlog that routing alone would pick the other desk
            self.engine.issue_token("rm_meeting", counter_id=9)
        a = self.today()
        _, t = self.book.check_in(a["ref"])
        advisor = next(x for x in AP.ADVISORS if x["id"] == a["advisor_id"])
        self.assertEqual(t["counter_id"], advisor["counter_id"])
        self.assertEqual((t["segment"], t["ref"], t["language"]), ("appointment", a["ref"], "தமிழ்"))

    def test_idle_desk_does_not_take_a_pinned_customer(self):
        a = self.today()
        _, t = self.book.check_in(a["ref"])
        other = 10 if t["counter_id"] == 9 else 9
        self.assertIsNone(self.engine.call_next(other))
        self.assertEqual(self.engine.call_next(t["counter_id"])["token"], t["token"])

    def test_closed_desk_falls_back_to_normal_routing(self):
        a = self.today("loan")
        self.engine.set_counter_status(6, "closed")
        self.engine.counters[6]["skills"].append("LOAN")  # Desk B also covers loans
        _, t = self.book.check_in(a["ref"])
        self.assertNotEqual(t["counter_id"], 6)

    def test_serving_the_token_completes_the_appointment(self):
        a = self.today()
        _, t = self.book.check_in(a["ref"])
        self.engine.call_next(t["counter_id"])
        self.engine.complete(t["counter_id"])
        self.assertEqual(a["status"], "completed")

    def test_queue_no_show_is_recorded(self):
        a = self.today()
        _, t = self.book.check_in(a["ref"])
        self.engine.call_next(t["counter_id"])
        self.engine.complete(t["counter_id"], "no_show")
        self.assertEqual(a["status"], "no_show")

    def test_check_in_only_on_the_day(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao")
        with self.assertRaises(ValueError):
            self.book.check_in(a["ref"])

    def test_no_show_only_after_the_slot_starts(self):
        a = self.today()
        with self.assertRaises(ValueError):
            self.book.mark_no_show(a["ref"])
        self.book.clock = lambda: datetime(2026, 11, 2, 11, 20)
        self.assertTrue(self.book._view(a)["late"])
        self.book.mark_no_show(a["ref"])
        self.assertEqual(a["status"], "no_show")

    def test_status_rules(self):
        a = self.today()
        with self.assertRaises(ValueError):
            self.book.complete(a["ref"])
        self.book.check_in(a["ref"])
        for action in (self.book.cancel, self.book.confirm, self.book.check_in):
            with self.assertRaises(ValueError):
                action(a["ref"])
        with self.assertRaises(ValueError):
            self.book.confirm("AP-ZZZZ")


class Kiosk(Base):
    def setUp(self):
        super().setUp()
        self.a = A.Assistant(self.engine, self.book)

    def start(self, lang="en"):
        return self.a.start(lang)["session"]

    def texts(self, r):
        return " ".join(m["text"] for m in r["messages"])

    def book_flow(self, sid, kind="rm", segment="regular", day=TUESDAY, time="11:00"):
        out = [self.a.message(sid, action="intent:appointment"), self.a.message(sid, action=f"appt:{kind}"),
               self.a.message(sid, action=f"apptseg:{segment}"), self.a.message(sid, action=f"apptday:{day}"),
               self.a.message(sid, action=f"appttime:{time}"), self.a.message(sid, "Anita Rao"),
               self.a.message(sid, "9876543210"), self.a.message(sid, action="confirm")]
        return out

    def test_menu_offers_booking_but_forms_menu_does_not(self):
        r = self.a.start("en")
        self.assertIn("intent:appointment", [x["action"] for x in r["actions"]])
        forms = self.a.message(r["session"], action="intent:form")
        self.assertNotIn("form:appointment", [x["action"] for x in forms["actions"]])
        self.a.message(r["session"], action="form:appointment")
        self.assertIsNone(self.a.sessions[r["session"]]["flow"])

    def test_books_in_every_language(self):
        for lang in LANG_CODES:
            sid = self.start(lang)
            r = self.book_flow(sid, time=AP.TIMES[LANG_CODES.index(lang)])[-1]
            self.assertEqual(r["side"]["type"], "appointment", lang)
            appt = self.book.items[r["side"]["ref"]]
            self.assertEqual(appt["channel"], "kiosk")
            self.assertIn(T["day_1"][LANG_CODES.index(lang)], self.texts(r))

    def test_keywords_start_booking_in_each_language(self):
        for lang, text in (("en", "I want an appointment"), ("hi", "मुझे अपॉइंटमेंट चाहिए"), ("ta", "எனக்கு சந்திப்பு வேண்டும்"),
                           ("te", "నాకు అపాయింట్‌మెంట్ కావాలి"), ("kn", "ನನಗೆ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬೇಕು"), ("ml", "എനിക്ക് അപ്പോയിന്റ്മെന്റ് വേണം"),
                           ("bn", "আমার অ্যাপয়েন্টমেন্ট চাই"), ("mr", "मला अपॉइंटमेंट हवी आहे")):
            sid = self.start(lang)
            self.assertEqual(self.a.sessions[sid]["flow"], None)
            self.a.message(sid, text)
            self.assertEqual(self.a.sessions[sid]["flow"], "appt_type", lang)

    def test_spoken_answers_work_for_each_step(self):
        sid = self.start("hi")
        self.a.message(sid, "अपॉइंटमेंट")
        self.a.message(sid, "रिलेशनशिप मैनेजर")
        self.assertEqual(self.a.sessions[sid]["flow"], "appt_segment")
        self.a.message(sid, "नहीं")
        self.a.message(sid, "मंगलवार")
        self.assertEqual(self.a.sessions[sid]["flow"], "appt_time")
        self.a.message(sid, "11:00")
        self.assertEqual(self.a.sessions[sid]["flow"], "form_field")

    def test_customers_never_see_the_risk(self):
        sid = self.start()
        replies = self.book_flow(sid)
        replies.append(self.a.message(sid, replies[-1]["side"]["ref"]))
        blob = json.dumps(replies, ensure_ascii=False)
        self.assertNotIn('"risk"', blob)
        self.assertNotIn("no-show", blob.lower())
        self.assertNotIn("overbook", blob.lower())

    def test_overbooked_slot_reads_like_a_normal_slot(self):
        self.fill(**RISKY)
        sid = self.start()
        self.a.message(sid, action="intent:appointment")
        self.a.message(sid, action="appt:forex")
        self.a.message(sid, action="apptseg:regular")
        r = self.a.message(sid, action=f"apptday:{TUESDAY}")
        self.assertTrue(any(x["action"].startswith("appttime:") for x in r["actions"]))

    def test_senior_gets_another_day_when_only_overbooking_is_left(self):
        self.fill(**RISKY)
        sid = self.start()
        self.a.message(sid, action="intent:appointment")
        self.a.message(sid, action="appt:forex")
        self.a.message(sid, action="apptseg:senior")
        r = self.a.message(sid, action=f"apptday:{TUESDAY}")
        self.assertIn(T["appt_none"][0], self.texts(r))

    def test_slot_taken_during_the_flow_is_handled(self):
        sid = self.start()
        self.a.message(sid, action="intent:appointment")
        self.a.message(sid, action="appt:forex")
        self.a.message(sid, action="apptseg:regular")
        self.a.message(sid, action=f"apptday:{TUESDAY}")
        self.a.message(sid, action="appttime:11:00")
        self.a.message(sid, "Anita Rao")
        self.a.message(sid, "9876543210")
        self.book.create("forex", TUESDAY, "11:00", "John Mathew", **SAFE)
        r = self.a.message(sid, action="confirm")
        self.assertIn(T["appt_none"][0], self.texts(r))
        self.assertEqual(self.a.sessions[sid]["flow"], "appt_day")

    def test_check_in_by_reference_issues_a_token(self):
        a = self.book.create("rm", "2026-11-02", "11:00", "Anita Rao", language="हिन्दी")
        sid = self.start("hi")
        r = self.a.message(sid, f"my reference is {a['ref'].lower()}")
        self.assertIn(f"checkin:{a['ref']}", [x["action"] for x in r["actions"]])
        r = self.a.message(sid, action=f"checkin:{a['ref']}")
        self.assertEqual(r["side"]["type"], "token")
        self.assertEqual(self.engine.tokens[r["side"]["token"]]["ref"], a["ref"])

    def test_future_appointment_offers_no_check_in(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao")
        r = self.a.message(self.start(), a["ref"])
        self.assertFalse(any(x["action"].startswith("checkin") for x in r["actions"]))
        self.assertIn("11:00", self.texts(r))

    def test_cancel_by_reference(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao")
        r = self.a.message(self.start(), action=f"apptcancel:{a['ref']}")
        self.assertIn(a["ref"], self.texts(r))
        self.assertEqual(a["status"], "cancelled")

    def test_reference_reveals_no_customer_details(self):
        a = self.book.create("rm", TUESDAY, "11:00", "Anita Rao", "9876543210")
        blob = json.dumps(self.a.message(self.start(), a["ref"]), ensure_ascii=False)
        for secret in ("Anita", "3210", "Menon", "Bose"):
            self.assertNotIn(secret, blob)

    def test_unknown_reference(self):
        self.assertIn(T["ref_unknown"][0], self.texts(self.a.message(self.start(), "AP-ZZZZ")))

    def test_cancel_mid_flow_books_nothing(self):
        sid = self.start()
        self.a.message(sid, action="intent:appointment")
        self.a.message(sid, action="appt:rm")
        self.a.message(sid, action="cancel")
        self.assertEqual(self.book.items, {})

    def test_works_without_a_book(self):
        a = A.Assistant(self.engine)
        r = a.start("en")
        self.assertNotIn("intent:appointment", [x["action"] for x in r["actions"]])


class Demo(Base):
    def test_seed_is_mixed_and_never_doubles_a_slot(self):
        self.book.seed_demo()
        view = self.book.day_view("2026-11-03")
        self.assertGreater(view["kpi"]["booked"], 10)
        self.assertEqual({a["status"] for a in view["appointments"]}, {"booked"})
        self.assertEqual(view["kpi"]["overbooked_slots"], 0)
        bands = {a["band"] for a in view["appointments"]}
        self.assertTrue({"low", "medium", "high"} & bands)
        self.assertTrue(math.isfinite(view["kpi"]["predicted_no_shows"]))
        today = {a["status"] for a in self.book.day_view("2026-11-02")["appointments"]}
        self.assertTrue(today & {"completed", "no_show"})


if __name__ == "__main__":
    unittest.main()
