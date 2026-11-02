"""Integration: complete customer and staff journeys over real HTTP, across all modules."""
from concurrent.futures import ThreadPoolExecutor

from helpers import ApiCase, DEMO_MOBILE, TODAY, TOMORROW  # also puts backend/ on the path
from assistant_i18n import LANG_CODES, T


class QueueJourney(ApiCase):
    def test_token_from_issue_to_served(self):
        t = self.ok(self.api.post("/api/tokens", {"service": "loan_enquiry", "segment": "senior", "name": "Anita", "mobile": "9876543210"}))
        self.assertEqual(self.ok(self.api.get(f"/api/token/{t['token']}"))["status"], "waiting")
        self.assertEqual(self.ok(self.api.get("/api/state"))["kpi"]["waiting"], 1)
        self.assertEqual(self.ok(self.api.post("/api/counters/6/next"))["token"]["token"], t["token"])
        self.assertEqual(self.ok(self.api.get("/api/display"))["serving"][0]["token"], t["token"])
        self.ok(self.api.post("/api/counters/6/complete", {"outcome": "served"}))
        state = self.ok(self.api.get("/api/state"))["kpi"]
        self.assertEqual((state["waiting"], state["served"]), (0, 1))

    def test_transfer_over_http(self):
        t = self.ok(self.api.post("/api/tokens", {"service": "cash_deposit"}))
        moved = self.ok(self.api.post("/api/transfer", {"token": t["token"].lower(), "service": "forex"}))
        self.assertEqual(moved["category"], "FOREX")

    def test_counter_status_change_is_visible(self):
        self.ok(self.api.post("/api/counters/1/status", {"status": "break"}))
        counters = self.ok(self.api.get("/api/state"))["counters"]
        self.assertEqual(next(c for c in counters if c["id"] == 1)["status"], "break")

    def test_forecast_for_a_chosen_week(self):
        week = self.ok(self.api.get("/api/forecast?start=2026-11-02"))["week"]
        self.assertEqual(week[0]["date"], "2026-11-02")
        self.assertIn("staffing", week[0]["hours"][0])

    def test_demo_then_reset(self):
        self.ok(self.api.post("/api/demo"))
        self.assertGreater(self.ok(self.api.get("/api/state"))["kpi"]["issued_total"], 0)
        self.ok(self.api.post("/api/reset"))
        self.assertEqual(self.ok(self.api.get("/api/state"))["kpi"]["issued_total"], 0)


