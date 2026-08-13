"""
POST /api/export — the full state table at every kilometre, as CSV.

Body is the same input vector `/api/simulate` takes, so the download is the run
on screen rather than a separately parameterised one. Every column the kernel
produces is included, not just the ones the dashboard draws.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

# Vercel executes this file directly, so `api/` is not guaranteed to be on
# sys.path. Both paths are added here rather than relying on `_http` to add the
# engine one as a side effect — that would make the imports below order-
# dependent, and any formatter that sorts them would break the deployment.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_HERE, os.path.join(_HERE, "_engine")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


from _http import guard, read_json_body, send, send_text

from service import csv_filename, simulation_csv


class handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        def respond():
            payload = read_json_body(self)
            send_text(
                self,
                200,
                simulation_csv(payload),
                "text/csv; charset=utf-8",
                Content_Disposition=f'attachment; filename="{csv_filename(payload)}"',
                Cache_Control="no-store",
            )

        guard(self, respond)

    def do_GET(self):  # noqa: N802
        send(
            self,
            405,
            {"ok": False, "error": "POST the input vector as JSON to this endpoint."},
            Allow="POST",
        )

    def log_message(self, fmt, *args):
        """Silence the per-request access log; Vercel already records it."""
