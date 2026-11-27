"""GitHub REST collector and authenticated webhook wakeups."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import twin

HOST = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGES = 8
MAX_REQUESTS = 240
REQUEST_WINDOW = 90
MAX_RESPONSE = 8 * 1024 * 1024
DELIVERY_TTL = 30 * 86400
COMMIT = re.compile(r"[0-9a-f]{40}")
DELIVERY = re.compile(r"[A-Za-z0-9-]{8,80}", re.ASCII)
HOOK_EVENTS = {"push", "issues", "pull_request", "release", "check_run", "check_suite",
               "workflow_job", "workflow_run", "ping"}
FAILURES = {"failure", "timed_out", "action_required", "startup_failure", "stale"}


class CollectionError(ValueError):
    pass


class Deferred(CollectionError):
    def __init__(self, retry_at):
        super().__init__("GitHub requested retry backoff")
        self.retry_at = retry_at


class RepositoryUnavailable(CollectionError):
    pass


class AccessDenied(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def hook(twin_store, raw, event, delivery, signature, secret, now=None):
    if not secret or not signature:
        raise AccessDenied("webhook authentication required")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8")):
        raise AccessDenied("webhook authentication required")
    if not DELIVERY.fullmatch(delivery or "") or event not in HOOK_EVENTS:
        raise ValueError("invalid GitHub delivery metadata")
    if event == "ping":
        return {"accepted": True, "duplicate": False}
    payload = json.loads(raw)
    repository = payload.get("repository") if isinstance(payload, dict) else None
    repo = repository.get("id") if isinstance(repository, dict) else None
    if type(repo) is not int or repo not in twin_store.projects:
        raise AccessDenied("repository is outside the registry")
    now = time.time() if now is None else now
    with twin_store.transaction(write=True) as con:
        con.execute("DELETE FROM twin_deliveries WHERE received<?", (now - DELIVERY_TTL,))
        inserted = con.execute("INSERT OR IGNORE INTO twin_deliveries VALUES(?,?,?)", (delivery, repo, now))
        if inserted.rowcount:
            if event != "push" or payload.get("ref") in {"refs/heads/" + ref for ref in
                                                         twin_store.projects[repo]["refs"]}:
                con.execute("UPDATE twin_projects SET requested=requested+1 WHERE repo=?", (repo,))
        return {"accepted": True, "duplicate": not bool(inserted.rowcount)}


def stamp(value):
    if not isinstance(value, str):
        raise CollectionError("GitHub response is missing a source timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError as exc:
        raise CollectionError("invalid GitHub source timestamp") from exc


def sha(value):
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        raise CollectionError("invalid GitHub commit identifier")
    return value


def retry_time(headers, now):
    delay = headers.get("Retry-After")
    if delay:
        try:
            return now + max(1, int(delay))
        except ValueError:
            try:
                return max(now + 1, parsedate_to_datetime(delay).timestamp())
            except (TypeError, ValueError):
                pass
    reset = headers.get("X-RateLimit-Reset")
    if headers.get("X-RateLimit-Remaining") == "0" and reset and reset.isdigit():
        return max(now + 1, int(reset) + 1)
    return now + 60


class GitHub:
    def __init__(self, token, opener=None):
        if not token:
            raise ValueError("a scoped GitHub read token is required")
        self.token = token
        self.opener = opener or build_opener(NoRedirect())
        self.requests = 0
        self.deadline = time.monotonic() + REQUEST_WINDOW

    def get(self, path, params=None):
        if not path.startswith("/repos/"):
            raise CollectionError("invalid GitHub request path")
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise CollectionError("GitHub request budget exceeded")
        until = self.deadline - time.monotonic()
        if until <= 0:
            raise CollectionError("GitHub observation exceeded its time budget")
        url = HOST + path + ("?" + urlencode(params) if params else "")
        request = Request(url, headers={"Accept": "application/vnd.github+json",
                          "Authorization": "Bearer " + self.token,
                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "HD2Lab-Idle-Ops"})
        try:
            with self.opener.open(request, timeout=min(10, until)) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise CollectionError("GitHub response exceeded the size limit")
                return json.loads(raw), 'rel="next"' in response.headers.get("Link", "")
        except HTTPError as exc:
            exc.close()
            if exc.code == 429 or exc.code == 403:
                raise Deferred(retry_time(exc.headers, time.time())) from None
            if exc.code == 404 and path.count("/") == 3:
                raise RepositoryUnavailable("registered repository unavailable") from None
            raise CollectionError(f"GitHub returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            raise CollectionError("GitHub request failed") from None

    def pages(self, path, key=None, params=None):
        for page in range(1, MAX_PAGES + 1):
            payload, more = self.get(path, {**(params or {}), "page": page, "per_page": PAGE_SIZE})
            items = payload if key is None else payload.get(key) if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise CollectionError("invalid GitHub paginated response")
            yield from items
            if not more:
                return
        raise CollectionError("GitHub pagination limit exceeded; checkpoint preserved")


def repo_path(name):
    return "/repos/" + "/".join(quote(part, safe="") for part in name.split("/"))


def branch_heads(client, path, refs):
    result = {}
    for ref in refs:
        payload, _ = client.get(path + "/git/ref/heads/" + quote(ref, safe="/"))
        if payload.get("ref") != "refs/heads/" + ref:
            raise CollectionError("GitHub returned a different tracked ref")
        result[ref] = sha(payload.get("object", {}).get("sha"))
    return result


def changed_commits(client, path, refs, previous, baseline):
    commits = {}
    for ref, head in refs.items():
        old = previous.get(ref)
        if not old or old == head:
            continue
        seen_head = False
        for commit in client.pages(path + f"/compare/{old}...{head}", key="commits"):
            identifier = sha(commit.get("sha"))
            seen_head = seen_head or identifier == head
            parents = commit.get("parents")
            if not isinstance(parents, list):
                raise CollectionError("GitHub response is missing commit parents")
            authored = stamp(commit.get("commit", {}).get("committer", {}).get("date"))
            # Old work newly cherry-picked has a new SHA and committer date.
            # Old ancestry brought in by a later branch/merge does not pay.
            commits[identifier] = len(parents) <= 1 and authored > baseline
        if not seen_head:
            # A rewind may legitimately have zero newly reachable commits.
            verdict, _ = client.get(path + f"/compare/{old}...{head}", {"per_page": 1, "page": 1})
            if verdict.get("status") not in ("behind", "identical"):
                raise CollectionError("GitHub commit comparison was incomplete")
    return commits


def issue_items(client, path, observed):
    params = {"state": "all", "sort": "updated", "direction": "desc"}
    if observed is None:
        params["state"] = "open"
    else:
        params["since"] = datetime.fromtimestamp(max(0, observed - 120), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    for item in client.pages(path + "/issues", params=params):
        identifier = item["id"]
        updated = stamp(item.get("updated_at"))
        if "pull_request" in item:
            yield (f"pull:{identifier}", "pull", item["state"], updated, 0)
        else:
            status = "open" if item.get("state") == "open" else (
                "resolved" if item.get("state_reason") == "completed" else "dismissed")
            # Reconciliation reads the current authoritative issue record, so
            # a current reopen can supersede a close in the same second.
            yield (f"issue:{identifier}", "issue", status, updated, 0)


def ci_state(conclusion):
    if conclusion == "success":
        return "resolved"
    if conclusion in FAILURES:
        return "open"
    return None


def ci_items(client, path, refs):
    for ref, head in refs.items():
        for item in client.pages(path + f"/commits/{head}/check-runs", key="check_runs",
                                 params={"filter": "latest"}):
            state = ci_state(item.get("conclusion"))
            app_id = item.get("app", {}).get("id") if item.get("app") else None
            branch = (item.get("check_suite") or {}).get("head_branch")
            unambiguous = list(refs.values()).count(head) == 1 if branch is None else branch == ref
            if (state and app_id and unambiguous and item["app"].get("slug") != "github-actions"
                    and item.get("head_sha") == head):
                key = twin.opaque("check", ref, app_id, item["name"])
                yield (key, "ci", state, stamp(item.get("completed_at")), item["id"])
        workflows = {}
        for item in client.pages(path + "/actions/runs", key="workflow_runs",
                                 params={"branch": ref, "head_sha": head, "event": "push"}):
            if item.get("head_sha") != head or item.get("head_branch") != ref:
                continue
            if not item.get("head_repository") or item["head_repository"]["full_name"].casefold() != path[7:].casefold():
                continue
            previous = workflows.get(item["workflow_id"])
            if previous is None or (item["run_number"], item["run_attempt"]) > (
                previous["run_number"], previous["run_attempt"]
            ):
                workflows[item["workflow_id"]] = item
        for workflow, item in workflows.items():
            if item.get("status") != "completed":
                continue
            state = ci_state(item.get("conclusion"))
            if state:
                yield (twin.opaque("workflow", ref, workflow), "ci", state,
                       stamp(item.get("updated_at")), item["id"])
            for job in client.pages(path + f"/actions/runs/{item['id']}/attempts/{item['run_attempt']}/jobs",
                                    key="jobs"):
                state = ci_state(job.get("conclusion"))
                if state and job.get("status") == "completed":
                    yield (twin.opaque("job", ref, workflow, job["name"]), "ci", state,
                           stamp(job.get("completed_at")), job["id"])


def collect(twin_store, repo, client, now=None):
    """Fetch outside the transaction, then commit the entire observation atomically."""
    now = time.time() if now is None else now
    config = twin_store.projects[repo]
    path = repo_path(config["repo"])
    with twin_store.transaction() as con:
        prior = con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()
        previous = dict(con.execute("SELECT ref,sha FROM twin_refs WHERE repo=?", (repo,)))
    if prior["retry_at"] > now:
        raise Deferred(prior["retry_at"])

    try:
        meta, _ = client.get(path)
        if meta.get("id") != repo or type(meta.get("private")) is not bool:
            raise RepositoryUnavailable("GitHub repository identity did not match the registry")
        public = not meta["private"] and meta.get("visibility") == "public" and config["publish"]
        # Hide immediately after verified privacy changes, even when a later
        # ancillary GitHub request fails or hits a page cap.
        if not public and prior["public_verified"]:
            twin_store.hide(repo)
        refs = branch_heads(client, path, config["refs"])
        baseline = prior["baseline_at"]
        commits = changed_commits(client, path, refs, previous, baseline) if baseline is not None else {}
        issues = list(issue_items(client, path, prior["observed_at"]))
        ci = list(ci_items(client, path, refs))
        releases = [(f"release:{item['id']}", "release", "observed", stamp(item.get("published_at")), 0)
                    for item in client.pages(path + "/releases")
                    if not item.get("draft") and item.get("published_at")]
        # Ref updates during a collection invalidate the assembled observation.
        if refs != branch_heads(client, path, config["refs"]):
            raise CollectionError("tracked refs changed during reconciliation; retry")
        final_meta, _ = client.get(path)
        if final_meta.get("id") != repo or final_meta.get("private") != meta["private"] or (
            final_meta.get("visibility") != meta.get("visibility")
        ):
            raise RepositoryUnavailable("repository visibility or identity changed during reconciliation")
    except Deferred as exc:
        with twin_store.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET retry_at=max(retry_at,?) WHERE repo=?", (exc.retry_at, repo))
        raise
    except RepositoryUnavailable:
        twin_store.hide(repo)
        raise

    with twin_store.transaction(write=True) as con:
        current = con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()
        if (current["observed_at"], current["served"]) != (prior["observed_at"], prior["served"]):
            raise CollectionError("a newer reconciliation already won")
        con.execute("UPDATE twin_projects SET public_verified=? WHERE repo=?", (public, repo))
        xp = 0
        earned = 0
        count = 0
        points = twin.XP_PER_COMMIT * twin.bonus(current["reboots"]) // 10000
        for identifier, eligible in commits.items():
            if con.execute("INSERT OR IGNORE INTO twin_commits VALUES(?,?,?)",
                           (repo, identifier, points if eligible else 0)).rowcount:
                count += 1
                if eligible:
                    xp += points
                    earned += 1
        if baseline is None:
            baseline = now
        # A seeded branch tip is already known repository history, including
        # when it is later pushed onto another branch with a future commit date.
        con.executemany("INSERT OR IGNORE INTO twin_commits VALUES(?,?,0)",
                        ((repo, head) for ref, head in refs.items() if ref not in previous))
        con.execute("""
            UPDATE twin_projects SET baseline_at=?, observed_at=?, xp=xp+?,run_commits=run_commits+?,
              commit_count=commit_count+?,served=?,retry_at=0
            WHERE repo=?
        """, (baseline, now, xp, earned, count, prior["requested"], repo))
        con.execute("DELETE FROM twin_refs WHERE repo=?", (repo,))
        con.executemany("INSERT INTO twin_refs VALUES(?,?,?)", ((repo, ref, value) for ref, value in refs.items()))
        for item in issues + ci + releases:
            twin.signal(con, repo, item, now, prior["observed_at"] is not None)
        if count:
            twin.emit(con, repo, now, "commit")
        if earned:
            old_level, old_tier = twin.level_tier(current["xp"])
            level, tier = twin.level_tier(current["xp"] + xp)
            if tier != old_tier:
                twin.emit(con, repo, now, "install", level=level, tier=tier)
            if level != old_level:
                twin.emit(con, repo, now, "levelup", level=level, tier=tier)
        twin.revise(con, public)
        return {"commits": count, "eligible": earned, "xp": xp}
