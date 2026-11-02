"""Non-functional: accessibility, localisation, reliability and compatibility."""
import ast
import os
import re
import sys
import time
import unittest
from html.parser import HTMLParser

from helpers import ApiCase, BACKEND, FIXED_NOW, read  # also puts backend/ on the path
import appointments as AP
import assistant as A
from assistant_i18n import KEYWORDS, LANG_CODES, LANGUAGES, SERVICE_KEYWORDS, T
from server import MODEL, QueueEngine

SCRIPT_RANGES = {"hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F), "bn": (0x0980, 0x09FF), "ta": (0x0B80, 0x0BFF),
                 "te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF), "ml": (0x0D00, 0x0D7F)}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def page_tags():
    p = Page()
    p.feed(read("frontend", "index.html"))
    return p.tags


def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Accessibility(unittest.TestCase):
    def setUp(self):
        self.tags = page_tags()
        self.css = read("frontend", "styles.css")
        self.colours = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", self.css))

    def test_page_declares_language_and_viewport(self):
        self.assertTrue(any(t == "html" and a.get("lang") for t, a in self.tags))
        self.assertTrue(any(t == "meta" and a.get("name") == "viewport" for t, a in self.tags))

    def test_every_form_field_has_a_label(self):
        labelled = {a["for"] for t, a in self.tags if t == "label" and "for" in a}
        for tag, attrs in self.tags:
            if tag in ("input", "select", "textarea") and attrs.get("type") != "checkbox":
                self.assertTrue(attrs.get("id") in labelled or attrs.get("aria-label"), attrs)

    def test_new_chat_messages_are_announced(self):
        log = next(a for t, a in self.tags if a.get("id") == "asLog")
        self.assertEqual(log.get("aria-live"), "polite")

    def test_text_colours_meet_wcag_aa(self):
        for fg, bg in (("text", "surface"), ("text", "bg"), ("muted", "surface"), ("muted", "bg"), ("muted", "surface-2"),
                       ("accent", "surface"), ("accent", "accent-soft"), ("teal", "surface"), ("teal", "teal-soft"),
                       ("amber", "surface"), ("amber", "amber-soft"), ("red", "surface"), ("red", "red-soft")):
            ratio = contrast(self.colours[fg], self.colours[bg])
            self.assertGreaterEqual(ratio, 4.5, f"{fg} on {bg}: {ratio:.2f}")

    def test_kiosk_mode_enlarges_touch_targets(self):
        for rule in ("body.kiosk { font-size: 17px", "body.kiosk .quick", "body.kiosk .key", "body.kiosk .lang-btn"):
            self.assertIn(rule, self.css)

    def test_keyboard_focus_stays_visible(self):
        self.assertNotRegex(self.css, r"outline:\s*(none|0)\b")
        self.assertIn(":focus-visible", self.css)

    def test_page_works_on_phone_widths(self):
        self.assertIn("@media (max-width: 700px)", self.css)

    def test_page_elements_used_by_scripts_exist(self):
        ids = {a["id"] for _, a in self.tags if "id" in a}
        for name in ("app.js", "assistant.js", "appointments.js"):
            for wanted in set(re.findall(r'\$\("#(\w+)["\s:]', read("frontend", name))):
                self.assertIn(wanted, ids, f"{name} uses a missing element")


class Localisation(unittest.TestCase):
    def test_every_language_has_a_voice_tag_and_native_name(self):
        for l in LANGUAGES:
            self.assertRegex(l["bcp47"], r"^[a-z]{2}-IN$")
            self.assertTrue(l["name"])

    def test_each_translation_uses_its_own_script(self):
        for key, values in T.items():
            for code, text in zip(LANG_CODES, values):
                if code == "en" or text.isascii():
                    continue
                lo, hi = SCRIPT_RANGES[code]
                letters = [ord(c) for c in text if c.isalpha()]
                self.assertTrue(any(lo <= c <= hi for c in letters), f"{key}/{code}: {text}")

    def test_no_strings_are_left_in_english(self):
        allowed = {"st_open"}
        for key, values in T.items():
            for code, text in zip(LANG_CODES[1:], values[1:]):
                if key not in allowed:
                    self.assertNotEqual(text, values[0], f"{key}/{code}")

    def test_regional_digits_are_understood(self):
        for raw in ("५०००", "৫০০০", "౫౦౦౦", "೫೦೦೦", "൫൦൦൦", "௫௦௦௦"):
            self.assertEqual(A.normalise_digits(raw), "5000", raw)

    def test_every_main_intent_has_phrases_in_every_script(self):
        for intent in ("balance", "token", "appointment", "complaint"):
            phrases = KEYWORDS[intent]
            for code, (lo, hi) in SCRIPT_RANGES.items():
                self.assertTrue(any(lo <= ord(c) <= hi for p in phrases for c in p), f"{intent}/{code}")

    def test_keyword_tables_are_lowercase(self):
        for table in (KEYWORDS, SERVICE_KEYWORDS):
            for phrases in table.values():
                for p in phrases:
                    self.assertEqual(p, p.casefold())


class Reliability(unittest.TestCase):
    def test_ai_outage_falls_back_to_keywords(self):
        class Down:
            class beta:
                class messages:
                    @staticmethod
                    def create(**_):
                        raise TimeoutError("network down")

        class Fake:
            AnthropicError = TimeoutError
        a = A.Assistant(QueueEngine(MODEL))
        a.claude.client, a.claude.anthropic = Down, Fake
        sid = a.start("en")["session"]
        self.assertIn("I didn't understand", a.message(sid, "zzzz qqqq")["messages"][0]["text"])
        self.assertEqual(a.message(sid, "balance")["side"]["type"], "keypad")

    def test_ai_can_only_choose_from_the_allowed_list(self):
        class Rogue:
            class beta:
                class messages:
                    @staticmethod
                    def create(**_):
                        block = type("B", (), {"type": "text", "text": '{"intent":"transfer_all_money","service":"none"}'})
                        return type("R", (), {"stop_reason": "end_turn", "content": [block]})
        a = A.Assistant(QueueEngine(MODEL))
        a.claude.client, a.claude.anthropic = Rogue, type("M", (), {"AnthropicError": Exception})
        sid = a.start("en")["session"]
        self.assertIn("I didn't understand", a.message(sid, "zzzz qqqq")["messages"][0]["text"])

    def test_idle_sessions_are_removed(self):
        a = A.Assistant(QueueEngine(MODEL))
        sid = a.start("en")["session"]
        a.sessions[sid]["seen"] = time.time() - A.SESSION_TTL - 1
        a.start("en")
        self.assertNotIn(sid, a.sessions)
        self.assertEqual(len(a.sessions), 1)

    def test_app_survives_every_counter_closing(self):
        q = QueueEngine(MODEL)
        a = A.Assistant(q)
        for c in q.counters:
            c["status"] = "closed"
        sid = a.start("en")["session"]
        a.message(sid, action="svc:passbook")
        self.assertIn("not available", a.message(sid, action="seg:regular")["messages"][0]["text"])
        self.assertTrue(q.snapshot()["alerts"])

    def test_check_in_survives_the_advisors_desk_closing(self):
        q = QueueEngine(MODEL)
        book = AP.AppointmentBook(q, NOSHOW)
        book.clock = lambda: FIXED_NOW
        a = book.create("rm", "2026-11-02", "11:00", "Anita Rao")
        counter = next(x["counter_id"] for x in AP.ADVISORS if x["id"] == a["advisor_id"])
        q.set_counter_status(counter, "closed")
        _, token = book.check_in(a["ref"])
        self.assertNotEqual(token["counter_id"], counter)

    def test_works_on_any_day_of_the_week(self):
        for day in range(2, 9):
            book = AP.AppointmentBook(QueueEngine(MODEL), NOSHOW)
            book.clock = lambda: FIXED_NOW.replace(day=day, hour=18)
            days = book.open_days()
            self.assertEqual(len(days), 6)
            self.assertTrue(book.available("rm", days[0]))

    def test_forecast_and_seed_work_at_any_hour(self):
        for hour in (0, 8, 12, 17, 23):
            book = AP.AppointmentBook(QueueEngine(MODEL), NOSHOW)
            book.clock = lambda: FIXED_NOW.replace(hour=hour)
            book.seed_demo()
            self.assertTrue(book.items)


NOSHOW = AP.NoShowModel()


class Recovery(ApiCase):
    def test_server_keeps_working_after_bad_requests(self):
        for _ in range(20):
            self.api.request("POST", "/api/tokens", raw=b"{bad")
        self.ok(self.api.post("/api/tokens", {"service": "passbook"}))

    def test_unknown_pages_return_not_found(self):
        self.assertEqual(self.api.get("/nope.html").status, 404)
        self.assertEqual(self.api.get("/api/nope").status, 404)
        self.assertEqual(self.api.post("/api/nope").status, 404)


class Compatibility(unittest.TestCase):
    def test_backend_uses_only_the_standard_library(self):
        own = {"appointments", "assistant", "assistant_i18n", "server"}
        for name in os.listdir(BACKEND):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(BACKEND, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [n.name for n in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module]
                else:
                    continue
                for module in names:
                    top = module.split(".")[0]
                    optional = top == "anthropic" and node.col_offset > 0  # imported only inside a guarded block
                    self.assertTrue(top in sys.stdlib_module_names or top in own or optional, f"{name}: {module}")

    def test_front_end_loads_nothing_from_outside(self):
        for name in ("index.html", "styles.css", "app.js", "assistant.js", "appointments.js"):
            self.assertNotRegex(read("frontend", name), r"(src|href)=[\"']https?://|@import|cdn\.")

    def test_no_machine_specific_paths_in_the_code(self):
        for path in (("backend", "server.py"), ("backend", "assistant.py"), ("backend", "appointments.py"), ("frontend", "app.js")):
            self.assertNotRegex(read(*path), r"C:\\\\Users|/home/\w+|/Users/\w+")

    def test_voice_features_are_optional_in_the_browser(self):
        js = read("frontend", "assistant.js")
        self.assertIn('"speechSynthesis" in window', js)
        self.assertIn("window.SpeechRecognition || window.webkitSpeechRecognition", js)
