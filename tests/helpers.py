"""Shared test tools: a real server, an HTTP client and fixed dates."""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
sys.path.insert(0, BACKEND)

# Appointment tests run on a fixed Monday, so they pass on any day and at any hour
FIXED_NOW = datetime(2026, 11, 2, 10, 0)
TODAY, TOMORROW = "2026-11-02", "2026-11-03"
DEMO_MOBILE = "9000000001"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.text = body.decode("utf-8", "replace")

    @property
    def json(self):
        return json.loads(self.text)


class Api:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
            conn.request(method, path, payload, {"Content-Type": "application/json", **(headers or {})})
            res = conn.getresponse()
            return Response(res.status, dict(res.getheaders()), res.read())
        finally:
            conn.close()

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcess:
    """Runs backend/server.py as a separate process, the way a branch would."""

    def __init__(self, python_flags=(), env=None):
        self.port = free_port()
        self.api = Api(self.port)
        self.log = tempfile.TemporaryFile()
        full_env = {**os.environ, "PORT": str(self.port), "PYTHONIOENCODING": "utf-8", **(env or {})}
        self.proc = subprocess.Popen(
            [sys.executable, "-u", *python_flags, os.path.join(BACKEND, "server.py")],
            cwd=tempfile.gettempdir(),  # must work from any folder
            env=full_env, stdout=self.log, stderr=subprocess.STDOUT)
        self._wait_until_ready()

    def _wait_until_ready(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Server exited early:\n{self.output()}")
            try:
                if self.api.get("/api/meta").status == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"Server did not start:\n{self.output()}")

    def output(self):
        self.log.seek(0)
        return self.log.read().decode("utf-8", "replace")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.log.close()


class ApiCase(unittest.TestCase):
    """Real HTTP against the real handler, with clean state for every test."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.httpd = server.MitraServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.api = Api(cls.httpd.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.assertEqual(self.api.post("/api/reset").status, 200)
        self.server.BOOK.items.clear()
        self.server.BOOK.clock = lambda: FIXED_NOW

    def ok(self, response):
        self.assertEqual(response.status, 200, response.text)
        return response.json

    def start_kiosk(self, lang="en"):
        return self.ok(self.api.post("/api/assist/start", {"lang": lang}))

    def say(self, session, text):
        return self.ok(self.api.post("/api/assist/message", {"session": session, "text": text}))

    def press(self, session, action):
        return self.ok(self.api.post("/api/assist/message", {"session": session, "action": action}))

    def keypad(self, session, value):
        return self.ok(self.api.post("/api/assist/secure", {"session": session, "value": value}))

    def verify(self, session):
        code = self.keypad(session, DEMO_MOBILE)["side"]["demo_code"]
        return self.keypad(session, code)

    def book(self, kind="rm", day=TOMORROW, time_="11:00", name="Anita Rao", **extra):
        return self.ok(self.api.post("/api/appointments", {"type": kind, "date": day, "time": time_, "name": name, **extra}))
