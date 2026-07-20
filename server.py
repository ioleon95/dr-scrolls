#!/usr/bin/env python3
"""DR Scrolls NAS server.

Serves the static authoring app (scrolls.html + companions) AND provides a tiny
read/write JSON backend so the PC and phone share ONE copy of the Verse data.

  GET  /                -> scrolls.html
  GET  /api/verses      -> current verses.json  (404 until first save)
  PUT  /api/verses      -> overwrite verses.json (atomic)  [also accepts POST]
  GET  /health          -> "ok"

If AUTH_TOKEN is set, /api/verses (GET + write) requires a matching token via the
X-Auth-Token header or ?token=... query. Static files and /health stay open.

Stdlib only (runs on a stock python:alpine image, no pip). Data lives in
DATA_DIR/verses.json so it persists in a Docker volume, independent of the app.
"""
import hmac
import json
import os
import posixpath
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs

APP_DIR = os.environ.get("APP_DIR", os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(APP_DIR, "data"))
DATA_FILE = os.path.join(DATA_DIR, "verses.json")
PORT = int(os.environ.get("PORT", "8088"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
MAX_BODY = 16 * 1024 * 1024  # 16 MB ceiling on writes

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "DRScrolls/1.0"

    # ---- helpers ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token")

    def _authed(self):
        if not AUTH_TOKEN:
            return True
        got = self.headers.get("X-Auth-Token", "")
        if not got:
            got = (parse_qs(urlparse(self.path).query).get("token", [""]) or [""])[0]
        return hmac.compare_digest(got, AUTH_TOKEN)

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _safe_path(self, url_path):
        """Map a URL path to a file inside APP_DIR, blocking traversal."""
        p = unquote(urlparse(url_path).path)
        if p in ("", "/"):
            p = "/scrolls.html"
        p = posixpath.normpath(p).lstrip("/")
        full = os.path.normpath(os.path.join(APP_DIR, p))
        if not full.startswith(os.path.normpath(APP_DIR)):
            return None
        return full

    # ---- verbs ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, "ok")
        if path == "/api/verses":
            if not self._authed():
                return self._send(401, '{"error":"unauthorized"}', MIME[".json"])
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "rb") as f:
                    data = f.read()
                return self._send(200, data, MIME[".json"])
            return self._send(404, '{"error":"no data yet"}', MIME[".json"])
        # static file
        full = self._safe_path(self.path)
        if not full or not os.path.isfile(full):
            return self._send(404, "Not found")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        return self._send(200, body, MIME.get(ext, "application/octet-stream"))

    do_HEAD = do_GET

    def _write_verses(self):
        if not self._authed():
            return self._send(401, '{"error":"unauthorized"}', MIME[".json"])
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            return self._send(400, '{"error":"bad length"}', MIME[".json"])
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self._send(400, json.dumps({"error": "invalid json: %s" % e}), MIME[".json"])
        os.makedirs(DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8"))
            os.replace(tmp, DATA_FILE)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        n = len(parsed.get("verses", []) if isinstance(parsed, dict) else [])
        return self._send(200, json.dumps({"ok": True, "verses": n}), MIME[".json"])

    def do_PUT(self):
        if urlparse(self.path).path == "/api/verses":
            return self._write_verses()
        return self._send(405, "Method not allowed")

    def do_POST(self):
        if urlparse(self.path).path == "/api/verses":
            return self._write_verses()
        return self._send(405, "Method not allowed")

    def log_message(self, fmt, *args):
        print("[dr-scrolls] " + (fmt % args))


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("[dr-scrolls] serving %s on :%d  (data: %s)" % (APP_DIR, PORT, DATA_FILE))
    print("[dr-scrolls] api auth: %s" % ("ON (token required)" if AUTH_TOKEN else "OFF (open)"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
