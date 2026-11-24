"""Run the Idle Ops API over a Unix socket (production) or TCP (local dev)."""

from __future__ import annotations

import argparse
import os
import signal
import socketserver
import stat
import sys

from .api import Server, make_handler
from .store import Store


class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def _prepare_socket(path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise SystemExit(f"refusing to replace non-socket path: {path}")
        os.unlink(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="HD2Lab Idle Ops API")
    parser.add_argument("--db", default=os.environ.get("IDLE_OPS_DB", "./data/idle-ops.sqlite3"))
    parser.add_argument("--uds", help="Unix socket path (production)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--socket-mode", type=lambda value: int(value, 8), default=0o660)
    parser.add_argument("--dev-user", help="fallback X-HD2Lab-User for local preview only")
    parser.add_argument("--dev-role", help="fallback X-HD2Lab-Role for local preview only")
    parser.add_argument("--static-root", help="serve the admin UI from this folder (local preview only)")
    parser.add_argument("--public-root", help="serve the public world UI from this folder (local preview only)")
    args = parser.parse_args(argv)

    store = Store(args.db)
    handler = make_handler(
        store,
        dev_user=args.dev_user,
        dev_role=args.dev_role,
        static_root=args.static_root,
        public_root=args.public_root,
    )
    if args.uds:
        _prepare_socket(args.uds)
        server = ThreadingUnixHTTPServer(args.uds, handler)
        os.chmod(args.uds, args.socket_mode)
        print(f"idle-ops listening on unix:{args.uds} db={args.db}", file=sys.stderr)
    else:
        server = Server((args.host, args.port), handler)
        print(f"idle-ops listening on http://{args.host}:{args.port} db={args.db}", file=sys.stderr)

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.uds and os.path.exists(args.uds):
            os.unlink(args.uds)


if __name__ == "__main__":
    main()
