"""Bounded one-shot GitHub synchronization; run periodically under the service user."""
import argparse
import contextlib
import fcntl
import os
import sqlite3
import sys
import time

from . import github, r3_config
from .store import Store
from .twin import Twin

MIN_INTERVAL = 300
WORKER_WINDOW = 240


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("IDLE_OPS_DB", "./data/idle-ops.sqlite3"))
    parser.add_argument("--project", type=int, help="reconcile one allowlisted GitHub repository ID")
    parser.add_argument("--token-file", help="path to a read-only fine-grained GitHub credential")
    parser.add_argument("--recover", action="store_true", help="rescan reachable history without moving branch checkpoints")
    parser.add_argument("--since", help="recovery lower bound in UTC RFC3339 (inclusive)")
    parser.add_argument("--until", help="recovery upper bound in UTC RFC3339 (inclusive)")
    args = parser.parse_args(argv)
    if args.recover and args.project is None:
        parser.error("--recover requires an explicit --project")
    if (args.since or args.until) and not args.recover:
        parser.error("--since and --until require --recover")
    try:
        window = tuple(github.stamp(value) if value else None for value in (args.since, args.until))
    except github.CollectionError:
        parser.error("recovery bounds must use YYYY-MM-DDTHH:MM:SSZ")
    if all(value is not None for value in window) and window[0] > window[1]:
        parser.error("--since must be at or before --until")
    projects, _, _ = r3_config.load()
    if args.project is not None and args.project not in projects:
        parser.error("repository is outside the registry")
    if not projects:
        return 0
    token = os.environ.get("IDLE_OPS_GITHUB_TOKEN")
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as stream:
            token = stream.read().strip()
    if not token:
        parser.error("a scoped GitHub read token is required")

    result = 0
    store = Store(args.db, preserve_legacy=True)
    with contextlib.closing(os.fdopen(os.open(args.db + ".collector.lock", os.O_RDWR | os.O_CREAT, 0o600), "r+")) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        service = Twin(store, projects)
        now = time.time()
        with service.transaction() as con:
            due = [row["repo"] for row in con.execute(
                "SELECT * FROM twin_projects WHERE retry_at<=? ORDER BY coalesce(observed_at,0),repo", (now,))
                if row["repo"] in projects and (args.project == row["repo"] or (
                    args.project is None and (row["requested"] > row["served"] or row["observed_at"] is None
                                              or row["observed_at"] < now - MIN_INTERVAL)))]
        if args.project is not None and not due:
            print(f"{args.project}: reconciliation is in backoff", file=sys.stderr)
            return 1
        deadline = time.monotonic() + WORKER_WINDOW
        for repo in due:
            if time.monotonic() >= deadline:
                break
            try:
                summary = github.collect(service, repo, github.GitHub(token), recover=args.recover, window=window)
                print(repo, summary)
            except github.Deferred as exc:
                service.record_collection_failure(repo, str(exc), time.time())
                print(f"{repo}: reconciliation deferred", file=sys.stderr)
                if args.project is not None:
                    result = 1
                # A single credential serves this invocation. Stop also on
                # the other repositories until the shared backoff expires.
                with service.transaction(write=True) as con:
                    retry = con.execute("SELECT retry_at FROM twin_projects WHERE repo=?", (repo,)).fetchone()[0]
                    con.executemany("UPDATE twin_projects SET retry_at=max(retry_at,?) WHERE repo=?",
                                    ((retry, repo_id) for repo_id in projects))
                break
            except (github.CollectionError, KeyError, TypeError, AttributeError, ValueError, sqlite3.Error) as exc:
                detail = str(exc) if isinstance(exc, github.CollectionError) else "invalid source or storage response"
                try:
                    service.record_collection_failure(repo, detail, time.time())
                except sqlite3.Error:
                    pass
                print(f"{repo}: reconciliation failed: {detail}", file=sys.stderr)
                result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
