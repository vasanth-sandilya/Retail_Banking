"""Regression: each test pins a bug found earlier in this project, so it cannot return."""
import re
import unittest

from helpers import ApiCase, FIXED_NOW, ServerProcess, read  # also puts backend/ on the path
import appointments as AP
import assistant as A
from assistant_i18n import LANG_CODES, T
from server import MODEL, QueueEngine

TUESDAY = "2026-11-03"


class ServerStartup(unittest.TestCase):
    def test_starts_on_python_that_omits_the_script_folder(self):
        """Portable Python has no script folder on its path, so assistant.py could not be imported."""
        running = ServerProcess(python_flags=["-I"])
        try:
            self.assertEqual(running.api.get("/api/meta").status, 200)
        finally:
            running.stop()


class Conversation(unittest.TestCase):
    def setUp(self):
        self.a = A.Assistant(QueueEngine(MODEL))

    def deposit_form(self, sid):
        self.a.message(sid, action="form:deposit_slip")
        self.a.message(sid, "4821")
        self.a.message(sid, "5,000.50")
        return self.a.message(sid, "Anita Rao")

    def test_amounts_keep_their_paise(self):
        """Stripping non-digits turned 5,000.50 into 500050."""
        sid = self.a.start("en")["session"]
        self.assertEqual(self.deposit_form(sid)["side"]["rows"][1]["value"], "5000.50")

    def test_changing_language_on_review_keeps_the_form(self):
        """Switching language on the review screen used to throw the form away."""
        sid = self.a.start("en")["session"]
        self.deposit_form(sid)
        reply = self.a.message(sid, action="lang:ta")
        self.assertEqual(reply["side"]["type"], "review")
        self.assertEqual(self.a.sessions[sid]["data"]["values"]["depositor"], "Anita Rao")

    def test_internal_appointment_form_stays_out_of_the_forms_menu(self):
        sid = self.a.start("en")["session"]
        actions = [x["action"] for x in self.a.message(sid, action="intent:form")["actions"]]
        self.assertNotIn("form:appointment", actions)

    def test_not_ok_never_confirms(self):
        self.assertEqual(A.yes_or_no("not ok, no"), "no")

    def test_short_words_do_not_match_inside_longer_ones(self):
        self.assertIsNone(A.yes_or_no("I know nothing"))

    def test_lost_card_reply_never_says_the_card_is_blocked(self):
        for i in range(len(LANG_CODES)):
            self.assertIn("{ref}", T["req_block_done"][i])
        self.assertIn("not blocked yet", T["req_block_done"][0])


class Appointments(unittest.TestCase):
    def setUp(self):
        self.engine = QueueEngine(MODEL)
        self.model = AP.NoShowModel()
        self.book = AP.AppointmentBook(self.engine, self.model)
        self.book.clock = lambda: FIXED_NOW

    def test_model_learns_from_past_no_shows(self):
        """Unscaled features left this at 0.17 instead of about 2.4."""
        per_unit = dict(zip(self.model.FEATURES, (w / s for w, s in zip(self.model.weights, self.model.scales))))
        self.assertGreater(per_unit["prior_rate"], 1.5)

    def test_calendar_only_hints_at_overbooking_that_can_happen(self):
        """The calendar offered overbooking on days that still had free slots."""
        self.book.create("forex", TUESDAY, "11:00", "Anita Rao", first_visit=False, prior_visits=2, prior_no_shows=2)
        self.assertEqual(self.book.day_view(TUESDAY)["candidates"], [])


class ServerHardening(ApiCase):
    def test_malformed_requests_return_400_not_500(self):
        """A JSON null or list body crashed the handler and exposed Python error text."""
        for raw in (b"null", b"[]", b'{"service":[]}'):
            r = self.api.request("POST", "/api/tokens", raw=raw)
            self.assertEqual(r.status, 400, raw)

    def test_connection_queue_handles_kiosk_bursts(self):
        """A backlog of 5 added 500 ms to about one request in twenty under load."""
        self.assertGreaterEqual(self.server.MitraServer.request_queue_size, 128)


class FrontEnd(unittest.TestCase):
    """Browser behaviour that can be pinned by reading the source."""

    def setUp(self):
        self.app = read("frontend", "app.js")
        self.assistant = read("frontend", "assistant.js")
        self.css = read("frontend", "styles.css")

    def test_late_loading_voices_are_retried(self):
        """Edge loads Tamil voices late, so replies stayed silent."""
        self.assertIn('addEventListener("voiceschanged"', self.assistant)
        self.assertIn("AS.unspoken = text", self.assistant)

    def test_regional_text_is_never_read_by_a_mismatched_voice(self):
        speak = self.assistant[self.assistant.index("function speak("):self.assistant.index("function stopSpeech")]
        self.assertIn("if (!voice)", speak)
        self.assertRegex(speak, r"if \(!voice\) \{[\s\S]*?return;\s*\}")

    def test_tablet_layout_keeps_the_keypad_on_screen(self):
        """At tablet width the keypad used to fall below the chat, off screen."""
        tablet = self.css[self.css.index("@media (max-width: 1100px)"):self.css.index("@media (max-width: 700px)")]
        self.assertRegex(tablet, r"\.grid-2\.assist\s*\{[^}]*minmax")

    def test_locked_kiosk_ignores_links_to_staff_screens(self):
        route = self.app[self.app.index("function routeFromHash"):self.app.index("function showView")]
        self.assertRegex(route, r"if \(LOCKED\) return")
        self.assertIn('if (!LOCKED) location.hash = "home"', self.app)

    def test_leaving_the_kiosk_ends_the_customer_session(self):
        self.assertIn('v !== "assistant") leaveAssistant()', self.app)
        leave = self.assistant[self.assistant.index("function leaveAssistant"):self.assistant.index("async function endSession")]
        self.assertIn('"/assist/end"', leave)

    def test_balance_is_never_passed_to_speech(self):
        reveal = self.assistant[self.assistant.index("async function revealBalance"):self.assistant.index("function hideBalance")]
        self.assertNotIn("speak(", reveal)
        self.assertIsNotNone(re.search(r"setTimeout\(hideBalance", reveal))
