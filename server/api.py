"""Minimal HTTP API for HD2Lab Idle Ops (stdlib only).

In production Nginx terminates HTTPS, enforces the admin SSO and proxies
`/admin/idle-ops/api/` to a Unix socket. This module trusts the
`X-HD2Lab-User` header, which only Nginx can set on that socket.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import economy

BASE_PATH = "/admin/idle-ops"
API_PATH = BASE_PATH + "/api/v1"
MAX_BODY = 8 * 1024
OP_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ACTION_TYPES = {"buyServer", "buyUpgrade", "reboot", "reset"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def public_state(state):
    return {
        "requests": state["requests"],
        "run_lifetime": state["run_lifetime"],
        "total_lifetime": state["total_lifetime"],
        "tokens": state["tokens"],
        "reboots": state["reboots"],
        "servers": state["servers"],
        "upgrades": state["upgrades"],
        "achievements": state["achievements"],
        "created_at": state["created_at"],
        "last_sim_at": state["last_sim_at"],
        "last_seen": state["last_seen"],
    }


def build_payload(result, now):
    state = result.state
    return {
        "serverTime": now,
        "state": public_state(state),
        "derived": economy.derived(state),
        "catalog": economy.catalog(state),
        "gains": result.gains,
        "awaySeconds": result.away_seconds,
        "message": result.message,
        "duplicate": result.duplicate,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HD2LabIdleOps/1.0"
    store = None
    dev_user = None
    static_root = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def address_string(self):
        address = self.client_address
        if isinstance(address, tuple):
            return address[0]
        return address or "unix"

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _username(self):
        user = (self.headers.get("X-HD2Lab-User") or "").strip()
        if not user and self.dev_user:
            user = self.dev_user
        return user

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("invalid content length")
        if length <= 0:
            raise ValueError("empty request body")
        if length > MAX_BODY:
            raise OverflowError("request body too large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("body must be a JSON object")
        return value

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == BASE_PATH:
            self.send_response(308)
            self.send_header("Location", BASE_PATH + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == API_PATH + "/health/ready":
            self._json(200, {"status": "ok", "service": "idle-ops"})
            return
        if path == API_PATH + "/state":
            self._handle_state()
            return
        if self.static_root is not None:
            self._serve_static(path)
            return
        self._json(404, {"detail": "not found"})

    def do_HEAD(self):
        if self.static_root is not None and urlsplit(self.path).path.startswith(BASE_PATH + "/"):
            self._serve_static(urlsplit(self.path).path)
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == API_PATH + "/actions":
            self._handle_action()
            return
        if path == API_PATH + "/reset":
            self._handle_action(reset=True)
            return
        self.close_connection = True
        self._json(404, {"detail": "not found"})

    def _handle_state(self):
        user = self._username()
        if not user:
            self._json(401, {"detail": "admin authentication required"})
            return
        now = time.time()
        try:
            result = self.store.session(user, now)
        except (ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        self._json(200, build_payload(result, now))

    def _handle_action(self, reset=False):
        user = self._username()
        if not user:
            self._json(401, {"detail": "admin authentication required"})
            return
        try:
            body = self._read_json()
        except OverflowError:
            self.close_connection = True
            self._json(413, {"detail": "request body too large"})
            return
        except ValueError as exc:
            self._json(400, {"detail": str(exc)})
            return
        if reset:
            action = {"type": "reset"}
        else:
            action = body.get("action")
            if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
                self._json(400, {"detail": "invalid action"})
                return
        op_id = body.get("opId")
        if not isinstance(op_id, str) or not OP_ID.fullmatch(op_id):
            self._json(400, {"detail": "invalid opId"})
            return
        now = time.time()
        try:
            result = self.store.session(user, now, action=action, op_id=op_id)
        except ValueError as exc:
            self._json(400, {"detail": str(exc)})
            return
        except sqlite3.Error:
            self._error(RuntimeError("storage failure"))
            return
        self._json(200, build_payload(result, now))

    def _error(self, exc):
        self.log_message("error: %s", exc)
        self._json(500, {"detail": "internal error"})

    def _serve_static(self, path):
        prefix = BASE_PATH + "/"
        if not path.startswith(prefix):
            self._json(404, {"detail": "not found"})
            return
        relative = unquote(path[len(prefix):]) or "index.html"
        if relative.endswith("/"):
            relative += "index.html"
        if ".." in relative.split("/") or relative.startswith("/"):
            self._json(404, {"detail": "not found"})
            return
        extension = os.path.splitext(relative)[1].lower()
        if extension not in CONTENT_TYPES:
            self._json(404, {"detail": "not found"})
            return
        target = os.path.realpath(os.path.join(self.static_root, relative))
        root = os.path.realpath(self.static_root)
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            self._json(404, {"detail": "not found"})
            return
        with open(target, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[extension])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def make_handler(store, dev_user=None, static_root=None):
    return type(
        "BoundHandler",
        (Handler,),
        {"store": store, "dev_user": dev_user, "static_root": static_root},
    )


def make_server(host, port, **kwargs):
    handler = make_handler(**kwargs)
    return ThreadingHTTPServer((host, port), handler)
