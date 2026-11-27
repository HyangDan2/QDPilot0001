"""Smoke and load the R3 server over a disposable Unix socket with no credentials."""
import argparse
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import twin
from server.store import Store

PUBLIC = "/idle-ops/api/v1/state"


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def fetch(path, route, method="GET"):
    start = time.perf_counter()
    conn = UnixConnection(path)
    try:
        conn.request(method, route, headers={"Connection": "close"})
        response = conn.getresponse()
        body = response.read()
        return response.status, json.loads(body) if body else None, (time.perf_counter() - start) * 1000
    finally:
        conn.close()


def run(scratch, seconds, viewers):
    db = scratch / "qa.sqlite3"
    uds = scratch / "api.sock"
    raw = json.dumps([
        {"id": i, "repo": f"local/fixture-{i}", "name": f"Test Rack {i}", "refs": ["main"], "publish": True}
        for i in range(1, 41)
    ])
    service = twin.Twin(Store(db), twin.registry(raw))
    now = time.time()
    with service.transaction(write=True) as con:
        con.execute("UPDATE twin_projects SET public_verified=1,observed_at=?", (now,))
        for repo in service.projects:
            for number in range(24):
                twin.signal(con, repo, (f"issue:{number}", "bug", "open", now, 0), now, False)
    with (scratch / "service-stderr").open("wb") as log:
        child = subprocess.Popen([sys.executable, "-m", "server", "--db", str(db), "--uds", str(uds),
                                  "--static-root", "static", "--public-root", "static/public"],
                                 cwd=ROOT, env={**os.environ, "IDLE_OPS_CONFIG": "",
                                                "IDLE_OPS_REPOSITORIES": raw,
                                                "IDLE_OPS_TELEMETRY_TOKENS": "{}",
                                                "IDLE_OPS_GITHUB_WEBHOOK_SECRET": "runtime-qa-fake-secret-placeholder"},
                                 stdout=log, stderr=log)
        try:
            for _ in range(100):
                if uds.exists() or child.poll() is not None:
                    break
                time.sleep(0.05)
            assert fetch(str(uds), "/admin/idle-ops/api/v1/health/ready")[0] == 200
            assert fetch(str(uds), "/admin/idle-ops/api/v1/state")[0] == 401
            assert fetch(str(uds), PUBLIC, "POST")[0] == 405
            deadline = time.monotonic() + seconds

            def poll(_):
                timings = []
                while time.monotonic() < deadline:
                    status, data, elapsed = fetch(str(uds), PUBLIC)
                    assert status == 200 and len(data["projects"]) == 40
                    assert sum(p["bugCount"] for p in data["projects"]) == 960
                    timings.append(elapsed)
                    time.sleep(0.2)
                return timings

            with concurrent.futures.ThreadPoolExecutor(max_workers=viewers) as pool:
                timing = sorted(ms for sample in pool.map(poll, range(viewers)) for ms in sample)
            ps = subprocess.check_output(["ps", "-o", "rss=", "-p", str(child.pid)], text=True)
            assert child.poll() is None
            return {"requests": len(timing), "errors": 0, "seconds": seconds, "viewers": viewers,
                    "p50_ms": round(statistics.median(timing), 2),
                    "p95_ms": round(timing[int((len(timing) - 1) * 0.95)], 2),
                    "max_ms": round(timing[-1], 2), "end_rss_kib": int(ps.strip())}
        finally:
            child.terminate()
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--viewers", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 180 or not 1 <= args.viewers <= 24:
        parser.error("select 1–180 seconds and 1–24 viewers")
    with tempfile.TemporaryDirectory(prefix="idle-r3-runtime-", dir="/tmp") as tmp:
        print(json.dumps(run(Path(tmp), args.seconds, args.viewers)))


if __name__ == "__main__":
    main()
