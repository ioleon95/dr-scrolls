#!/usr/bin/env python3
"""DR Scrolls NAS server.

Serves the static authoring app (scrolls.html + companions) AND provides a tiny
read/write JSON backend so the PC and phone share ONE copy of the Verse data.

  GET  /                -> scrolls.html
  GET  /api/verses      -> current verses.json  (404 until first save)
  PUT  /api/verses      -> conditional atomic save [also accepts POST]
  GET  /health          -> "ok"

If AUTH_TOKEN is set, /api/verses (GET + write) requires a matching token via the
X-Auth-Token header. Static app files and /health stay open.
Writes require the last GET's ETag in If-Match, or If-None-Match: * to create.

Stdlib only (runs on a stock python:alpine image, no pip). Data lives in
DATA_DIR/verses.json so it persists in a Docker volume, independent of the app.
"""
import hmac
import hashlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

APP_DIR = os.environ.get("APP_DIR", os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(APP_DIR, "data"))
DATA_FILE = os.path.join(DATA_DIR, "verses.json")
PORT = int(os.environ.get("PORT", "8088"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
MAX_BODY = 16 * 1024 * 1024  # 16 MB ceiling on writes
CATALOG_LOCK = threading.Lock()
# Never expose the data directory, source code, .git, or deployment secrets.
PUBLIC_FILES = frozenset({"scrolls.html", "index.html", "simple.html", "moderate.html",
                          "outline.html", "outline.json", "icon.svg", "manifest.webmanifest"})
RUNTIME_ARRAYS = ("modifiers", "conditionalModifiers", "keywords", "statPolicies", "enginePolicies")


def revision(raw):
    return '"' + hashlib.sha256(raw).hexdigest() + '"'


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON number")


def read_catalog():
    try:
        with open(DATA_FILE, "rb") as source:
            return source.read()
    except FileNotFoundError:
        return None


def validate_catalog(data):
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict) or not isinstance(data.get("verses"), list):
        raise ValueError("Expected a catalog with _meta and verses.")
    if not isinstance(data["_meta"].get("gods"), dict):
        raise ValueError("Expected _meta.gods to be an object.")
    seen = set()
    for verse in data["verses"]:
        if not isinstance(verse, dict) or not isinstance(verse.get("id"), str) or not verse["id"].strip():
            raise ValueError("Each Verse needs a nonempty string id.")
        if verse["id"] in seen:
            raise ValueError("Duplicate Verse id.")
        seen.add(verse["id"])
        runtime = verse.get("runtime")
        if not isinstance(runtime, dict):
            raise ValueError("Each Verse needs a runtime object.")
        for name in RUNTIME_ARRAYS:
            if not isinstance(runtime.get(name), list) or any(not isinstance(row, dict) for row in runtime[name]):
                raise ValueError("Runtime requires five arrays containing objects.")

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
    server_version = "DRScrolls/1.1"

    # ---- helpers ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token, If-Match, If-None-Match")
        self.send_header("Access-Control-Expose-Headers", "ETag")

    def _authed(self):
        if not AUTH_TOKEN:
            return True
        got = self.headers.get("X-Auth-Token", "")
        return hmac.compare_digest(got.encode("utf-8"), AUTH_TOKEN.encode("utf-8"))

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self._cors()
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _safe_path(self, url_path):
        """Only serve known app files, including when DATA_DIR is under APP_DIR."""
        p = unquote(urlparse(url_path).path)
        if p in ("", "/"):
            p = "/scrolls.html"
        p = p.lstrip("/")
        if p not in PUBLIC_FILES:
            return None
        full = os.path.realpath(os.path.join(APP_DIR, p))
        if os.path.dirname(full) != os.path.realpath(APP_DIR):
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
            with CATALOG_LOCK:
                data = read_catalog()
            if data is not None:
                return self._send(200, data, MIME[".json"], {"ETag": revision(data)})
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
        if self.headers.get("Transfer-Encoding"):
            return self._send(400, '{"error":"unsupported transfer encoding"}', MIME[".json"])
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, '{"error":"bad length"}', MIME[".json"])
        if length <= 0 or length > MAX_BODY:
            return self._send(400, '{"error":"bad length"}', MIME[".json"])
        raw = self.rfile.read(length)
        try:
            if len(raw) != length:
                raise ValueError("Incomplete body")
            parsed = json.loads(raw.decode("utf-8"), parse_constant=reject_nonfinite)
            validate_catalog(parsed)
            body = json.dumps(parsed, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            return self._send(400, '{"error":"invalid catalog; check JSON and runtime arrays"}', MIME[".json"])
        match, create = self.headers.get("If-Match"), self.headers.get("If-None-Match")
        if match is None and create is None:
            return self._send(428, '{"error":"load the latest catalog before saving"}', MIME[".json"])
        if (match is not None and create is not None) or (create is not None and create != "*") or match == "*":
            return self._send(400, '{"error":"expected an exact revision or create-only precondition"}', MIME[".json"])
        # The comparison and replacement share one lock: two clients cannot both win.
        # Run one server process per DATA_DIR. External writers must use this API.
        with CATALOG_LOCK:
            current = read_catalog()
            if (create == "*" and current is not None) or (match is not None and (current is None or match != revision(current))):
                return self._send(412, '{"error":"catalog changed; local edits were not saved"}', MIME[".json"])
            os.makedirs(DATA_DIR, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, DATA_FILE)
            except OSError:
                return self._send(500, '{"error":"catalog could not be saved"}', MIME[".json"])
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        return self._send(200, json.dumps({"ok": True, "verses": len(parsed["verses"])}), MIME[".json"], {"ETag": revision(body)})

    def do_PUT(self):
        if urlparse(self.path).path == "/api/verses":
            return self._write_verses()
        return self._send(405, "Method not allowed")

    def do_POST(self):
        if urlparse(self.path).path == "/api/verses":
            return self._write_verses()
        return self._send(405, "Method not allowed")

    def log_message(self, fmt, *args):
        # Never log URL queries, headers, bodies, or supplied credentials.
        print("[dr-scrolls] %s %s" % (self.command, urlparse(self.path).path))


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
