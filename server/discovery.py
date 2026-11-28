"""Policy-gated discovery of public development repositories."""
import re

FAMILIES = ("agpilot", "pkpilot", "qdpilot", "lmpilot", "agws")
NAMESPACE = re.compile(r"^(agpilot|pkpilot|qdpilot|lmpilot|agws)(?=[0-9_-]|$)", re.ASCII | re.I)
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", re.ASCII)
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}", re.ASCII)
INTERVAL = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_discovered (
    repo INTEGER PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, default_branch TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS twin_discovery_state (
    policy TEXT PRIMARY KEY, last_attempt REAL NOT NULL
) WITHOUT ROWID;
"""
TABLES = {"twin_discovered": {"repo", "owner", "name", "default_branch"},
          "twin_discovery_state": {"policy", "last_attempt"}}


def identifier(value):
    return type(value) is int and 0 < value < 2**63


def policy(value):
    """An absent policy leaves the deployment's manual registry intact."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"owner", "families", "publish_public", "exclude_ids"}:
        raise ValueError("invalid discovery policy")
    owner = value.get("owner")
    families = value.get("families")
    publish = value.get("publish_public", False)
    exclude = value.get("exclude_ids", [])
    if (not isinstance(owner, str) or not OWNER.fullmatch(owner)
            or not isinstance(families, list) or not families or not all(
                isinstance(family, str) and family.lower() in FAMILIES for family in families
            )
            or type(publish) is not bool or not isinstance(exclude, list) or not all(map(identifier, exclude))):
        raise ValueError("invalid discovery policy")
    return {"owner": owner.lower(), "families": frozenset(family.lower() for family in families),
            "publish": publish, "exclude": frozenset(exclude)}


def policy_key(approved):
    # A policy change needs a fresh inventory even when the prior scan was recent.
    return "/".join((approved["owner"], ",".join(sorted(approved["families"])),
                     str(int(approved["publish"])), ",".join(map(str, sorted(approved["exclude"])))))


def matches(approved, repo, owner, name):
    if (not approved or not identifier(repo) or not isinstance(owner, str)
            or not isinstance(name, str) or owner.lower() != approved["owner"] or repo in approved["exclude"]):
        return False
    match = NAMESPACE.match(name)
    return bool(REPO.fullmatch(name) and match and match[1].lower() in approved["families"])


def public_repository(item):
    owner = item["owner"]
    return (
        owner["type"] == "User" and item["visibility"] == "public" and item["private"] is False
        and item["archived"] is False and item["fork"] is False and item["disabled"] is False
    )


def candidates(client, approved):
    result = {}
    for item in client.pages("/user/repos", params={"affiliation": "owner", "visibility": "public", "sort": "full_name"}):
        owner = item["owner"]
        name, repo = item["name"], item["id"]
        if not public_repository(item) or not matches(approved, repo, owner["login"], name):
            continue
        branch = item.get("default_branch")
        if not isinstance(branch, str) or not 0 < len(branch) <= 200:
            continue
        result[repo] = (owner["login"], name, branch)
    return result


def mark_attempt(service, approved, now):
    with service.transaction(write=True) as con:
        current = con.execute("SELECT last_attempt FROM twin_discovery_state WHERE policy=?",
                              (policy_key(approved),)).fetchone()
        if current and current[0] > now - INTERVAL:
            return False
        con.execute("INSERT OR REPLACE INTO twin_discovery_state VALUES(?,?)", (policy_key(approved), now))
        return True
