"""Run with: python -m unittest discover tests"""
import json
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import assistant as A
from assistant_i18n import KEYWORDS, LANG_CODES, SERVICE_KEYWORDS, T
from server import MODEL, SERVICES, QueueEngine

MOBILE = "9000000001"


class Base(unittest.TestCase):
    def setUp(self):
        self.engine = QueueEngine(MODEL)
        self.a = A.Assistant(self.engine)

    def start(self, lang="en"):
        r = self.a.start(lang)
        return r["session"]

    def verify(self, sid):
        r = self.a.secure(sid, MOBILE)
        return self.a.secure(sid, r["side"]["demo_code"])

    def texts(self, r):
        return " ".join(m["text"] for m in r["messages"])


class Translations(unittest.TestCase):
    def test_every_string_exists_in_every_language(self):
        for key, values in T.items():
            self.assertEqual(len(values), len(LANG_CODES), key)
            self.assertTrue(all(v.strip() for v in values), key)

    def test_regional_strings_are_not_english_copies(self):
        allowed = {"st_open"}  # none expected; set kept explicit
        for key, values in T.items():
            for code, v in zip(LANG_CODES[1:], values[1:]):
                self.assertNotEqual(v, values[0], f"{key}/{code}") if key not in allowed else None

    def test_placeholders_match_english(self):
        for key, values in T.items():
            want = sorted(re.findall(r"{(\w+)}", values[0]))
            for code, v in zip(LANG_CODES, values):
                self.assertEqual(sorted(re.findall(r"{(\w+)}", v)), want, f"{key}/{code}")

    def test_every_service_has_a_translation(self):
        for key in SERVICES:
            self.assertIn("svc_" + key, T)

    def test_every_form_field_has_prompt_and_label(self):
        for form in A.FORMS.values():
            for name, _ in form["fields"]:
                self.assertIn("f_" + name, T)
                self.assertIn("l_" + name, T)


class Understanding(Base):
    CASES = [
        ("en", "what is my balance", "balance"), ("hi", "मेरा बैलेंस कितना है", "balance"), ("hi", "balance kitna hai", "balance"),
        ("ta", "என் இருப்பு எவ்வளவு", "balance"), ("te", "నా బ్యాలెన్స్ ఎంత", "balance"), ("kn", "ನನ್ನ ಬ್ಯಾಲೆನ್ಸ್ ಎಷ್ಟು", "balance"),
        ("ml", "എന്റെ ബാലൻസ് എത്രയാണ്", "balance"), ("bn", "আমার ব্যালেন্স কত", "balance"), ("mr", "माझी शिल्लक किती आहे", "balance"),
        ("en", "I lost my card", "card_block"), ("hi", "मेरा कार्ड खो गया", "card_block"), ("ta", "என் கார்டு தொலைந்துவிட்டது", "card_block"),
        ("te", "నా కార్డ్ పోయింది", "card_block"), ("bn", "আমার কার্ড হারিয়ে গেছে", "card_block"),
        ("hi", "मुझे शिकायत करनी है", "complaint"), ("ta", "புதிய கணக்கு தொடங்க வேண்டும்", "open_form"), ("kn", "ಹೊಸ ಖಾತೆ ತೆರೆಯಬೇಕು", "open_form"),
        ("ml", "ടോക്കൺ വേണം", "token"), ("mr", "मला टोकन हवा आहे", "token"), ("en", "talk to a person", "human"),
    ]

    def test_phrases_in_each_language(self):
        for lang, text, want in self.CASES:
            self.assertEqual(A.best_match(KEYWORDS, text, skip=("yes", "no")), want, text)

    def test_short_words_do_not_match_inside_others(self):
        self.assertIsNone(A.yes_or_no("I know nothing"))
        self.assertEqual(A.yes_or_no("no"), "no")
        self.assertEqual(A.yes_or_no("not ok, no"), "no")

    def test_script_switches_language(self):
        self.assertEqual(A.detect_language("எனக்கு உதவி", "en"), "ta")
        self.assertEqual(A.detect_language("मला मदत", "mr"), "mr")  # stays Marathi in Devanagari
        self.assertEqual(A.detect_language("मदद चाहिए", "en"), "hi")
        self.assertIsNone(A.detect_language("hello", "ta"))

    def test_conversation_follows_language_change(self):
        sid = self.start("en")
        r = self.a.message(sid, "எனக்கு டோக்கன் வேண்டும்")
        self.assertEqual(r["lang"], "ta")
        self.assertIn(T["token_choose"][LANG_CODES.index("ta")], self.texts(r))

    def test_regional_digits(self):
        self.assertEqual(A.normalise_digits("५०००"), "5000")
        self.assertEqual(A.normalise_digits("৫০০"), "500")

    def test_handoff_offered_after_repeated_misses(self):
        sid = self.start()
        self.a.message(sid, "xyzzy")
        r = self.a.message(sid, "plugh")
        self.assertEqual(r["actions"][0]["action"], "intent:human")


