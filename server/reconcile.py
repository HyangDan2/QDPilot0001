"""Bounded one-shot GitHub synchronization; run periodically under the service user."""
import argparse
import contextlib
import fcntl
import os
import sqlite3
import sys
import time

from . import discovery, github, r3_config
from .store import Store
from .twin import Twin

MIN_INTERVAL = 300
WORKER_WINDOW = 240
BATCH_PROJECTS = 4
BATCH_REQUEST_TARGET = 48
FAILURE_RETRY_SECONDS = 300


def defer_projects(service, until):
    projects = service.projects
    with service.transaction(write=True) as con:
        con.executemany("UPDATE twin_projects SET retry_at=max(retry_at,?) WHERE repo=?",
                        ((until, repo) for repo in projects))


def due_repositories(con, projects, only, now):
    rows = con.execute("""
        SELECT p.* FROM twin_projects p LEFT JOIN twin_collector c ON p.repo=c.repo
        WHERE p.retry_at<=?
        ORDER BY coalesce(c.attempted_at,p.observed_at,0),p.repo
    """, (now,))
    return [row["repo"] for row in rows
            if row["repo"] in projects and (only == row["repo"] or (
                only is None and (row["requested"] > row["served"] or row["observed_at"] is None
                                  or row["observed_at"] < now - MIN_INTERVAL)))]


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
    projects, _, _, policy = r3_config.load_deployment()
    if not projects and not policy:
        if args.project is not None:
            parser.error("repository is outside the registry")
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
        service = Twin(store, projects, discovery_policy=policy)
        now = time.time()
        deadline = time.monotonic() + WORKER_WINDOW
        quota = {}
        requests = 0
        if args.project is None and policy and discovery.mark_attempt(service, policy, now):
            client = github.GitHub(token, quota=quota)
            try:
                found = discovery.candidates(client, policy)
                print(f"discovery: {len(found)} matching public repositories, "
                      f"{service.register_discovered(found)} automatic registrations")
            except github.Deferred as exc:
                defer_projects(service, exc.retry_at)
                print("discovery deferred by GitHub request budget", file=sys.stderr)
                return 0
            except (github.CollectionError, KeyError, TypeError, AttributeError, ValueError) as exc:
                detail = str(exc) if isinstance(exc, github.CollectionError) else "invalid repository inventory"
                print(f"discovery failed: {detail}", file=sys.stderr)
                result = 1
            requests = client.requests
        projects = service.projects
        if args.project is not None and args.project not in projects:
            parser.error("repository is outside the registry")
        with service.transaction() as con:
            due = due_repositories(con, projects, args.project, now)
        if args.project is not None and not due:
            print(f"{args.project}: reconciliation is in backoff", file=sys.stderr)
            return 1
        attempts = 0
        for repo in due[:BATCH_PROJECTS]:
            if time.monotonic() >= deadline or requests >= BATCH_REQUEST_TARGET:
                break
            attempts += 1
            client = github.GitHub(token, quota=quota)
            try:
                summary = github.collect(service, repo, client, recover=args.recover, window=window)
                print(repo, summary)
            except github.Deferred as exc:
                service.record_collection_failure(repo, str(exc), time.time())
                print(f"{repo}: reconciliation deferred", file=sys.stderr)
                if args.project is not None:
                    result = 1
                # A single credential serves this invocation. Stop also on
                # the other repositories until the shared backoff expires.
                defer_projects(service, exc.retry_at)
                break
            except (github.CollectionError, KeyError, TypeError, AttributeError, ValueError, sqlite3.Error) as exc:
                detail = str(exc) if isinstance(exc, github.CollectionError) else "invalid source or storage response"
                try:
                    attempted = time.time()
                    service.record_collection_failure(repo, detail, attempted)
                    with service.transaction(write=True) as con:
                        con.execute("UPDATE twin_projects SET retry_at=max(retry_at,?) WHERE repo=?",
                                    (attempted + FAILURE_RETRY_SECONDS, repo))
                except sqlite3.Error:
                    pass
                print(f"{repo}: reconciliation failed: {detail}", file=sys.stderr)
                result = 1
            finally:
                requests += client.requests
        if requests or attempts:
            print(f"batch: {attempts} repository attempts, {requests} GitHub REST requests")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
