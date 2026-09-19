#!/usr/bin/env python3
"""Local mockup server on :8765 with POST /api/refresh and GET /api/enrich."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
PORT = 8765
_refresh_lock = threading.Lock()
sys.path.insert(0, str(ROOT))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if self.path.startswith("/events-data.js"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            meta = {"count": 0, "updated_at": ""}
            js = ROOT / "events-data.js"
            if js.exists():
                text = js.read_text(encoding="utf-8")
                import re
                m = re.search(r"window\.EVENTS_META\s*=\s*(\{.*?\});", text, re.S)
                if m:
                    meta = json.loads(m.group(1))
            self._json(200, meta)
            return
        if parsed.path == "/api/enrich":
            qs = parse_qs(parsed.query)
            eid = (qs.get("id") or [None])[0]
            try:
                from enrich_summaries import enrich_by_id
                result = enrich_by_id(eid)
                self._json(200, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self.send_error(404)
            return
        if not _refresh_lock.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "refresh_in_progress"})
            return
        try:
            py = ROOT / ".venv" / "bin" / "python"
            if not py.exists():
                py = Path(sys.executable)
            env = os.environ.copy()
            env.setdefault("SKIP_PW", "1")
            proc = subprocess.run(
                [str(py), str(ROOT / "refresh_catalog.py")],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            count = 0
            updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            try:
                start = (proc.stdout or "").find("{")
                end = (proc.stdout or "").find("\nFINAL")
                blob = (proc.stdout or "")[start:end] if start >= 0 else ""
                if blob.strip():
                    stats = json.loads(blob)
                    count = stats.get("count") or stats.get("after") or 0
                    updated_at = stats.get("updated_at") or updated_at
                else:
                    import re
                    text = (ROOT / "events-data.js").read_text(encoding="utf-8")
                    m = re.search(r"window\.EVENTS_META\s*=\s*(\{.*?\});", text, re.S)
                    if m:
                        meta = json.loads(m.group(1))
                        count = meta.get("count", 0)
                        updated_at = meta.get("updated_at", updated_at)
            except Exception:
                pass
            ok = proc.returncode == 0
            self._json(
                200 if ok else 500,
                {"ok": ok, "count": count, "updated_at": updated_at, "log_tail": out[-1500:]},
            )
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
        finally:
            _refresh_lock.release()

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving {ROOT} on http://127.0.0.1:{PORT}/  (POST /api/refresh, GET /api/enrich)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