class Privacy(Base):
    def test_balance_needs_verification(self):
        sid = self.start()
        r = self.a.message(sid, "balance")
        self.assertEqual(r["side"]["type"], "keypad")
        self.assertEqual(self.a.reveal(sid), {"error": "not_verified"})

    def test_balance_never_appears_in_a_reply(self):
        for lang in LANG_CODES:
            sid = self.start(lang)
            self.a.message(sid, action="intent:balance")
            r = self.verify(sid)
            blob = json.dumps(r, ensure_ascii=False)
            self.assertNotIn("84250", blob)
            self.assertNotIn("balance\":", blob)
            self.assertEqual(self.a.reveal(sid)["accounts"][0]["balance"], 84250.75)

    def test_unknown_mobile_gets_same_reply_and_no_code(self):
        sid = self.start()
        self.a.message(sid, action="intent:balance")
        unknown = self.a.secure(sid, "9111111111")
        sid2 = self.start()
        self.a.message(sid2, action="intent:balance")
        known = self.a.secure(sid2, MOBILE)
        self.assertEqual(self.texts(unknown), self.texts(known))
        self.assertIsNone(unknown["side"]["demo_code"])

    def test_wrong_otp_locks_after_three(self):
        sid = self.start()
        self.a.message(sid, action="intent:balance")
        self.a.secure(sid, MOBILE)
        real = self.a.sessions[sid]["otp"]["code"]
        wrong = "000000" if real != "000000" else "111111"
        self.a.secure(sid, wrong)
        self.a.secure(sid, wrong)
        r = self.a.secure(sid, wrong)
        self.assertIn(T["auth_locked"][0], self.texts(r))
        r = self.a.message(sid, action="intent:balance")
        self.assertIn(T["auth_locked"][0], self.texts(r))

    def test_expired_otp_rejected(self):
        sid = self.start()
        self.a.message(sid, action="intent:balance")
        code = self.a.secure(sid, MOBILE)["side"]["demo_code"]
        self.a.sessions[sid]["otp"]["at"] -= A.OTP_TTL + 1
        self.assertIn(T["auth_bad_otp"][0].split(".")[0], self.texts(self.a.secure(sid, code)))

    def test_card_numbers_and_pins_in_chat_are_refused(self):
        for text in ("my card is 4111 1111 1111 1111", "4111-1111-1111-1111", "my pin is 1234", "otp 998877"):
            sid = self.start()
            self.assertIn(T["sensitive"][0], self.texts(self.a.message(sid, text)), text)

    def test_pin_code_is_not_mistaken_for_a_pin(self):
        sid = self.start()
        self.assertNotIn(T["sensitive"][0], self.texts(self.a.message(sid, "pin code 560001")))

    def test_ended_session_is_wiped(self):
        sid = self.start()
        self.a.message(sid, action="intent:balance")
        self.verify(sid)
        self.a.end(sid)
        self.assertTrue(self.a.reveal(sid)["expired"])

    def test_idle_session_expires(self):
        sid = self.start()
        self.a.sessions[sid]["seen"] = time.time() - A.SESSION_TTL - 1
        self.assertTrue(self.a.message(sid, "hello")["expired"])

    def test_verification_expires(self):
        sid = self.start()
        self.a.message(sid, action="intent:balance")
        self.verify(sid)
        self.a.sessions[sid]["auth_at"] -= A.AUTH_TTL + 1
        self.assertEqual(self.a.reveal(sid), {"error": "not_verified"})

    def test_digits_are_redacted_before_claude(self):
        sent = {}

        class Fake:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        sent.update(kw)
                        raise ValueError("stop here")

        c = A.ClaudeIntent()
        c.client, c.anthropic = Fake, type("M", (), {"AnthropicError": Exception})
        self.assertIsNone(c.classify("my number is 9000000001"))
        self.assertNotIn("9000000001", sent["messages"][0]["content"])


