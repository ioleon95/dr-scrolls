import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import server


def catalog(name="Sample"):
    return {"_meta": {"gods": {}}, "verses": [{"id": "TEST-01", "name": name,
            "runtime": {key: [] for key in server.RUNTIME_ARRAYS}}]}


class CatalogServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = patch.multiple(server, APP_DIR=str(self.root),
                                     DATA_DIR=str(self.root / "data"),
                                     DATA_FILE=str(self.root / "data" / "verses.json"),
                                     AUTH_TOKEN="test-only-token")
        self.config.start()
        (self.root / "scrolls.html").write_text("test app")
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.config.stop()
        self.temp.cleanup()

    def request(self, method="GET", path="/api/verses", data=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        merged = {"X-Auth-Token": "test-only-token"} if auth else {}
        merged.update(headers or {})
        body = json.dumps(data).encode() if data is not None else None
        conn.request(method, path, body=body, headers=merged)
        result = conn.getresponse()
        status, response_headers, raw = result.status, dict(result.getheaders()), result.read()
        conn.close()
        return status, response_headers, raw

    def seed(self):
        status, headers, _ = self.request("PUT", data=catalog(), headers={"If-None-Match": "*"})
        self.assertEqual(status, 200)
        return headers["ETag"]

    def test_create_read_head_and_exact_revision(self):
        self.assertEqual(self.request()[0], 404)
        etag = self.seed()
        status, headers, body = self.request()
        self.assertEqual((status, headers["ETag"], json.loads(body)), (200, etag, catalog()))
        self.assertEqual(self.request("HEAD")[2], b"")
        self.assertEqual(self.request("PUT", data=catalog("Changed"), headers={"If-Match": etag})[0], 200)
        self.assertNotEqual(self.request()[1]["ETag"], etag)

    def test_old_clients_and_stale_clients_cannot_overwrite(self):
        etag = self.seed()
        for headers, expected in [({}, 428), ({"If-Match": '"stale"'}, 412),
                                  ({"If-None-Match": "*"}, 412), ({"If-Match": "*"}, 400),
                                  ({"If-Match": etag, "If-None-Match": "*"}, 400)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", data=catalog("Lost update"), headers=headers)[0], expected)
                self.assertEqual(json.loads(self.request()[2]), catalog())

    def test_overlapping_mobile_and_pc_save_only_one_wins(self):
        etag = self.seed()
        barrier = threading.Barrier(2)
        def save(name):
            barrier.wait()
            return self.request("PUT", data=catalog(name), headers={"If-Match": etag})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["Mobile", "PC"]))
        self.assertEqual(sorted(r[0] for r in results), [200, 412])
        winner = next(r for r in results if r[0] == 200)
        self.assertEqual(self.request()[1]["ETag"], winner[1]["ETag"])

    def test_schema_failures_leave_saved_catalog_intact(self):
        etag = self.seed()
        invalid = [None, [], {}, {"verses": []}, {"_meta": {}, "verses": []}]
        for key in server.RUNTIME_ARRAYS:
            for value in [None, {}, [1], [None]]:
                bad = catalog()
                bad["verses"][0]["runtime"][key] = value
                invalid.append(bad)
            bad = catalog()
            del bad["verses"][0]["runtime"][key]
            invalid.append(bad)
        duplicate = catalog()
        duplicate["verses"].append(copy.deepcopy(duplicate["verses"][0]))
        invalid.append(duplicate)
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.request("PUT", data=value, headers={"If-Match": etag})[0], 400)
        self.assertEqual(self.request()[1]["ETag"], etag)

    def test_auth_and_static_allowlist(self):
        self.seed()
        (self.root / "server.py").write_text("private source")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("private config")
        for path in ["/api/verses", "/api/verses?token=test-only-token"]:
            self.assertEqual(self.request(path=path, auth=False)[0], 401)
        for path in ["/data/verses.json", "/server.py", "/.git/config", "/%2e%2e/server.py", "/verses.js"]:
            self.assertEqual(self.request(path=path, auth=False)[0], 404)
        self.assertEqual(self.request(path="/", auth=False)[0], 200)
        self.assertEqual(self.request(path="/health", auth=False)[2], b"ok")

    def test_bad_length_and_nonfinite_json(self):
        etag = self.seed()
        self.assertEqual(self.request("PUT", headers={"Content-Length": "bad", "If-Match": etag})[0], 400)
        bad = catalog()
        bad["verses"][0]["runtime"]["modifiers"] = [{"value": float("nan")}]
        self.assertEqual(self.request("PUT", data=bad, headers={"If-Match": etag})[0], 400)
        self.assertEqual(self.request()[1]["ETag"], etag)

    def test_failed_replace_does_not_destroy_current_catalog(self):
        etag = self.seed()
        with patch.object(server.os, "replace", side_effect=OSError("synthetic failure")):
            self.assertEqual(self.request("PUT", data=catalog("Failed"), headers={"If-Match": etag})[0], 500)
        self.assertEqual(self.request()[1]["ETag"], etag)
        self.assertEqual(list((self.root / "data").glob("*.tmp")), [])

    def test_cors_exposes_revision(self):
        status, headers, _ = self.request("OPTIONS")
        self.assertEqual(status, 204)
        self.assertIn("If-Match", headers["Access-Control-Allow-Headers"])
        self.assertEqual(headers["Access-Control-Expose-Headers"], "ETag")


if __name__ == "__main__":
    unittest.main()
