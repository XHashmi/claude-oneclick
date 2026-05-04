import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from claude_oneclick.discover import DiscoverError, list_models


class _Stub(BaseHTTPRequestHandler):
    payload: dict = {}
    require_auth: bool = False

    def log_message(self, *a, **kw): pass  # silence

    def do_GET(self):  # noqa: N802
        if self.require_auth and not (self.headers.get("Authorization") or "").startswith("Bearer "):
            self.send_response(401); self.end_headers(); return
        if self.path in ("/v1/models", "/api/tags"):
            body = json.dumps(self.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404); self.end_headers()


class DiscoverTests(unittest.TestCase):
    def _serve(self, payload, require_auth=False, *, ollama=False):
        _Stub.payload = payload
        _Stub.require_auth = require_auth
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        port = srv.server_address[1]
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        # Ollama discovery only kicks in on localhost:11434, so we fake the URL.
        return srv, f"http://127.0.0.1:{port}", thr

    def test_openai_models(self):
        srv, base, _ = self._serve({"data": [{"id": "m-a"}, {"id": "m-b"}]})
        try:
            self.assertEqual(list_models(base), ["m-a", "m-b"])
        finally:
            srv.shutdown(); srv.server_close()

    def test_openai_models_with_auth(self):
        srv, base, _ = self._serve({"data": [{"id": "x"}]}, require_auth=True)
        try:
            self.assertEqual(list_models(base, "sk-test"), ["x"])
            with self.assertRaises(DiscoverError):
                list_models(base, "")  # 401
        finally:
            srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
