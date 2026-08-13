"""
Local API server — the same endpoints, without the Vercel CLI
==============================================================

    python tools/devserver.py [--port 8000]

Serves `/api/meta`, `/api/simulate`, `/api/air` and `/api/export` on localhost so
`npm run dev` works with nothing installed but Python and Node. Vite proxies
`/api/*` here (see `vite.config.ts`).

This is a convenience, not a second implementation: every route below calls the
same function in `_engine/service.py` that the Vercel handler for that route
calls. If you have the Vercel CLI, `vercel dev` runs the real handlers instead
and should behave identically — that equivalence is the whole reason the service
layer exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api", "_engine"))

from service import (  # noqa: E402
    csv_filename,
    get_air,
    get_meta,
    run_simulation,
    simulation_csv,
)


class DevHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MMBL-dev"

    # ── transport ───────────────────────────────────────────────────

    def _send(self, status: int, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(
            status,
            json.dumps(payload, allow_nan=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return parsed

    # ── routes ──────────────────────────────────────────────────────

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        route = urlparse(self.path)
        try:
            if route.path == "/api/meta":
                self._json(200, get_meta())
            elif route.path == "/api/air":
                force = parse_qs(route.query).get("force", ["0"])[0] not in ("0", "", "false")
                result = get_air(force=force)
                self._json(200 if result["ok"] else 503, result)
            elif route.path in ("/", "/health"):
                self._json(200, {"ok": True, "service": "MMBL simulator dev API"})
            else:
                self._json(404, {"ok": False, "error": f"no route {route.path}"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path)
        try:
            if route.path == "/api/simulate":
                self._json(200, run_simulation(self._body()))
            elif route.path == "/api/export":
                payload = self._body()
                self._send(
                    200,
                    simulation_csv(payload).encode("utf-8"),
                    "text/csv; charset=utf-8",
                    Content_Disposition=f'attachment; filename="{csv_filename(payload)}"',
                )
            else:
                self._json(404, {"ok": False, "error": f"no route {route.path}"})
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command:5s} {self.path}  ->  {args[1]}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print("Warming the engine (route geometry + ERA5 soil table)…", flush=True)
    meta = get_meta()
    print(
        f"  {meta['route']['originName']} -> {meta['route']['terminusName']}  "
        f"{meta['route']['lengthKm']:.0f} km  ·  "
        f"{len(meta['months'])} months  ·  {len(meta['products'])} products"
    )
    print(f"\nAPI on http://{args.host}:{args.port}  (Ctrl-C to stop)\n")

    ThreadingHTTPServer((args.host, args.port), DevHandler).serve_forever()


if __name__ == "__main__":
    main()
