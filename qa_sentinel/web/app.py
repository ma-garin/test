"""台帳の Web 表示。標準ライブラリだけ。/api/tasks, /api/tasks/<id>, POST /api/approve, / で index.html。"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.ledger import Ledger
from ..core.orchestrator import approve
from ..core.state import GATES, PHASE_LABELS, PHASES

_STATIC = Path(__file__).parent / "static"


def make_handler(ledger: Ledger):
    class H(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                b = (_STATIC / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/meta":
                self._json({"phases": list(PHASES), "labels": PHASE_LABELS, "gates": GATES})
            elif u.path == "/api/tasks":
                self._json([t.to_dict() for t in ledger.list()])
            elif u.path.startswith("/api/tasks/"):
                try:
                    self._json(ledger.load(u.path.rsplit("/", 1)[1]).to_dict())
                except FileNotFoundError:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            q = parse_qs(self.rfile.read(n).decode())
            if u.path == "/api/approve":
                try:
                    t = approve(q["task"][0], q.get("by", ["web"])[0], ledger)
                    self._json(t.to_dict())
                except (KeyError, ValueError, FileNotFoundError) as e:
                    self._json({"error": str(e)}, 400)
            else:
                self._json({"error": "not found"}, 404)

        def log_message(self, *a):  # 静かに
            pass

    return H


def serve(ledger: Ledger, host: str = "127.0.0.1", port: int = 8790) -> None:
    srv = ThreadingHTTPServer((host, port), make_handler(ledger))
    print(f"qa-sentinel web: http://{host}:{port}/  (Ctrl+C で停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
