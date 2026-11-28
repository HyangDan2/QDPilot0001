"""Load the deployment registry and verifier credentials outside the web root."""
import json
import os

from . import discovery, telemetry, twin


def load_deployment():
    config = {}
    path = os.environ.get("IDLE_OPS_CONFIG")
    if path:
        with open(path, encoding="utf-8") as stream:
            config = json.load(stream)
        if not isinstance(config, dict) or set(config) - {"repositories", "telemetry_tokens", "webhook_secret", "discovery"}:
            raise ValueError("invalid Idle Ops configuration")
    projects = twin.registry(os.environ.get("IDLE_OPS_REPOSITORIES", json.dumps(config.get("repositories", []))))
    tokens = telemetry.load_tokens(os.environ.get(
        "IDLE_OPS_TELEMETRY_TOKENS", json.dumps(config.get("telemetry_tokens", {}))))
    secret = os.environ.get("IDLE_OPS_GITHUB_WEBHOOK_SECRET", config.get("webhook_secret"))
    if secret is not None and (not isinstance(secret, str) or len(secret) < 32):
        raise ValueError("webhook secret must have at least 32 characters")
    return projects, tokens, secret, discovery.policy(config.get("discovery"))


def load():
    return load_deployment()[:3]
