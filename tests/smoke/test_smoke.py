"""Smoke: does a freshly started server work at all? Runs first and fails fast."""
import unittest

from helpers import ServerProcess, TOMORROW  # also puts backend/ on the path
from assistant_i18n import LANG_CODES


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.running = ServerProcess()
        cls.api = cls.running.api

    @classmethod
    def tearDownClass(cls):
        cls.running.stop()

    def test_server_announces_itself(self):
        self.assertIn("Mitra running", self.running.output())

    def test_home_page_loads(self):
        r = self.api.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("<title>Mitra</title>", r.text)

    def test_locked_kiosk_page_loads(self):
        self.assertEqual(self.api.get("/?mode=kiosk").status, 200)

    def test_front_end_files_load(self):
        for path in ("/styles.css", "/app.js", "/assistant.js", "/appointments.js"):
            r = self.api.get(path)
            self.assertEqual(r.status, 200, path)
            self.assertGreater(len(r.text), 500, path)

    def test_read_endpoints_respond(self):
        for path in ("/api/meta", "/api/state", "/api/display", "/api/forecast", "/api/requests",
                     "/api/assist/meta", f"/api/appointments?date={TOMORROW}", "/api/appointments"):
            r = self.api.get(path)
            self.assertEqual(r.status, 200, path)
            self.assertIsInstance(r.json, dict, path)

    def test_token_can_be_issued(self):
        r = self.api.post("/api/tokens", {"service": "cash_deposit"})
        self.assertEqual(r.status, 200, r.text)
        self.assertTrue(r.json["token"].startswith("C"))

    def test_kiosk_starts_in_every_language(self):
        for lang in LANG_CODES:
            r = self.api.post("/api/assist/start", {"lang": lang})
            self.assertEqual(r.status, 200, lang)
            self.assertEqual(r.json["lang"], lang)
            self.assertTrue(r.json["messages"][0]["text"])

    def test_demo_appointments_are_loaded(self):
        days = self.api.get(f"/api/appointments?date={TOMORROW}").json
        self.assertEqual(len(days["advisors"]), 5)
        self.assertTrue(days["times"])
