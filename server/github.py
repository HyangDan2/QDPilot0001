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

from . import discovery, twin

HOST = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGES = 8
MAX_REQUESTS = 240
RATE_RESERVE = 250
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


class ComparisonUnavailable(CollectionError):
    pass


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
            ref = payload.get("ref")
            if event != "push" or (isinstance(ref, str) and ref.startswith("refs/heads/")):
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
    def __init__(self, token, opener=None, quota=None):
        if not token:
            raise ValueError("a scoped GitHub read token is required")
        self.token = token
        self.opener = opener or build_opener(NoRedirect())
        self.requests = 0
        self.quota = quota if quota is not None else {}
        self.deadline = time.monotonic() + REQUEST_WINDOW

    def get(self, path, params=None):
        if path != "/user/repos" and not path.startswith("/repos/"):
            raise CollectionError("invalid GitHub request path")
        now = time.time()
        if self.quota.get("retry_at", 0) > now:
            raise Deferred(self.quota["retry_at"])
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
                remaining = response.headers.get("X-RateLimit-Remaining", "")
                reset = response.headers.get("X-RateLimit-Reset", "")
                if remaining.isdigit() and int(remaining) <= RATE_RESERVE and reset.isdigit():
                    self.quota["retry_at"] = max(now + 1, int(reset) + 1)
                    raise Deferred(self.quota["retry_at"])
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise CollectionError("GitHub response exceeded the size limit")
                return json.loads(raw), 'rel="next"' in response.headers.get("Link", "")
        except HTTPError as exc:
            rate_limited = exc.code == 429 or (
                exc.code == 403 and (exc.headers.get("X-RateLimit-Remaining") == "0"
                                     or bool(exc.headers.get("Retry-After")))
            )
            try:
                if exc.code == 403 and not rate_limited:
                    error = json.loads(exc.read(4096))
                    rate_limited = isinstance(error, dict) and "rate limit" in str(error.get("message", "")).lower()
            except (ValueError, OSError):
                pass
            finally:
                exc.close()
            if rate_limited:
                self.quota["retry_at"] = retry_time(exc.headers, time.time())
                raise Deferred(self.quota["retry_at"]) from None
            if exc.code == 404 and "/compare/" in path:
                raise ComparisonUnavailable("GitHub comparison base unavailable") from None
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


def branch_heads(client, path):
    """Use complete paginated branch snapshots from the verified repository."""
    result = {}
    for item in client.pages(path + "/branches"):
        ref = item.get("name")
        if not isinstance(ref, str) or not ref or ref in result:
            raise CollectionError("invalid or repeated GitHub branch; checkpoint preserved")
        result[ref] = sha(item.get("commit", {}).get("sha"))
    return result


def commit_observation(commit, baseline):
    identifier = sha(commit.get("sha"))
    parents = commit.get("parents")
    if not isinstance(parents, list):
        raise CollectionError("GitHub response is missing commit parents")
    authored = stamp(commit.get("commit", {}).get("committer", {}).get("date"))
    if authored <= baseline:
        return identifier, ("before_baseline", authored)
    return identifier, ("merge" if len(parents) > 1 else "awarded", authored)


