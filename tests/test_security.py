"""Targeted tests for the security defenses described in SECURITY.md."""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.request
from http.client import HTTPConnection

from tests._helpers import isolated_home


def _start_ui(port: int):
    from claude_oneclick.server import serve
    t = threading.Thread(target=lambda: serve(host="127.0.0.1", port=port, open_browser=False), daemon=True)
    t.start()
    # Wait for the listen socket.
    import socket
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"UI server didn't come up on {port}")


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class HostHeaderDefenseTests(unittest.TestCase):
    def test_foreign_host_is_rejected(self):
        with isolated_home():
            port = _free_port()
            # Force the config to use this port so _allowed_hosts() agrees.
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            # Foreign host: request claims to be evil.com (a DNS-rebound origin).
            conn = HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/api/state", headers={"Host": "evil.com"})
            r = conn.getresponse()
            body = r.read()
            self.assertEqual(r.status, 403)
            self.assertIn(b"host_not_allowed", body)
            conn.close()

    def test_localhost_host_is_accepted(self):
        with isolated_home():
            port = _free_port()
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2)
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertIn("csrf", data)


class CsrfTests(unittest.TestCase):
    def test_post_without_csrf_is_rejected(self):
        with isolated_home():
            port = _free_port()
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/toggle",
                data=json.dumps({"enabled": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=2)
                self.fail("expected 403")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)

    def test_export_requires_csrf(self):
        # /api/export contains plaintext API keys → CSRF protected on GET.
        with isolated_home():
            port = _free_port()
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/export", timeout=2)
                self.fail("expected 403")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)


class StaticPathTraversalTests(unittest.TestCase):
    def test_traversal_blocked(self):
        with isolated_home():
            port = _free_port()
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/static/../../../etc/passwd", timeout=2)
                self.fail("expected 4xx")
            except urllib.error.HTTPError as e:
                self.assertIn(e.code, (400, 404))


class StateRedactionTests(unittest.TestCase):
    def test_state_redacts_api_keys(self):
        with isolated_home():
            from claude_oneclick.config import update_override
            update_override("deepseek", {"api_key": "sk-supersecret"})
            port = _free_port()
            from claude_oneclick.config import load, save
            cfg = load(); cfg["ui"]["port"] = port; save(cfg)
            _start_ui(port)
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2)
            data = json.loads(r.read().decode("utf-8"))
            for p in data["presets"]:
                if p["name"] == "deepseek":
                    self.assertEqual(p["api_key"], "")
                    self.assertTrue(p["api_key_set"])
                    return
            self.fail("deepseek preset not in state")


if __name__ == "__main__":
    unittest.main()
