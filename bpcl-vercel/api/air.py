"""
GET /api/air — the current air temperature at Bina and Bijwasan.

`?force=1` bypasses the process-lifetime cache, which is what the console's
refresh control sends.

Those two terminals are the only places the line surfaces, so they are the only
places air enters the boundary condition and the only ones queried at all. A
station whose request fails comes back with a `failure` string and a null
temperature — never with a substituted number.
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

from urllib.parse import parse_qs, urlparse

from _http import guard, send

from service import get_air


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        force = query.get("force", ["0"])[0] not in ("0", "", "false")

        def respond():
            result = get_air(force=force)
            send(self, 200 if result["ok"] else 503, result, Cache_Control="no-store")

        guard(self, respond)

    def log_message(self, fmt, *args):
        """Silence the per-request access log; Vercel already records it."""