class KioskJourney(ApiCase):
    def test_balance_in_tamil(self):
        sid = self.start_kiosk("ta")["session"]
        reply = self.say(sid, "என் இருப்பு எவ்வளவு")
        self.assertEqual((reply["input"], reply["side"]["mode"]), ("keypad", "mobile"))
        reply = self.verify(sid)
        self.assertEqual(reply["side"]["type"], "balance")
        self.assertNotIn("84250", str(reply))
        shown = self.ok(self.api.post("/api/assist/reveal", {"session": sid}))
        self.assertEqual(shown["accounts"][0]["balance"], 84250.75)
        self.assertIn("••4821", shown["accounts"][0]["label"])

    def test_reveal_needs_verification(self):
        sid = self.start_kiosk()["session"]
        self.assertEqual(self.ok(self.api.post("/api/assist/reveal", {"session": sid})), {"error": "not_verified"})

    def test_ended_session_cannot_reveal(self):
        sid = self.start_kiosk()["session"]
        self.press(sid, "intent:balance")
        self.verify(sid)
        self.ok(self.api.post("/api/assist/end", {"session": sid}))
        self.assertTrue(self.ok(self.api.post("/api/assist/reveal", {"session": sid}))["expired"])

    def test_form_reaches_the_queue_and_staff(self):
        sid = self.start_kiosk("hi")["session"]
        self.press(sid, "form:deposit_slip")
        self.say(sid, "४८२१")
        self.say(sid, "₹5,000")
        self.say(sid, "अनिता राव")
        self.press(sid, "confirm")
        ticket = self.press(sid, "seg:senior")["side"]
        queued = self.ok(self.api.get("/api/state"))["waiting"][0]
        self.assertEqual((queued["token"], queued["ref"], queued["language"]), (ticket["token"], ticket["ref"], "हिन्दी"))
        record = self.ok(self.api.get("/api/requests"))["records"][0]
        self.assertEqual((record["ref"], record["token"]), (ticket["ref"], ticket["token"]))
        self.assertEqual(record["details"][1]["value"], "5000.00")

    def test_lost_card_jumps_the_queue(self):
        sid = self.start_kiosk()["session"]
        self.ok(self.api.post("/api/tokens", {"service": "card_services", "segment": "regular"}))
        self.say(sid, "I lost my card")
        self.verify(sid)
        token = self.press(sid, "confirm")["side"]["token"]
        counter = next(t["counter_id"] for t in self.ok(self.api.get("/api/state"))["waiting"] if t["token"] == token)
        self.assertEqual(self.ok(self.api.post(f"/api/counters/{counter}/next"))["token"]["token"], token)

    def test_request_tracked_after_staff_update(self):
        sid = self.start_kiosk("kn")["session"]
        self.press(sid, "req:complaint")
        ref = self.say(sid, "ಎಟಿಎಂ ಹಣ ನೀಡಲಿಲ್ಲ")["side"]["ref"]
        self.ok(self.api.post(f"/api/requests/{ref}", {"status": "done"}))
        self.assertIn(T["st_done"][LANG_CODES.index("kn")], self.say(sid, ref)["messages"][0]["text"])

    def test_token_status_by_voice_or_chat(self):
        sid = self.start_kiosk()["session"]
        self.press(sid, "svc:passbook")
        token = self.press(sid, "seg:regular")["side"]["token"]
        self.assertIn(token, self.say(sid, f"where is {token}")["messages"][0]["text"])

    def test_expired_session_is_reported(self):
        self.assertTrue(self.say("not-a-session", "hello")["expired"])


