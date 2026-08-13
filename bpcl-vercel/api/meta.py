"""
GET /api/meta — everything the console needs before the first run.

Products, months present in the soil table, route waypoints, booster stations,
slider bounds derived from the geometry, defaults and presets.

All of it is a function of the data files baked into the deployment, so it is
immutable for the life of a deployment and is cached hard at the edge. A new
deployment gets a new URL for its assets and a fresh cache anyway.
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


from _http import guard, send

from service import get_meta


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        guard(
            self,
            lambda: send(
                self,
                200,
                get_meta(),
                Cache_Control="public, max-age=300, s-maxage=86400, stale-while-revalidate=86400",
            ),
        )

    def log_message(self, fmt, *args):
        """Silence the per-request access log; Vercel already records it."""
