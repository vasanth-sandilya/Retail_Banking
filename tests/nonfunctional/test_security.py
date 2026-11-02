"""Non-functional: security and privacy."""
import json
import os
import re
import subprocess
import unittest

from helpers import ApiCase, DEMO_MOBILE, ROOT, ServerProcess, read  # also puts backend/ on the path
import assistant as A
from server import MODEL, QueueEngine


class WebSecurity(ApiCase):
    def test_source_files_cannot_be_downloaded(self):
        for path in ("/../backend/server.py", "/..%2fbackend%2fserver.py", "/%2e%2e/backend/server.py",
                     "/../README.md", "/../.git/config", "/....//backend/server.py"):
            r = self.api.get(path)
            self.assertNotIn("FootfallModel", r.text, path)
            self.assertNotIn("[core]", r.text, path)
            self.assertNotIn("# Mitra", r.text, path)

    def test_security_headers_are_sent(self):
        for path in ("/", "/app.js", "/api/meta"):
            h = self.api.get(path).headers
            self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(h.get("X-Frame-Options"), "DENY", path)
            self.assertIn("default-src 'self'", h.get("Content-Security-Policy", ""), path)

    def test_csp_allows_no_outside_or_inline_scripts(self):
        csp = self.api.get("/").headers["Content-Security-Policy"]
        self.assertNotRegex(csp, r"script-src[^;]*unsafe-inline")
        self.assertNotIn("<script>", read("frontend", "index.html"))
        self.assertNotRegex(read("frontend", "index.html"), r'src="https?://')

    def test_api_replies_are_never_cached(self):
        self.assertEqual(self.api.get("/api/state").headers["Cache-Control"], "no-store")

    def test_customer_text_comes_back_as_json_not_html(self):
        r = self.api.post("/api/tokens", {"service": "passbook", "name": "<script>alert(1)</script>"})
        self.assertEqual(r.headers["Content-Type"], "application/json")

    def test_front_end_escapes_customer_supplied_text(self):
        for name in ("app.js", "assistant.js", "appointments.js"):
            raw = re.findall(r"\$\{\w+\.(?:name|language|text)\}", read("frontend", name))
            self.assertEqual(raw, [], f"{name} puts unescaped text into HTML")

    def test_chat_uses_text_content_not_html(self):
        add = read("frontend", "assistant.js")
        bubble = add[add.index("function addBubble"):add.index("function applyLabels")]
        self.assertIn("textContent", bubble)
        self.assertNotIn("innerHTML", bubble)


class BadInput(ApiCase):
    ENDPOINTS = ["/api/tokens", "/api/transfer", "/api/counters/1/next", "/api/counters/1/complete", "/api/counters/1/status",
                 "/api/counters/abc/next", "/api/counters/1", "/api/assist/start", "/api/assist/message", "/api/assist/secure",
                 "/api/assist/reveal", "/api/assist/end", "/api/requests/SR-1", "/api/appointments", "/api/appointments/policy",
                 "/api/appointments/AP-ZZZZ/confirm", "/api/appointments/AP-ZZZZ/nope", "/api/appointments/x", "/api/nope"]
    PAYLOADS = [b"", b"{", b"null", b"[]", b"1", b'"text"', b"\xff\xfe", b'{"service":[]}', b'{"service":{}}', b'{"name":123}',
                b'{"token":5}', b'{"session":[],"text":{},"action":7}', b'{"session":{"a":1}}', b'{"lang":[1]}', b'{"value":null}',
                b'{"status":[]}', b'{"level":[]}', b'{"type":[],"date":[],"time":[],"name":[]}', b'{"type":"rm","date":"nope","time":"x","name":"ab"}',
                b'{"service":"passbook","segment":["x"]}', b'{"service":"passbook","mobile":123}', b'{"text":"' + b"x" * 5000 + b'"}']

    def test_no_input_causes_a_server_error(self):
        for path in self.ENDPOINTS:
            for raw in self.PAYLOADS:
                r = self.api.request("POST", path, raw=raw)
                self.assertLess(r.status, 500, f"{path} {raw[:40]!r}: {r.text}")

    def test_bad_queries_do_not_cause_a_server_error(self):
        for path in ("/api/forecast?start=nope", "/api/appointments?date=nope", "/api/appointments/slots", "/api/appointments/slots?type=x&date=y",
                     "/api/token/%00", "/api/token/../..", "/api/nope"):
            self.assertLess(self.api.get(path).status, 500, path)

    def test_error_replies_reveal_no_internals(self):
        for raw in (b"[]", b'{"service":[]}', b'{"session":[]}'):
            r = self.api.request("POST", "/api/assist/message", raw=raw)
            self.assertNotIn("Traceback", r.text)
            self.assertNotRegex(r.text, r"\.py|unhashable|NoneType|object has no")

    def test_oversized_requests_are_refused_without_reading_them(self):
        r = self.api.request("POST", "/api/tokens", raw=b"{}", headers={"Content-Length": str(50 * 1024 * 1024)})
        self.assertEqual(r.status, 413)

    def test_invalid_content_length_is_refused(self):
        for value in ("-5", "abc"):
            r = self.api.request("POST", "/api/tokens", raw=b"", headers={"Content-Length": value})
            self.assertEqual(r.status, 400, value)


