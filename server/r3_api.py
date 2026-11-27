"""R3 routes using the existing Idle Ops HTTP server and SSO boundary."""
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from . import github, telemetry
from .api import ADMIN_API, ADMIN_BASE, MAX_BODY, OP_ID, PUBLIC_API, PUBLIC_BASE, SERVICE_ROLE, Handler
from .twin import Conflict, MAX_BONUS, REBOOT_COMMITS, REBOOT_STEP, XP_PER_COMMIT, demo_snapshot

INTERNAL_API = "/internal/idle-ops/v1"
MAX_WEBHOOK = 1024 * 1024


class TwinHandler(Handler):
    twin = None
    webhook_secret = None

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == PUBLIC_API + "/state":
            since = self._since(self.path)
            if parse_qs(url.query).get("demo") == ["1"]:
                self._json(200, demo_snapshot(since))
            else:
                self._dispatch(lambda: self.twin.snapshot(public=True, since=since))
        elif url.path == ADMIN_API + "/state":
            if self._admin():
                self._dispatch(lambda: self.twin.snapshot(since=self._since(self.path)))
        elif url.path == ADMIN_API + "/integrations":
            if self._admin():
                self._dispatch(self.twin.integrations)
        elif url.path == ADMIN_API + "/whoami":
            if self._admin():
                self._json(200, {"user": self._username(), "role": self._role(),
                                 "accountRole": self._account_role(), "canHardReset": False})
        elif url.path == ADMIN_API + "/content":
            if self._admin():
                self._json(200, {"version": 1, "xpPerCommit": XP_PER_COMMIT, "rebootRequired": REBOOT_COMMITS,
                                 "rebootStepBps": REBOOT_STEP, "maxBonusBps": MAX_BONUS})
        elif url.path.startswith((ADMIN_API + "/", PUBLIC_API + "/")) and url.path != ADMIN_API + "/health/ready":
            self._json(404, {"detail": "not found"})
        else:
            if url.path.startswith(ADMIN_BASE + "/") and url.path != ADMIN_API + "/health/ready":
                if not self._admin():
                    return
            super().do_GET()

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        self.close_connection = True
        path = urlsplit(self.path).path
        if path == ADMIN_API + "/actions":
            if self._admin():
                self._dispatch(self._reboot)
        elif path == ADMIN_API + "/reconcile":
            if self._admin():
                self._dispatch(self._reconcile, accepted=202)
        elif path == INTERNAL_API + "/github":
            self._dispatch(self._webhook, accepted=202)
        elif path == INTERNAL_API + "/telemetry":
            self._dispatch(self._telemetry, accepted=202)
        elif path.startswith(PUBLIC_BASE + "/"):
            self._method_not_allowed()
        else:
            self._json(404, {"detail": "not found"})

    def _method_not_allowed(self):
        self.close_connection = True
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def _admin(self):
        if not self._username():
            self._json(401, {"detail": "admin authentication required"})
            return False
        if self._role() != SERVICE_ROLE:
            self._json(403, {"detail": "admiral service role required"})
            return False
        return True

    def _dispatch(self, operation, accepted=200):
        try:
            self._json(accepted, operation())
        except Conflict as exc:
            self._json(409, {"detail": str(exc)})
        except telemetry.TelemetryError as exc:
            self._json(exc.status, {"detail": str(exc)})
        except github.AccessDenied:
            self._json(401, {"detail": "integration authentication required"})
        except PermissionError:
            self._json(403, {"detail": "scope does not allow this project"})
        except OverflowError:
            self._json(413, {"detail": "request body too large"})
        except (ValueError, RecursionError):
            self._json(400, {"detail": "invalid request"})
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                self._json(503, {"detail": "database temporarily busy"})
            else:
                self._error(exc)
        except sqlite3.Error as exc:
            self._error(exc)

    def _reboot(self):
        body = self._read_json()
        action = body.get("action")
        if not isinstance(action, dict) or action.get("type") != "reboot":
            raise ValueError("invalid action")
        op_id = body.get("opId")
        if not isinstance(op_id, str) or not OP_ID.fullmatch(op_id):
            raise ValueError("invalid opId")
        since = body.get("since")
        if type(since) is not int or since < 0:
            since = None
        return self.twin.reboot(action.get("projectId"), op_id, self._username(), since)

    def _reconcile(self):
        self.twin.enqueue(self._read_json().get("projectId"))
        return {"queued": True}

    def _webhook(self):
        if not self.webhook_secret:
            raise github.AccessDenied("webhook disabled")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("invalid content length")
        if length > MAX_WEBHOOK:
            raise OverflowError("request body too large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("truncated request body")
        return github.hook(self.twin, raw, self.headers.get("X-GitHub-Event"),
                           self.headers.get("X-GitHub-Delivery"),
                           self.headers.get("X-Hub-Signature-256"), self.webhook_secret)

    def _telemetry(self):
        scope = telemetry.authenticate(self.headers.get("Authorization"), self.twin.tokens)
        return self.twin.ingest_agent(self._read_json(), scope)


def make_twin_handler(twin, webhook_secret=None, **kwargs):
    return type("BoundTwinHandler", (TwinHandler,), {
        **kwargs, "twin": twin, "store": twin.store, "webhook_secret": webhook_secret
    })