class QueueBundle(Base):
    def test_token_reaches_the_queue_with_language(self):
        sid = self.start("ta")
        self.a.message(sid, action="svc:cash_deposit")
        r = self.a.message(sid, action="seg:senior")
        t = self.engine.tokens[r["side"]["token"]]
        self.assertEqual((t["service"], t["segment"], t["language"]), ("cash_deposit", "senior", "தமிழ்"))
        self.assertEqual(self.engine.snapshot()["kpi"]["waiting"], 1)

    def test_every_service_issues_a_token(self):
        for svc in A.TOKEN_SERVICES:
            sid = self.start()
            self.a.message(sid, action="svc:" + svc)
            self.assertEqual(self.a.message(sid, action="seg:regular")["side"]["type"], "token", svc)

    def test_named_service_goes_straight_to_token(self):
        sid = self.start()
        r = self.a.message(sid, "I want to withdraw money")
        self.assertEqual(self.a.sessions[sid]["data"]["service"], "cash_withdrawal")
        self.assertTrue(any(x["action"] == "seg:senior" for x in r["actions"]))

    def test_track_token(self):
        sid = self.start()
        self.a.message(sid, action="svc:passbook")
        tok = self.a.message(sid, action="seg:regular")["side"]["token"]
        self.assertIn(tok, self.texts(self.a.message(sid, f"where is {tok}")))

    def test_closed_counters_do_not_crash(self):
        for c in self.engine.counters:
            c["status"] = "closed"
        sid = self.start()
        self.a.message(sid, action="svc:forex")
        self.assertIn(T["no_counter"][0], self.texts(self.a.message(sid, action="seg:regular")))


class Forms(Base):
    def fill_deposit(self, sid):
        self.a.message(sid, action="form:deposit_slip")
        self.a.message(sid, "4821")
        self.a.message(sid, "5,000")
        return self.a.message(sid, "Anita Rao")

    def test_deposit_form_end_to_end(self):
        sid = self.start("hi")
        r = self.fill_deposit(sid)
        self.assertEqual(r["side"]["type"], "review")
        self.a.message(sid, action="confirm")
        r = self.a.message(sid, action="seg:regular")
        token = self.engine.tokens[r["side"]["token"]]
        self.assertEqual(token["category"], "CASH")
        rec = self.a.records[token["ref"]]
        self.assertEqual(rec["token"], token["token"])
        self.assertEqual(rec["details"][1]["value"], "5000.00")

    def test_every_form_in_every_language(self):
        answers = {"digits4": "4821", "amount": "100", "text": "12 Gandhi Road", "pincode": "600001", "mobile": "9876543210"}
        for lang in LANG_CODES:
            for key, form in A.FORMS.items():
                if form.get("internal"):
                    continue
                sid = self.start(lang)
                self.a.message(sid, action="form:" + key)
                if form["auth"]:
                    self.verify(sid)
                for _, kind in form["fields"]:
                    if kind in A.CHOICES:
                        r = self.a.message(sid, action="choice:" + A.CHOICES[kind][0])
                    else:
                        r = self.a.message(sid, answers[kind])
                self.assertEqual(r["side"]["type"], "review", f"{key}/{lang}")

    def test_invalid_values_reask(self):
        sid = self.start()
        self.a.message(sid, action="form:deposit_slip")
        r = self.a.message(sid, "12")
        self.assertIn(T["invalid"][0], self.texts(r))
        self.assertEqual(self.a.sessions[sid]["data"]["step"], 0)

    def test_amount_with_paise_keeps_its_value(self):
        self.assertEqual(self.a._validate("amount", "₹1,250.50"), "1250.50")
        self.assertIsNone(self.a._validate("amount", "0"))
        self.assertIsNone(self.a._validate("amount", "no money"))

    def test_edit_a_field_from_review(self):
        sid = self.start()
        self.fill_deposit(sid)
        self.a.message(sid, action="edit:amount")
        r = self.a.message(sid, "750")
        self.assertEqual(r["side"]["rows"][1]["value"], "750.00")
        self.assertEqual(self.a.sessions[sid]["flow"], "form_review")

    def test_cancel_stores_nothing(self):
        sid = self.start()
        self.fill_deposit(sid)
        self.a.message(sid, action="cancel")
        self.assertEqual(self.a.records, {})

    def test_language_change_on_review_keeps_the_form(self):
        sid = self.start()
        self.fill_deposit(sid)
        r = self.a.message(sid, action="lang:bn")
        self.assertEqual(r["side"]["type"], "review")
        self.assertEqual(r["side"]["title"], T["form_deposit_slip"][LANG_CODES.index("bn")])

    def test_address_update_requires_verification(self):
        sid = self.start()
        self.assertEqual(self.a.message(sid, action="form:address_update")["side"]["type"], "keypad")