class Privacy(ApiCase):
    def test_server_logs_stay_free_of_customer_data(self):
        running = ServerProcess()
        try:
            api = running.api
            sid = api.post("/api/assist/start", {"lang": "en"}).json["session"]
            api.post("/api/assist/message", {"session": sid, "action": "intent:balance"})
            code = api.post("/api/assist/secure", {"session": sid, "value": DEMO_MOBILE}).json["side"]["demo_code"]
            api.post("/api/assist/secure", {"session": sid, "value": code})
            api.post("/api/assist/reveal", {"session": sid})
            api.post("/api/tokens", {"service": "passbook", "name": "Anita Rao", "mobile": "9876543210"})
            log = running.output()
        finally:
            running.stop()
        for secret in (DEMO_MOBILE, code, "84250", "Anita", "9876543210", sid):
            self.assertNotIn(secret, log)

    def test_activity_log_holds_no_names_or_numbers(self):
        self.ok(self.api.post("/api/tokens", {"service": "passbook", "name": "Anita Rao", "mobile": "9876543210"}))
        events = json.dumps(self.ok(self.api.get("/api/state"))["events"])
        self.assertNotIn("Anita", events)
        self.assertNotIn("3210", events)

    def test_otp_is_hidden_outside_demo_mode(self):
        running = ServerProcess(env={"ASSIST_DEMO": "0"})
        try:
            sid = running.api.post("/api/assist/start", {"lang": "en"}).json["session"]
            running.api.post("/api/assist/message", {"session": sid, "action": "intent:balance"})
            reply = running.api.post("/api/assist/secure", {"session": sid, "value": DEMO_MOBILE}).json
            self.assertIsNone(reply["side"]["demo_code"])
            self.assertFalse(re.search(r"\b\d{6}\b", json.dumps(reply)))
        finally:
            running.stop()

    def test_session_ids_are_long_and_unique(self):
        ids = {self.start_kiosk()["session"] for _ in range(100)}
        self.assertEqual(len(ids), 100)
        self.assertTrue(all(len(i) >= 24 for i in ids))

    def test_appointment_lookup_shows_no_identity(self):
        a = self.book("rm", name="Anita Rao", mobile="9876543210")
        sid = self.start_kiosk()["session"]
        blob = json.dumps(self.say(sid, a["ref"]))
        for secret in ("Anita", "3210", a["advisor"]):
            self.assertNotIn(secret, blob)

    def test_customer_replies_never_carry_risk_scores(self):
        a = self.book("rm")
        sid = self.start_kiosk()["session"]
        self.assertNotIn('"risk"', json.dumps(self.say(sid, a["ref"])))

    def test_digits_never_reach_the_optional_ai_step(self):
        sent = {}

        class Fake:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        sent.update(kw)
                        raise ValueError("stop")
        claude = A.ClaudeIntent()
        claude.client, claude.anthropic = Fake, type("M", (), {"AnthropicError": Exception})
        claude.classify("my mobile is 9876543210 and account 4821")
        self.assertNotRegex(sent["messages"][0]["content"], r"\d")


class Authentication(unittest.TestCase):
    def setUp(self):
        self.a = A.Assistant(QueueEngine(MODEL))

    def test_otp_cannot_be_guessed_within_the_attempt_limit(self):
        sid = self.a.start("en")["session"]
        self.a.message(sid, action="intent:balance")
        self.a.secure(sid, DEMO_MOBILE)
        real = self.a.sessions[sid]["otp"]["code"]
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(A.OTP_ATTEMPTS):
            self.a.secure(sid, wrong)
        self.assertTrue(self.a.sessions[sid]["locked"])
        self.assertIsNone(self.a.sessions[sid]["otp"])

    def test_otp_comparison_is_constant_time(self):
        self.assertIn("hmac.compare_digest", read("backend", "assistant.py"))

    def test_otp_comes_from_a_secure_random_source(self):
        self.assertRegex(read("backend", "assistant.py"), r"secrets\.randbelow")
        self.assertRegex(read("backend", "appointments.py"), r"secrets\.choice")

    def test_lockout_survives_a_new_balance_request(self):
        sid = self.a.start("en")["session"]
        self.a.message(sid, action="intent:balance")
        self.a.secure(sid, DEMO_MOBILE)
        real = self.a.sessions[sid]["otp"]["code"]
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(3):
            self.a.secure(sid, wrong)
        reply = self.a.message(sid, action="intent:balance")
        self.assertNotEqual((reply["side"] or {}).get("type"), "keypad")


class KnownGaps(ApiCase):
    """
    Agreed gaps before real customers. Each test asserts the safe behaviour and is
    marked as an expected failure, so it will flag itself once the gap is closed.
    """

    @unittest.expectedFailure
    def test_staff_data_requires_a_login(self):
        self.assertEqual(self.api.get("/api/state").status, 401)

    @unittest.expectedFailure
    def test_kiosk_devices_cannot_reset_the_branch(self):
        self.assertEqual(self.api.post("/api/reset").status, 401)

    @unittest.expectedFailure
    def test_kiosk_requests_are_rate_limited_per_device(self):
        statuses = [self.api.post("/api/assist/start", {"lang": "en"}).status for _ in range(500)]
        self.assertIn(429, statuses)


class Repository(unittest.TestCase):
    def tracked(self):
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
            return out.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git is not available")

    def test_no_secrets_are_committed(self):
        patterns = re.compile(r"sk-ant-[\w-]{10,}|gh[po]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}")
        for path in self.tracked():
            with open(os.path.join(ROOT, path), encoding="utf-8", errors="ignore") as f:
                self.assertIsNone(patterns.search(f.read()), path)

    def test_no_environment_or_database_files_are_committed(self):
        for path in self.tracked():
            self.assertNotRegex(path, r"(^|/)\.env|\.pem$|\.key$|\.sqlite3?$|\.db$")