def changed_commits(client, path, refs, previous, baseline, recover=False, window=(None, None)):
    observations = {}
    compared = set()
    scanned = set()
    # Round down so commits in the baseline second are checked locally too.
    lower, upper = window
    if upper is not None and upper <= baseline:
        return observations
    since = datetime.fromtimestamp(int(max(baseline, lower or baseline)), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def scan_tip(head):
        if head in scanned:
            return
        params = {"sha": head, "since": since}
        if upper is not None:
            params["until"] = datetime.fromtimestamp(upper, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in client.pages(path + "/commits", params=params):
            identifier, observation = commit_observation(item, baseline)
            at = observation[1]
            if (lower is None or at >= lower) and (upper is None or at <= upper):
                observations[identifier] = observation
        scanned.add(head)

    for ref, head in refs.items():
        old = previous.get(ref)
        if recover or not old:
            scan_tip(head)
            continue
        if old == head or (old, head) in compared:
            continue
        compared.add((old, head))
        compare = path + f"/compare/{old}...{head}"
        try:
            observed = dict(commit_observation(item, baseline)
                            for item in client.pages(compare, key="commits"))
            if head not in observed:
                verdict, _ = client.get(compare, {"per_page": 1, "page": 1})
                if verdict.get("status") not in ("behind", "identical"):
                    raise CollectionError("GitHub commit comparison was incomplete")
        except ComparisonUnavailable:
            # A deleted or force-replaced comparison base can disappear from
            # GitHub. A bounded walk from the immutable current tip can recover.
            scan_tip(head)
        else:
            observations.update(observed)
    return observations


def issue_kind(labels):
    names = {re.sub(r"\s*:\s*", ":", label["name"].strip().casefold()) for label in labels}
    if names & {"critical", "p0", "critical/p0", "priority:p0", "severity:critical"}:
        return "critical"
    if "regression" in names:
        return "regression"
    return "bug" if "bug" in names else "issue"


def issue_items(client, path, observed, now):
    params = {"sort": "updated", "direction": "desc"}
    if observed is None:
        # Include old open work as well as explicitly merged PRs in the recent window.
        yield from read_issues(client.pages(path + "/issues", params={**params, "state": "open"}))
    since = (now - 30 * 86400) if observed is None else (observed - 120)
    params.update(state="all", since=datetime.fromtimestamp(max(0, since), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"))
    yield from read_issues(client.pages(path + "/issues", params=params))


def read_issues(items):
    for item in items:
        identifier = item["id"]
        updated = stamp(item.get("updated_at"))
        pull = item.get("pull_request")
        if pull is not None:
            merged = pull.get("merged_at")
            if merged is not None:
                stamp(merged)
            yield (f"pull:{identifier}", "pull", "merged" if merged is not None else item["state"], updated, 0)
        else:
            status = "open" if item["state"] == "open" else (
                "resolved" if item.get("state_reason") == "completed" else "dismissed")
            yield (f"issue:{identifier}", issue_kind(item.get("labels", [])), status, updated, 0)


def ci_state(item):
    if item.get("status") == "in_progress":
        return "running"
    if item.get("status") == "completed":
        conclusion = item.get("conclusion")
        if conclusion == "success":
            return "resolved"
        if conclusion in FAILURES:
            return "open"
    return None


def ci_stamp(item, state):
    return stamp(item.get("started_at") if state == "running" else item.get("completed_at"))


def ci_items(client, path, refs):
    for ref, head in refs.items():
        for item in client.pages(path + f"/commits/{head}/check-runs", key="check_runs",
                                 params={"filter": "latest"}):
            state = ci_state(item)
            app_id = item.get("app", {}).get("id") if item.get("app") else None
            branch = (item.get("check_suite") or {}).get("head_branch")
            unambiguous = list(refs.values()).count(head) == 1 if branch is None else branch == ref
            if (state and app_id and unambiguous and item["app"].get("slug") != "github-actions"
                    and item.get("head_sha") == head):
                key = twin.opaque("check", ref, app_id, item["name"])
                yield (key, "ci", state, ci_stamp(item, state), item["id"])
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
            if item.get("status") not in ("in_progress", "completed"):
                continue
            state = ci_state(item)
            if state:
                yield (twin.opaque("workflow", ref, workflow), "ci", state,
                       stamp(item.get("updated_at")), item["id"])
            for job in client.pages(path + f"/actions/runs/{item['id']}/attempts/{item['run_attempt']}/jobs",
                                    key="jobs"):
                state = ci_state(job)
                if state:
                    yield (twin.opaque("job", ref, workflow, job["name"]), "ci", state,
                           ci_stamp(job, state), job["id"])


def collect(twin_store, repo, client, now=None, recover=False, window=(None, None)):
    """Fetch outside the transaction, then commit the entire observation atomically."""
    now = time.time() if now is None else now
    config = twin_store.projects[repo]
    path = repo_path(config["repo"])
    with twin_store.transaction() as con:
        prior = con.execute("SELECT * FROM twin_projects WHERE repo=?", (repo,)).fetchone()
        previous = dict(con.execute("SELECT ref,sha FROM twin_refs WHERE repo=?", (repo,)))
        activity = con.execute("SELECT * FROM twin_development WHERE repo=?", (repo,)).fetchone()
    if prior["retry_at"] > now:
        raise Deferred(prior["retry_at"])

    try:
        meta, _ = client.get(path)
        if meta.get("id") != repo or type(meta.get("private")) is not bool:
            raise RepositoryUnavailable("GitHub repository identity did not match the registry")
        automatic = config.get("automatic", False)
        if automatic and (not discovery.public_repository(meta) or not discovery.matches(
            twin_store.discovery_policy, repo, meta["owner"]["login"], meta["name"]
        )):
            raise RepositoryUnavailable("automatic repository no longer meets public approval policy")
        public = not meta["private"] and meta.get("visibility") == "public" and config["publish"]
        # Hide immediately after verified privacy changes, even when a later
        # ancillary GitHub request fails or hits a page cap.
        if not public and prior["public_verified"]:
            twin_store.hide(repo)
        refs = branch_heads(client, path)
        baseline = prior["baseline_at"]
        if recover and baseline is None:
            raise CollectionError("initialize the repository baseline before recovery")
        commits = changed_commits(client, path, refs, previous, baseline, recover, window) if baseline is not None else {}
        # Branch discovery covers repository-wide commit progress. Keep CI
        # observation within the original configured ref budget.
        ci_refs = {ref: refs[ref] for ref in config["refs"] if ref in refs}
        if not ci_refs and refs:
            ref = meta.get("default_branch")
            ref = ref if ref in refs else min(refs)
            ci_refs = {ref: refs[ref]}
        commit_at = activity["commit_at"] if activity else None
        if activity is None or refs != previous:
            dates = []
            for tip in set(ci_refs.values()):
                commit, _ = client.get(path + "/git/commits/" + tip)
                if commit.get("sha") != tip:
                    raise CollectionError("GitHub returned a different tracked commit")
                dates.append(stamp(commit.get("committer", {}).get("date")))
            commit_at = max(dates, default=None)

        commit_dates = [value[1] for value in commits.values()]
        commit_dates.extend(value for value in (commit_at, activity["commit_at"] if activity else None)
                            if value is not None)
        commit_at = max(commit_dates, default=None)

        issues = list(issue_items(client, path, prior["observed_at"] if activity else None, now))
        ci = {}
        for observation in ci_items(client, path, ci_refs):
            key = observation[0]
            if key not in ci or observation[3:] >= ci[key][3:]:
                ci[key] = observation
        ci_signals = [item for item in ci.values() if item[2] != "running"]
        ci_counts = {state: sum(item[2] == state for item in ci.values())
                     for state in ("running", "resolved", "open")}
        releases = [(f"release:{item['id']}", "release", "observed", stamp(item.get("published_at")), 0)
                    for item in client.pages(path + "/releases")
                    if not item.get("draft") and item.get("published_at")]
        # Ref updates during a collection invalidate the assembled observation.
        if refs != branch_heads(client, path):
            raise CollectionError("tracked refs changed during reconciliation; retry")
        final_meta, _ = client.get(path)
        if final_meta.get("id") != repo or final_meta.get("private") != meta["private"] or (
            final_meta.get("visibility") != meta.get("visibility")
        ) or (automatic and (
            not discovery.public_repository(final_meta) or not discovery.matches(
                twin_store.discovery_policy, repo, final_meta["owner"]["login"], final_meta["name"]
            )
        )):
            raise RepositoryUnavailable("repository visibility or identity changed during reconciliation")
    except Deferred as exc:
        with twin_store.transaction(write=True) as con:
            con.execute("UPDATE twin_projects SET retry_at=max(retry_at,?) WHERE repo=?", (exc.retry_at, repo))
        raise
    except RepositoryUnavailable:
        twin_store.hide(repo, remove_discovered=config.get("automatic", False))
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
        duplicates = 0
        if baseline is None:
            baseline = now
            commits = dict.fromkeys(refs.values(), ("baseline", None))
        for identifier, (reason, _) in commits.items():
            awarded = reason == "awarded"
            if not con.execute("INSERT OR IGNORE INTO twin_commits VALUES(?,?,?)",
                               (repo, identifier, points if awarded else 0)).rowcount:
                duplicates += 1
                continue
            con.execute("INSERT INTO twin_commit_observations VALUES(?,?,?,?)",
                        (repo, identifier, now, reason))
            if reason != "baseline":
                count += 1
            if awarded:
                xp += points
                earned += 1
        con.execute("""
            INSERT INTO twin_collector VALUES(?,?,NULL,?)
            ON CONFLICT(repo) DO UPDATE SET attempted_at=excluded.attempted_at,error=NULL,
                duplicates=excluded.duplicates
        """, (repo, now, duplicates))
        con.execute("""
            UPDATE twin_projects SET baseline_at=?, observed_at=?, xp=xp+?,run_commits=run_commits+?,
              commit_count=commit_count+?,served=?,retry_at=0,
              requested=CASE WHEN ? THEN max(requested,served+1) ELSE requested END
            WHERE repo=?
        """, (baseline, now, xp, earned, count, prior["served"] if recover else prior["requested"],
              recover, repo))
        if not recover:
            con.execute("DELETE FROM twin_refs WHERE repo=?", (repo,))
            con.executemany("INSERT INTO twin_refs VALUES(?,?,?)", ((repo, ref, value) for ref, value in refs.items()))
        for item in issues + ci_signals + releases:
            twin.signal(con, repo, item, now, activity is not None)
        observed_activity = [(commit_at, "commit")] if commit_at is not None else []
        observed_activity.extend((item[3], "issue" if item[1] in twin.BUG_CLASSES else item[1])
                                 for item in issues + list(ci.values()) + releases)
        if activity and activity["activity_at"] is not None:
            observed_activity.insert(0, (activity["activity_at"], activity["activity_kind"]))
        activity_at, activity_kind = max(observed_activity, key=lambda item: item[0], default=(None, None))
        con.execute("""
            INSERT INTO twin_development VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(repo) DO UPDATE SET commit_at=excluded.commit_at,
                activity_at=excluded.activity_at, activity_kind=excluded.activity_kind,
                ci_running=excluded.ci_running, ci_passed=excluded.ci_passed, ci_failed=excluded.ci_failed
        """, (repo, commit_at, activity_at, activity_kind, ci_counts["running"],
              ci_counts["resolved"], ci_counts["open"]))
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
