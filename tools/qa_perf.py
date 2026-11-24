"""Headless QA harness for the Idle Ops HTTP API (stdlib only).

Modes:
  public  --viewers N --seconds S [--interval 8]   read-only viewer polling
  action  --workers N --ops M                      concurrent admin buys (unique opIds)
  burst   --workers N --ops M                      same as action but no think time

Reports latency P50/P95/P99, status counts, API-contract violations, SQLite
lock errors in responses, and optional server RSS samples. Output is JSON so a
report can quote exact numbers. This measures the host it runs on; label
results with that host (do not present them as Raspberry Pi or production).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ADMIN = "/admin/idle-ops/api/v1"
PUBLIC = "/idle-ops/api/v1"
FLAG_KEYS = ("actor", "opId", "op_id", "backup")
# Bypass HTTP(S)_PROXY: a local harness must not measure a forward proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url, headers=None, body=None, timeout=10.0):
    request = urllib.request.Request(url, data=body, headers=headers or {})
    start = time.perf_counter()
    try:
        with OPENER.open(request, timeout=timeout) as response:
            payload = response.read()
            return response.status, payload, time.perf_counter() - start
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.perf_counter() - start
    except Exception as exc:  # timeouts, resets, DNS
        return 0, str(exc).encode(), time.perf_counter() - start


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    low = int(k)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def summarize(latencies, statuses, violations, lock_errors, wall_seconds):
    latencies_ms = [value * 1000.0 for value in latencies]
    return {
        "requests": len(latencies),
        "status": {str(code): statuses.count(code) for code in sorted(set(statuses))},
        "p50_ms": round(percentile(latencies_ms, 50) or 0.0, 2),
        "p95_ms": round(percentile(latencies_ms, 95) or 0.0, 2),
        "p99_ms": round(percentile(latencies_ms, 99) or 0.0, 2),
        "max_ms": round(max(latencies_ms) if latencies_ms else 0.0, 2),
        "rps": round(len(latencies) / wall_seconds, 3) if wall_seconds else None,
        "violations": violations,
        "lock_errors": lock_errors,
    }


def sample_rss(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return round(int(out.stdout.strip()) / 1024.0, 1)
    except Exception:
        return None


def _run_viewers(base, viewers, seconds, interval):
    deadline = time.monotonic() + seconds
    latencies, statuses = [], []
    violations = []
    lock = threading.Lock()

    def viewer(_):
        cursor = 0
        while time.monotonic() < deadline:
            url = f"{base}{PUBLIC}/state?since={cursor}"
            status, payload, elapsed = fetch(url)
            with lock:
                latencies.append(elapsed)
                statuses.append(status)
            if status != 200:
                with lock:
                    violations.append({"status": status, "body": payload[:120].decode("utf-8", "replace")})
            else:
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    with lock:
                        violations.append({"status": status, "body": "invalid json"})
                    continue
                missing = [key for key in ("serverTime", "state", "derived", "cursor") if key not in data]
                if missing:
                    with lock:
                        violations.append({"missing": missing})
                text = payload.decode("utf-8", "replace")
                for flag in FLAG_KEYS:
                    if f'"{flag}"' in text:
                        with lock:
                            violations.append({"leak": flag})
                if isinstance(data.get("cursor"), int):
                    cursor = data["cursor"]
                if data.get("message") is not None:
                    with lock:
                        violations.append({"leak": "message"})
            time.sleep(interval)

    threads = [threading.Thread(target=viewer, args=(index,), daemon=True) for index in range(viewers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return latencies, statuses, violations


def _viewer_process(base, viewers, seconds, interval, queue):
    try:
        queue.put(_run_viewers(base, viewers, seconds, interval))
    except Exception as exc:
        queue.put(([], [0], [{"worker_error": str(exc)}]))


def public_mode(base, viewers, seconds, interval, pid, processes=1):
    rss = []

    def sampler(deadline):
        while time.monotonic() < deadline:
            value = sample_rss(pid) if pid else None
            if value is not None:
                rss.append(value)
            time.sleep(5)

    latencies, statuses, violations = [], [], []
    if processes > 1 and viewers > 1:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        split = [viewers // processes + (1 if index < viewers % processes else 0) for index in range(processes)]
        workers = [
            context.Process(target=_viewer_process, args=(base, count, seconds, interval, queue))
            for count in split if count > 0
        ]
        deadline = time.monotonic() + seconds
        for worker in workers:
            worker.start()
        sampler_thread = threading.Thread(target=sampler, args=(deadline,), daemon=True)
        if pid:
            sampler_thread.start()
        for _ in workers:
            part_latency, part_status, part_violations = queue.get(timeout=seconds + 30)
            latencies.extend(part_latency)
            statuses.extend(part_status)
            violations.extend(part_violations)
        for worker in workers:
            worker.join()
    else:
        deadline = time.monotonic() + seconds
        if pid:
            sampler_thread = threading.Thread(target=sampler, args=(deadline,), daemon=True)
            sampler_thread.start()
        latencies, statuses, violations = _run_viewers(base, viewers, seconds, interval)
    summary = summarize(latencies, statuses, violations, [], wall_seconds=seconds)
    summary["client_processes"] = processes
    if rss:
        summary["rss_mb"] = {"first": rss[0], "max": max(rss), "samples": len(rss)}
    return summary


def action_mode(base, workers, ops, user, tier, burst):
    latencies, statuses = [], []
    violations, lock_errors = [], []
    lock = threading.Lock()
    start_gate = threading.Event()

    def worker(index):
        start_gate.wait()
        for n in range(ops):
            op_id = f"qa-{index}-{n}-{uuid.uuid4().hex[:10]}"
            body = json.dumps({"opId": op_id, "action": {"type": "buyServer", "tier": tier, "qty": 1}}).encode()
            headers = {"Content-Type": "application/json", "X-HD2Lab-User": user}
            status, payload, elapsed = fetch(f"{base}{ADMIN}/actions", headers, body)
            with lock:
                latencies.append(elapsed)
                statuses.append(status)
            if status != 200:
                text = payload.decode("utf-8", "replace")
                with lock:
                    violations.append({"status": status, "body": text[:160]})
                    if "locked" in text.lower():
                        lock_errors.append(text[:160])
            if not burst:
                time.sleep(0.05)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    started = time.perf_counter()
    start_gate.set()
    for thread in threads:
        thread.join()
    wall_seconds = time.perf_counter() - started
    summary = summarize(latencies, statuses, violations, lock_errors, wall_seconds)
    summary["wall_seconds"] = round(wall_seconds, 2)
    return summary


def admin_read_mode(base, workers, ops, user, burst):
    latencies, statuses = [], []
    violations, lock_errors = [], []
    lock = threading.Lock()
    start_gate = threading.Event()

    def worker(index):
        start_gate.wait()
        for _ in range(ops):
            headers = {"X-HD2Lab-User": f"{user}-{index}"}
            status, payload, elapsed = fetch(f"{base}{ADMIN}/state", headers)
            with lock:
                latencies.append(elapsed)
                statuses.append(status)
            if status != 200:
                text = payload.decode("utf-8", "replace")
                with lock:
                    violations.append({"status": status, "body": text[:160]})
                    if "locked" in text.lower():
                        lock_errors.append(text[:160])
            if not burst:
                time.sleep(0.02)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    started = time.perf_counter()
    start_gate.set()
    for thread in threads:
        thread.join()
    wall_seconds = time.perf_counter() - started
    summary = summarize(latencies, statuses, violations, lock_errors, wall_seconds)
    summary["wall_seconds"] = round(wall_seconds, 2)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("public", "action", "burst", "adminread"))
    parser.add_argument("--base", default="http://127.0.0.1:8124")
    parser.add_argument("--viewers", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--interval", type=float, default=8)
    parser.add_argument("--ops", type=int, default=5)
    parser.add_argument("--user", default="qa")
    parser.add_argument("--tier", default="cache")
    parser.add_argument("--rss-pid", type=int)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.mode == "public":
        summary = public_mode(
            args.base, args.viewers, args.seconds, args.interval, args.rss_pid, args.processes
        )
    elif args.mode == "adminread":
        summary = admin_read_mode(args.base, args.workers, args.ops, args.user, burst=False)
    else:
        summary = action_mode(args.base, args.workers, args.ops, args.user, args.tier, args.mode == "burst")
    summary["mode"] = args.mode
    summary["base"] = args.base
    if args.mode == "public":
        summary["viewers"] = args.viewers
        summary["seconds"] = args.seconds
        summary["interval"] = args.interval
    else:
        summary["workers"] = args.workers
        summary["ops_per_worker"] = args.ops
    output = json.dumps(summary, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