class ServiceRequests(Base):
    def test_chequebook(self):
        sid = self.start("kn")
        self.a.message(sid, action="req:chequebook")
        self.verify(sid)
        r = self.a.message(sid, action="confirm")
        ref = r["side"]["ref"]
        self.assertEqual(self.a.records[ref]["lang"], "kn")
        self.assertIn(ref, self.texts(self.a.message(sid, ref)))
        self.a.set_status(ref, "done")
        self.assertIn(T["st_done"][LANG_CODES.index("kn")], self.texts(self.a.message(sid, ref)))

    def test_card_block_is_urgent_and_never_claims_the_card_is_blocked(self):
        sid = self.start()
        self.a.message(sid, "I lost my card")
        self.verify(sid)
        r = self.a.message(sid, action="confirm")
        t = self.engine.tokens[r["side"]["token"]]
        self.assertEqual(t["segment"], "urgent")
        self.assertIn("not blocked yet", self.texts(r))

    def test_urgent_token_is_called_first(self):
        s1 = self.start()
        self.a.message(s1, action="svc:card_services")
        self.a.message(s1, action="seg:regular")
        s2 = self.start()
        self.a.message(s2, "lost my card")
        self.verify(s2)
        urgent = self.a.message(s2, action="confirm")["side"]["token"]
        counter = self.engine.tokens[urgent]["counter_id"]
        self.assertEqual(self.engine.call_next(counter)["token"], urgent)

    def test_complaint_needs_no_verification_and_keeps_words(self):
        sid = self.start("ml")
        self.a.message(sid, action="req:complaint")
        r = self.a.message(sid, "എടിഎം പണം നൽകിയില്ല")
        self.assertEqual(self.a.records[r["side"]["ref"]]["details"][0]["value"], "എടിഎം പണം നൽകിയില്ല")

    def test_staff_view_shows_urgent_first_and_no_full_mobile(self):
        sid = self.start()
        self.a.message(sid, action="req:complaint")
        self.a.message(sid, "slow service")
        self.a.message(sid, "lost my card")
        self.verify(sid)
        self.a.message(sid, action="confirm")
        view = self.a.staff_view()
        self.assertTrue(view["records"][0]["urgent"])
        self.assertNotIn(MOBILE, json.dumps(view))

    def test_unknown_reference(self):
        sid = self.start()
        self.assertIn(T["ref_unknown"][0], self.texts(self.a.message(sid, "SR-9999")))


class Meta(unittest.TestCase):
    def test_client_meta_has_all_languages(self):
        m = A.client_meta()
        self.assertEqual(set(m["ui"]), set(LANG_CODES))
        self.assertIn("choose_lang", m["ui"]["ta"])

    def test_service_keywords_point_at_real_services(self):
        self.assertTrue(set(SERVICE_KEYWORDS) <= set(A.TOKEN_SERVICES))


if __name__ == "__main__":
    unittest.main()
