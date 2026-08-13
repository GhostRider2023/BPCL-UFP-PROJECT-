"""
POST /api/simulate — one run of the coupled thermal-hydraulic kernel.

Body: the console's input vector (see `_engine/service.run_simulation`).
Response: the state at every kilometre, the per-station table, the KPI block,
and the provenance of the boundary condition.

Never cached. Every response embeds a live air temperature and a timestamp, and
a cached simulation would show a reading that is no longer current while
labelling it as one that is.
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


from _http import guard, read_json_body, send

from service import run_simulation


class handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's naming
        guard(self, lambda: send(self, 200, run_simulation(read_json_body(self)), Cache_Control="no-store"))

    def do_GET(self):  # noqa: N802
        send(
            self,
            405,
            {"ok": False, "error": "POST the input vector as JSON to this endpoint."},
            Allow="POST",
        )

    def log_message(self, fmt, *args):
        """Silence the per-request access log; Vercel already records it."""
