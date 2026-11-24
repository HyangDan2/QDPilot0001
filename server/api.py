"""HTTP API for HD2Lab Idle Ops (stdlib only).

Production topology:
  * Nginx terminates HTTPS and enforces admin SSO on ``/admin/idle-ops/``.
    Only that path may apply actions; the trusted ``X-HD2Lab-User`` header is
    forwarded there.
  * ``/idle-ops/`` is the public, read-only world viewer. It can only read
    state; the public API rejects every write method.

The shared world is advanced lazily inside the store on every read.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import economy
from .store import OpConflict, SnapshotCache

ADMIN_BASE = "/admin/idle-ops"
ADMIN_API = ADMIN_BASE + "/api/v1"
PUBLIC_BASE = "/idle-ops"
PUBLIC_API = PUBLIC_BASE + "/api/v1"
MAX_BODY = 8 * 1024
OP_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ACTION_TYPES = {"buyServer", "buyUpgrade", "reboot", "reset"}
HARD_RESET_CONFIRM = "HARD RESET"
OVERLORD_ROLE = "admiral"
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
        "events": result.events,
        "cursor": result.cursor,
        "cursorReset": bool(result.cursor_reset),
        "eventGap": bool(result.event_gap),
        **({"timestamps": result.timestamps} if result.timestamps else {}),
    }


def _reject_json_constant(token):
    raise ValueError(f"invalid JSON constant: {token}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HD2LabIdleOps/1.1"
    store = None
    snapshot = None
    dev_user = None
    dev_role = None
    static_root = None
    public_root = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def address_string(self):
        address = self.client_address
        if isinstance(address, tuple):
            return address[0]
        return address or "unix"

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
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

    def _role(self):
        role = (self.headers.get("X-HD2Lab-Role") or "").strip()
        if not role and self.dev_role:
            role = self.dev_role
        return role

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
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("body must be a JSON object")
        return value

    @staticmethod
    def _since(url):
        values = parse_qs(urlsplit(url).query).get("since")
        if not values:
            return None
        try:
            return max(0, int(values[0]))
        except (TypeError, ValueError):
            return None

    def do_GET(self):
        url = self.path
        path = urlsplit(url).path
        if path == ADMIN_BASE:
            self._redirect(ADMIN_BASE + "/")
            return
        if path == PUBLIC_BASE:
            self._redirect(PUBLIC_BASE + "/")
            return
        if path == ADMIN_API + "/health/ready":
            self._json(200, {"status": "ok", "service": "idle-ops"})
            return
        if path == ADMIN_API + "/state":
            self._handle_state(public=False)
            return
        if path == PUBLIC_API + "/state":
            self._handle_public_state()
            return
        if path.startswith(ADMIN_API + "/") or path.startswith(PUBLIC_API + "/"):
            self._json(404, {"detail": "not found"})
            return
        if path.startswith(ADMIN_BASE + "/") and self.static_root is not None:
            self._serve_static(path, ADMIN_BASE, self.static_root)
            return
        if path.startswith(PUBLIC_BASE + "/") and self.public_root is not None:
            self._serve_static(path, PUBLIC_BASE, self.public_root)
            return
        self._json(404, {"detail": "not found"})

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path.startswith(ADMIN_BASE + "/") and self.static_root is not None:
            self._serve_static(path, ADMIN_BASE, self.static_root)
            return
        if path.startswith(PUBLIC_BASE + "/") and self.public_root is not None:
            self._serve_static(path, PUBLIC_BASE, self.public_root)
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == ADMIN_API + "/actions":
            self._handle_action()
            return
        if path == ADMIN_API + "/reset":
            self._handle_action(reset=True)
            return
        if path == ADMIN_API + "/hard-reset":
            self._handle_hard_reset()
            return
        if path.startswith(PUBLIC_API + "/"):
            self.close_connection = True
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.close_connection = True
        self._json(404, {"detail": "not found"})

    def _redirect(self, location):
        self.send_response(308)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_state(self, public=False):
        user = "" if public else self._username()
        if not public and not user:
            self._json(401, {"detail": "admin authentication required"})
            return
        try:
            result = self.store.world(actor=user, since=self._since(self.path))
        except (ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        self._json(200, build_payload(result, result.now))

    def _handle_public_state(self):
        try:
            result = self.snapshot.latest(self._since(self.path))
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._error(exc)
            return
        self._json(200, build_payload(result, result.now))

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
        since = body.get("since")
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            since = None
        try:
            result = self.store.world(action=action, op_id=op_id, actor=user, since=since)
        except OpConflict as exc:
            self._json(409, {"detail": str(exc)})
            return
        except ValueError as exc:
            self._json(400, {"detail": str(exc)})
            return
        except sqlite3.Error:
            self._error(RuntimeError("storage failure"))
            return
        self._json(200, build_payload(result, result.now))

    def _handle_hard_reset(self):
        user = self._username()
        if not user:
            self._json(401, {"detail": "admin authentication required"})
            return
        if self._role() != OVERLORD_ROLE:
            self._json(403, {"detail": f"{OVERLORD_ROLE} role required"})
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
        if body.get("confirm") != HARD_RESET_CONFIRM:
            self._json(400, {"detail": f"confirmation must be {HARD_RESET_CONFIRM}"})
            return
        op_id = body.get("opId")
        if not isinstance(op_id, str) or not OP_ID.fullmatch(op_id):
            self._json(400, {"detail": "invalid opId"})
            return
        try:
            result = self.store.hard_reset(op_id=op_id, actor=user)
        except OpConflict as exc:
            self._json(409, {"detail": str(exc)})
            return
        except (OSError, sqlite3.Error) as exc:
            self._error(RuntimeError(f"hard reset failed: {exc}"))
            return
        payload = build_payload(result, result.now)
        payload["backup"] = (result.details or {}).get("backup")
        self._json(200, payload)

    def _error(self, exc):
        self.log_message("error: %s", exc)
        self._json(500, {"detail": "internal error"})

    def _serve_static(self, path, base, root_dir):
        prefix = base + "/"
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
        target = os.path.realpath(os.path.join(root_dir, relative))
        root = os.path.realpath(root_dir)
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


def make_handler(store, dev_user=None, static_root=None, public_root=None, dev_role=None, snapshot=None):
    return type(
        "BoundHandler",
        (Handler,),
        {
            "store": store,
            "snapshot": snapshot if snapshot is not None else SnapshotCache(store),
            "dev_user": dev_user,
            "static_root": static_root,
            "public_root": public_root,
            "dev_role": dev_role,
        },
    )


def make_server(host, port, **kwargs):
    handler = make_handler(**kwargs)
    return ThreadingHTTPServer((host, port), handler)
