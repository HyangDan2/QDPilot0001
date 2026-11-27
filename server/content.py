"""Content and reward interfaces for Idle Ops (Phase 2E).

Design rules:
  * Rewards and costs are computed by the server from ``server.economy``;
    clients only submit action intents. This module never accepts amounts
    from a request.
  * New content ships behind a feature flag that defaults to off and is
    switched only through the ``IDLE_OPS_FEATURE_<NAME>`` environment.
  * Balance values live in ``server.economy``. ``balance_hash()`` fingerprints
    them so an unapproved balance edit is visible in review and QA; changing
    the balance requires PO approval, a ``BALANCE_VERSION`` bump and updated
    docs (``docs/ECONOMY_SPEC.md``).
"""

from __future__ import annotations

import hashlib
import json
import os

from . import economy

BALANCE_VERSION = 1
FEATURE_FLAGS = ("achievement_rewards", "daily_bonus")
REWARD_POLICY = "server-computed"


def _env_flag(name):
    raw = os.environ.get("IDLE_OPS_FEATURE_" + name.upper())
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def enabled(name):
    if name not in FEATURE_FLAGS:
        raise ValueError(f"unknown feature: {name}")
    return _env_flag(name)


def features():
    return {name: _env_flag(name) for name in FEATURE_FLAGS}


def _balance_payload():
    return {
        "tiers": economy.TIERS,
        "upgrades": economy.UPGRADES,
        "achievements": economy.ACHIEVEMENTS,
    }


def balance_hash():
    raw = json.dumps(_balance_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def overview():
    """Static, state-free description consumed by the admin console and QA."""
    return {
        "balanceVersion": BALANCE_VERSION,
        "balanceHash": balance_hash(),
        "features": features(),
        "rewardPolicy": REWARD_POLICY,
        "thresholds": {
            "reboot": economy.REBOOT_THRESHOLD,
            "levelStep": economy.LEVEL_STEP,
            "maxBuy": economy.MAX_BUY,
            "maxServersPerTier": economy.MAX_SERVERS_PER_TIER,
        },
        "tiers": [
            {"id": tier["id"], "name": tier["name"], "cost": tier["cost"],
             "growth": tier["growth"], "rps": tier["rps"], "heat": tier["heat"]}
            for tier in economy.TIERS
        ],
        "upgrades": [
            {"id": upgrade_id, "name": spec["name"], "cost": spec["cost"],
             "growth": spec["growth"], "max": spec["max"]}
            for upgrade_id, spec in economy.UPGRADES.items()
        ],
        "achievements": [
            {"id": item["id"], "name": item["name"], "desc": item["desc"],
             "bonus": item["bonus"], "metric": item["metric"], "at": item["at"]}
            for item in economy.ACHIEVEMENTS
        ],
    }