class AppointmentJourney(ApiCase):
    def test_staff_booking_to_completed(self):
        a = self.book("rm", TODAY, "11:00", mobile="9876543210")
        self.assertEqual(a["mobile"], "3210")
        self.ok(self.api.post(f"/api/appointments/{a['ref']}/confirm"))
        self.ok(self.api.post(f"/api/appointments/{a['ref']}/checkin"))
        counter = 9 if a["advisor_id"] == "rm1" else 10
        queued = self.ok(self.api.get("/api/state"))["waiting"][0]
        self.assertEqual((queued["segment"], queued["ref"], queued["counter_id"]), ("appointment", a["ref"], counter))
        self.ok(self.api.post(f"/api/counters/{counter}/next"))
        self.ok(self.api.post(f"/api/counters/{counter}/complete", {"outcome": "served"}))
        day = self.ok(self.api.get(f"/api/appointments?date={TODAY}"))
        self.assertEqual(day["appointments"][0]["status"], "completed")

    def test_kiosk_booking_then_check_in_on_the_day(self):
        sid = self.start_kiosk("te")["session"]
        for action in ("intent:appointment", "appt:loan", "apptseg:regular", f"apptday:{TODAY}", "appttime:11:00"):
            self.press(sid, action)
        self.say(sid, "Anita Rao")
        self.say(sid, "9876543210")
        ref = self.press(sid, "confirm")["side"]["ref"]
        staff = self.ok(self.api.get(f"/api/appointments?date={TODAY}"))["appointments"][0]
        self.assertEqual((staff["ref"], staff["channel"], staff["language"]), (ref, "kiosk", "తెలుగు"))
        self.assertIn("risk", staff)
        lookup = self.say(sid, ref)
        self.assertIn(f"checkin:{ref}", [x["action"] for x in lookup["actions"]])
        ticket = self.press(sid, f"checkin:{ref}")["side"]
        self.assertEqual(self.ok(self.api.get(f"/api/token/{ticket['token']}"))["counter_id"], 6)

    def test_overbooking_over_http(self):
        for t in self.ok(self.api.get(f"/api/appointments?date={TOMORROW}"))["times"]:
            self.server.BOOK.create("forex", TOMORROW, t, "Anita Rao", first_visit=False, prior_visits=2, prior_no_shows=2)
        slots = self.ok(self.api.get(f"/api/appointments/slots?type=forex&date={TOMORROW}"))["slots"]
        self.assertTrue(slots and all(s["overbook"] for s in slots))
        self.assertEqual(self.ok(self.api.get(f"/api/appointments/slots?type=forex&date={TOMORROW}&segment=senior"))["slots"], [])
        self.book("forex", TOMORROW, slots[0]["time"], "John Mathew")
        self.assertEqual(self.ok(self.api.get(f"/api/appointments?date={TOMORROW}"))["kpi"]["overbooked_slots"], 1)

    def test_policy_change_applies_immediately(self):
        for t in self.ok(self.api.get(f"/api/appointments?date={TOMORROW}"))["times"]:
            self.server.BOOK.create("forex", TOMORROW, t, "Anita Rao", first_visit=False, prior_visits=2, prior_no_shows=2)
        self.ok(self.api.post("/api/appointments/policy", {"enabled": False}))
        self.assertEqual(self.ok(self.api.get(f"/api/appointments/slots?type=forex&date={TOMORROW}"))["slots"], [])

    def test_cancelled_appointment_can_no_longer_check_in(self):
        a = self.book("rm", TODAY, "11:00")
        self.ok(self.api.post(f"/api/appointments/{a['ref']}/cancel"))
        self.assertEqual(self.api.post(f"/api/appointments/{a['ref']}/checkin").status, 400)
        self.assertEqual(self.ok(self.api.get(f"/api/appointments?date={TODAY}"))["appointments"], [])

    def test_reset_restores_demo_data(self):
        self.ok(self.api.post("/api/reset"))
        self.assertGreater(len(self.server.BOOK.items), 0)


class Concurrency(ApiCase):
    def test_parallel_tokens_are_unique(self):
        with ThreadPoolExecutor(16) as pool:
            replies = list(pool.map(lambda _: self.ok(self.api.post("/api/tokens", {"service": "cash_deposit"})), range(120)))
        self.assertEqual(len({r["token"] for r in replies}), 120)

    def test_two_kiosks_cannot_take_the_same_slot(self):
        def attempt(i):
            return self.api.post("/api/appointments", {"type": "forex", "date": TOMORROW, "time": "11:00", "name": f"Customer {i}"}).status
        with ThreadPoolExecutor(8) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(400), 7)

    def test_parallel_kiosk_sessions_stay_separate(self):
        def session(lang):
            sid = self.start_kiosk(lang)["session"]
            self.press(sid, "svc:passbook")
            return lang, self.press(sid, "seg:regular")
        with ThreadPoolExecutor(8) as pool:
            for lang, reply in pool.map(session, LANG_CODES * 3):
                self.assertEqual(reply["lang"], lang)
                self.assertEqual(reply["side"]["type"], "token")

    def test_staff_can_read_while_customers_book(self):
        def reader(_):
            return self.api.get("/api/state").status, self.api.get(f"/api/appointments?date={TOMORROW}").status

        def writer(i):
            return self.api.post("/api/tokens", {"service": "passbook"}).status, 200
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(lambda i: (reader if i % 2 else writer)(i), range(60)))
        self.assertEqual({s for pair in results for s in pair}, {200})

    def test_demo_mobile_is_never_echoed_by_the_keypad(self):
        sid = self.start_kiosk()["session"]
        self.press(sid, "intent:balance")
        self.assertNotIn(DEMO_MOBILE, str(self.keypad(sid, DEMO_MOBILE)))
