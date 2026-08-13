"""
HTTP plumbing shared by the Vercel functions
=============================================

Vercel's Python runtime routes each `api/*.py` to a `BaseHTTPRequestHandler`
subclass named `handler`. Files whose names begin with an underscore are not
routed, which is what makes this one a library rather than an endpoint.

Everything here is transport: reading a body, writing a status line, setting
cache headers. The answers themselves come from `_engine/service.py`, which the
local dev server calls through the same functions — so a response cannot differ
between `vercel dev` and production because the shells drifted.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict

ENGINE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_engine")
if ENGINE_ROOT not in sys.path:
    sys.path.insert(0, ENGINE_ROOT)

MAX_BODY_BYTES = 64 * 1024  # a request body here is a dozen numbers; 64 KB is generous


def read_json_body(request: BaseHTTPRequestHandler) -> Dict[str, Any]:
    """Parse the request body as a JSON object. An absent body is an empty one."""
    try:
        length = int(request.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    if length > MAX_BODY_BYTES:
        raise ValueError(f"request body exceeds {MAX_BODY_BYTES} bytes")

    raw = request.rfile.read(length)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a JSON object")
    return parsed


def send(request: BaseHTTPRequestHandler, status: int, payload: Any, **headers: str) -> None:
    """Write one JSON response."""
    body = json.dumps(payload, allow_nan=False).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Content-Length", str(len(body)))
    for name, value in headers.items():
        request.send_header(name.replace("_", "-"), value)
    request.end_headers()
    request.wfile.write(body)


def send_text(
    request: BaseHTTPRequestHandler, status: int, text: str, content_type: str, **headers: str
) -> None:
    body = text.encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", content_type)
    request.send_header("Content-Length", str(len(body)))
    for name, value in headers.items():
        request.send_header(name.replace("_", "-"), value)
    request.end_headers()
    request.wfile.write(body)


def guard(request: BaseHTTPRequestHandler, fn: Callable[[], Any]) -> None:
    """Run an endpoint body, turning any escape into a JSON error response.

    A serverless function that raises returns an opaque platform 500 with the
    traceback buried in the log, which is useless from a browser. The message is
    surfaced instead; the traceback still goes to the log, where it belongs.
    """
    try:
        fn()
    except ValueError as exc:
        send(request, 400, {"ok": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — the outermost boundary; nothing escapes
        traceback.print_exc()
        send(request, 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
