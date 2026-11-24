"""Server-authoritative economy for HD2Lab Idle Ops.

All progression is computed here. Callers never supply costs, rates, or
currencies; they only submit action intents that are validated against this
module. Time is passed in explicitly so simulations are deterministic.
"""

from __future__ import annotations

import math

SAVE_VERSION = 1
SEED_REQUESTS = 25.0
REBOOT_THRESHOLD = 1_000_000.0
# Safety guard against float overflow only. There is no balance cap: absence is
# simulated for the full elapsed time up to this bound (100 years).
MAX_SIM_SECONDS = 100 * 365 * 86400.0
MAX_BUY = 1000

TIERS = [
    {"id": "cache", "name": "Cache Node", "cost": 15, "growth": 1.15, "rps": 0.5, "heat": 0.4,
     "desc": "저전력 요청 캐시. 싸고 조용하지만 느립니다."},
    {"id": "app", "name": "App Server", "cost": 150, "growth": 1.15, "rps": 5.0, "heat": 1.5,
     "desc": "애플리케이션 계층. 트래픽 급증을 흡수합니다."},
    {"id": "db", "name": "Database Node", "cost": 2_000, "growth": 1.15, "rps": 45.0, "heat": 5.0,
     "desc": "영구 저장소. 뜨겁지만 처리량의 척추입니다."},
    {"id": "gpu", "name": "GPU Node", "cost": 25_000, "growth": 1.15, "rps": 400.0, "heat": 14.0,
     "desc": "벡터 검색과 모델 연산. 빠르지만 전력을 많이 씁니다."},
    {"id": "quantum", "name": "Quantum Rack", "cost": 400_000, "growth": 1.15, "rps": 4_000.0, "heat": 40.0,
     "desc": "실험적 랙. 강력한 냉각이 필요합니다."},
]

UPGRADES = {
    "network": {"name": "Network Fabric", "cost": 300, "growth": 2.4, "max": 15,
                "desc": "레벨당 전체 RPS +12%."},
    "cooling": {"name": "Cooling Loop", "cost": 200, "growth": 2.2, "max": 15,
                "desc": "레벨당 냉각 용량 +6."},
    "balancer": {"name": "Load Balancer", "cost": 1_500, "growth": 2.8, "max": 10,
                 "desc": "레벨당 전체 RPS +8%."},
    "overclock": {"name": "Overclock", "cost": 5_000, "growth": 3.0, "max": 5,
                  "desc": "레벨당 RPS +30%, 발열 +25%."},
}

ACHIEVEMENTS = [
    {"id": "first_server", "name": "첫 랙 설치", "desc": "서버를 1대 보유합니다.", "bonus": 0.02,
     "metric": "servers", "at": 1},
    {"id": "ten_servers", "name": "작은 클러스터", "desc": "서버 10대를 동시에 보유합니다.", "bonus": 0.02,
     "metric": "servers", "at": 10},
    {"id": "rps_100", "name": "워밍 캐시", "desc": "기본 RPS 100을 달성합니다.", "bonus": 0.03,
     "metric": "base_rps", "at": 100},
    {"id": "rps_10k", "name": "트래픽 폭주", "desc": "기본 RPS 10,000을 달성합니다.", "bonus": 0.03,
     "metric": "base_rps", "at": 10_000},
    {"id": "lifetime_1m", "name": "백만 요청", "desc": "누적 100만 요청을 처리합니다.", "bonus": 0.04,
     "metric": "total_lifetime", "at": 1_000_000},
    {"id": "level_10", "name": "시니어 오퍼레이터", "desc": "운영 레벨 10에 도달합니다.", "bonus": 0.03,
     "metric": "level", "at": 10},
    {"id": "first_reboot", "name": "클린 재시작", "desc": "첫 Reboot를 완료합니다.", "bonus": 0.05,
     "metric": "reboots", "at": 1},
    {"id": "tokens_10", "name": "업타임 수집가", "desc": "가동 토큰 10개를 보유합니다.", "bonus": 0.05,
     "metric": "tokens", "at": 10},
]

BASE_COOLING = 10.0
COOLING_PER_LEVEL = 6.0
LEVEL_STEP = 10_000.0
# Settlement is segmented at production-changing thresholds (levels and
# lifetime achievements), so polling frequency cannot change the outcome.
THRESHOLD_EPSILON = 1e-6
MAX_SETTLE_SEGMENTS = 100_000


def _tier(tier_id):
    for tier in TIERS:
        if tier["id"] == tier_id:
            return tier
    raise ValueError(f"unknown tier: {tier_id}")


def _upgrade(upgrade_id):
    try:
        return UPGRADES[upgrade_id]
    except KeyError as exc:
        raise ValueError(f"unknown upgrade: {upgrade_id}") from exc


def _num(value, floor=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("corrupt numeric state") from exc
    if not math.isfinite(number) or number < floor:
        raise ValueError("corrupt numeric state")
    return number


def initial_state(now):
    return {
        "version": SAVE_VERSION,
        "requests": SEED_REQUESTS,
        "run_lifetime": 0.0,
        "total_lifetime": 0.0,
        "tokens": 0,
        "reboots": 0,
        "servers": {"cache": 1},
        "upgrades": {},
        "achievements": [],
        "created_at": float(now),
        "last_sim_at": float(now),
        "last_seen": float(now),
    }


def normalize_state(raw):
    """Validate a stored state and fill defaults. Raises ValueError if corrupt."""
    if not isinstance(raw, dict):
        raise ValueError("state must be an object")
    version = int(raw.get("version", 0))
    if version != SAVE_VERSION:
        raise ValueError(f"unsupported state version: {version}")
    state = {
        "version": SAVE_VERSION,
        "requests": _num(raw.get("requests", 0)),
        "run_lifetime": _num(raw.get("run_lifetime", 0)),
        "total_lifetime": _num(raw.get("total_lifetime", 0)),
        "tokens": int(_num(raw.get("tokens", 0))),
        "reboots": int(_num(raw.get("reboots", 0))),
        "servers": {},
        "upgrades": {},
        "achievements": [],
        "created_at": _num(raw.get("created_at", 0)),
        "last_sim_at": _num(raw.get("last_sim_at", 0)),
        "last_seen": _num(raw.get("last_seen", 0)),
    }
    servers = raw.get("servers") or {}
    if not isinstance(servers, dict):
        raise ValueError("servers must be an object")
    for tier in TIERS:
        count = servers.get(tier["id"], 0)
        count = int(_num(count))
        if count < 0 or count > 1_000_000:
            raise ValueError("server count out of range")
        if count:
            state["servers"][tier["id"]] = count
    upgrades = raw.get("upgrades") or {}
    if not isinstance(upgrades, dict):
        raise ValueError("upgrades must be an object")
    for upgrade_id, spec in UPGRADES.items():
        level = int(_num(upgrades.get(upgrade_id, 0)))
        if level < 0 or level > spec["max"]:
            raise ValueError("upgrade level out of range")
        if level:
            state["upgrades"][upgrade_id] = level
    unlocked = raw.get("achievements") or []
    if not isinstance(unlocked, list):
        raise ValueError("achievements must be a list")
    known = {item["id"] for item in ACHIEVEMENTS}
    state["achievements"] = [item for item in unlocked if isinstance(item, str) and item in known]
    return state


def total_servers(state):
    return sum(state["servers"].values())


def server_cost(tier_id, owned):
    tier = _tier(tier_id)
    return int(math.ceil(tier["cost"] * (tier["growth"] ** int(owned))))


def bulk_cost(tier_id, owned, qty):
    return sum(server_cost(tier_id, owned + index) for index in range(int(qty)))


def max_affordable(tier_id, owned, budget, cap=MAX_BUY):
    total = 0
    cost = 0
    while total < cap:
        next_cost = server_cost(tier_id, owned + total)
        if cost + next_cost > budget:
            break
        cost += next_cost
        total += 1
    return total, cost


def upgrade_cost(upgrade_id, level):
    spec = _upgrade(upgrade_id)
    return int(math.ceil(spec["cost"] * (spec["growth"] ** int(level))))


def level_for(total_lifetime):
    return int(math.floor(math.sqrt(max(0.0, float(total_lifetime)) / LEVEL_STEP))) + 1


def achievement_bonus(state):
    unlocked = set(state["achievements"])
    return sum(item["bonus"] for item in ACHIEVEMENTS if item["id"] in unlocked)


def heat_load(state):
    raw = sum(state["servers"].get(tier["id"], 0) * tier["heat"] for tier in TIERS)
    overclock = state["upgrades"].get("overclock", 0)
    return raw * (1.0 + 0.25 * overclock)


def cooling_capacity(state):
    return BASE_COOLING + COOLING_PER_LEVEL * state["upgrades"].get("cooling", 0)


def thermal_efficiency(state):
    heat = heat_load(state)
    if heat <= 0:
        return 1.0
    capacity = cooling_capacity(state)
    if heat <= capacity:
        return 1.0
    return max(0.25, capacity / heat)


def base_rps(state):
    return sum(state["servers"].get(tier["id"], 0) * tier["rps"] for tier in TIERS)


def rps(state):
    level = level_for(state["total_lifetime"])
    global_multiplier = 1.0 + achievement_bonus(state) + 0.01 * (level - 1)
    upgrade_multiplier = (
        (1.0 + 0.12 * state["upgrades"].get("network", 0))
        * (1.0 + 0.08 * state["upgrades"].get("balancer", 0))
        * (1.0 + 0.30 * state["upgrades"].get("overclock", 0))
    )
    token_multiplier = 1.0 + 0.02 * state["tokens"]
    return base_rps(state) * global_multiplier * upgrade_multiplier * token_multiplier * thermal_efficiency(state)


def derived(state):
    level = level_for(state["total_lifetime"])
    current_floor = ((level - 1) ** 2) * LEVEL_STEP
    next_level = (level ** 2) * LEVEL_STEP
    span = next_level - current_floor
    heat = heat_load(state)
    capacity = cooling_capacity(state)
    return {
        "level": level,
        "level_bonus": 0.01 * (level - 1),
        "level_floor": current_floor,
        "next_level_at": next_level,
        "level_progress": min(1.0, max(0.0, (state["total_lifetime"] - current_floor) / span)) if span else 1.0,
        "rps": rps(state),
        "base_rps": base_rps(state),
        "heat": heat,
        "cooling": capacity,
        "efficiency": thermal_efficiency(state),
        "thermal_pct": min(1.0, heat / capacity) if capacity else 0.0,
        "total_servers": total_servers(state),
        "achievement_bonus": achievement_bonus(state),
        "token_multiplier": 1.0 + 0.02 * state["tokens"],
    }


def prestige_tokens(run_lifetime):
    if run_lifetime < REBOOT_THRESHOLD:
        return 0
    return int(math.floor(math.sqrt(run_lifetime / REBOOT_THRESHOLD)))


def check_achievements(state, info=None):
    unlocked = set(state["achievements"])
    gained = []
    info = info or derived(state)
    metrics = {
        "servers": total_servers(state),
        "base_rps": info["base_rps"],
        "total_lifetime": state["total_lifetime"],
        "level": info["level"],
        "reboots": state["reboots"],
        "tokens": state["tokens"],
    }
    for item in ACHIEVEMENTS:
        if item["id"] in unlocked:
            continue
        if metrics[item["metric"]] >= item["at"]:
            state["achievements"].append(item["id"])
            unlocked.add(item["id"])
            gained.append(item["id"])
    return gained


def _next_level_total(state):
    level = level_for(state["total_lifetime"])
    return float(level * level) * LEVEL_STEP


def _next_production_threshold(state):
    """Earliest lifetime total where RPS can change, or None."""
    unlocked = set(state["achievements"])
    boundary = _next_level_total(state)
    for item in ACHIEVEMENTS:
        if item["id"] in unlocked:
            continue
        if item["metric"] == "total_lifetime":
            boundary = min(boundary, float(item["at"]))
        elif item["metric"] == "level":
            boundary = min(boundary, float((item["at"] - 1) ** 2) * LEVEL_STEP)
    return boundary


def advance(state, now):
    """Apply elapsed time since the last simulation. No absence cap above the guard.

    The interval is settled in segments bounded by level/achievement thresholds,
    so the result depends on the starting state and elapsed time, not on how many
    times the world was polled in between.
    """
    raw_elapsed = float(now) - state["last_sim_at"]
    elapsed = max(0.0, min(raw_elapsed, MAX_SIM_SECONDS))
    level_before = level_for(state["total_lifetime"])
    gained = []

    def unlock():
        for achievement_id in check_achievements(state):
            if achievement_id not in gained:
                gained.append(achievement_id)

    # Achievements already satisfied by the starting state apply from the start
    # of the interval, matching an admin action that unlocked them immediately.
    unlock()
    earned = 0.0
    remaining = elapsed
    segments = 0
    while remaining > 0.0 and segments < MAX_SETTLE_SEGMENTS:
        segments += 1
        rate = rps(state)
        if not math.isfinite(rate) or rate < 0.0:
            rate = 0.0
        boundary = _next_production_threshold(state)
        dt = remaining
        if boundary is not None:
            if rate <= 0.0:
                dt = 0.0
            else:
                dt = min(dt, max(0.0, (boundary - state["total_lifetime"]) / rate))
        if dt <= 0.0:
            before = len(state["achievements"])
            unlock()
            if len(state["achievements"]) == before:
                # Defensive: never spin on a stale threshold.
                break
            continue
        chunk = rate * dt
        if not math.isfinite(chunk):
            chunk = 0.0
        earned += chunk
        state["requests"] += chunk
        state["run_lifetime"] += chunk
        state["total_lifetime"] += chunk
        remaining -= dt
        if boundary is not None and abs(state["total_lifetime"] - boundary) <= THRESHOLD_EPSILON:
            state["total_lifetime"] = boundary
        unlock()
    if remaining > 0.0:
        # Extreme guard only (100y at absurd rates): settle the remainder at the
        # current rate rather than burning unbounded CPU. Normal worlds never hit this.
        rate = rps(state)
        if math.isfinite(rate) and rate > 0.0:
            chunk = rate * remaining
            earned += chunk
            state["requests"] += chunk
            state["run_lifetime"] += chunk
            state["total_lifetime"] += chunk
            remaining = 0.0
    state["last_sim_at"] = float(now)
    unlock()
    return {
        "seconds": elapsed,
        "requests": earned,
        "level_from": level_before,
        "level_to": level_for(state["total_lifetime"]),
        "achievements": gained,
        "segments": segments,
    }


def catalog(state):
    requests = state["requests"]
    tiers = []
    for tier in TIERS:
        owned = state["servers"].get(tier["id"], 0)
        one = server_cost(tier["id"], owned)
        count_max, cost_max = max_affordable(tier["id"], owned, requests)
        tiers.append({
            "id": tier["id"], "name": tier["name"], "desc": tier["desc"],
            "owned": owned, "cost": one, "cost10": bulk_cost(tier["id"], owned, 10),
            "max_count": count_max, "max_cost": cost_max,
            "rps": tier["rps"], "heat": tier["heat"],
            "can_afford": one <= requests,
        })
    upgrades = []
    for upgrade_id, spec in UPGRADES.items():
        level = state["upgrades"].get(upgrade_id, 0)
        cost = upgrade_cost(upgrade_id, level) if level < spec["max"] else None
        upgrades.append({
            "id": upgrade_id, "name": spec["name"], "desc": spec["desc"],
            "level": level, "max": spec["max"], "cost": cost,
            "can_afford": cost is not None and cost <= requests,
        })
    unlocked = set(state["achievements"])
    achievements = [
        {"id": item["id"], "name": item["name"], "desc": item["desc"],
         "bonus": item["bonus"], "unlocked": item["id"] in unlocked}
        for item in ACHIEVEMENTS
    ]
    gain = prestige_tokens(state["run_lifetime"])
    return {
        "tiers": tiers,
        "upgrades": upgrades,
        "achievements": achievements,
        "reboot": {
            "tokens": gain,
            "available": gain >= 1,
            "threshold": REBOOT_THRESHOLD,
            "progress": min(1.0, state["run_lifetime"] / REBOOT_THRESHOLD),
        },
    }


def apply_action(state, action):
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    kind = action.get("type")
    if kind == "buyServer":
        tier_id = action.get("tier")
        _tier(tier_id)
        owned = state["servers"].get(tier_id, 0)
        qty = action.get("qty", 1)
        if qty == "max":
            qty, cost = max_affordable(tier_id, owned, state["requests"])
            if qty < 1:
                raise ValueError("not enough requests")
        elif isinstance(qty, int) and not isinstance(qty, bool) and qty in (1, 10):
            cost = bulk_cost(tier_id, owned, qty)
            if cost > state["requests"]:
                raise ValueError("not enough requests")
        else:
            raise ValueError("qty must be 1, 10, or max")
        state["requests"] -= cost
        state["servers"][tier_id] = owned + qty
        name = _tier(tier_id)["name"]
        return {"type": "buyServer", "tier": tier_id, "message": f"{name} {qty}대를 설치했습니다.", "qty": qty, "cost": cost}
    if kind == "buyUpgrade":
        upgrade_id = action.get("id")
        spec = _upgrade(upgrade_id)
        level = state["upgrades"].get(upgrade_id, 0)
        if level >= spec["max"]:
            raise ValueError("upgrade already maxed")
        cost = upgrade_cost(upgrade_id, level)
        if cost > state["requests"]:
            raise ValueError("not enough requests")
        state["requests"] -= cost
        state["upgrades"][upgrade_id] = level + 1
        return {"type": "buyUpgrade", "id": upgrade_id, "level": level + 1,
                "message": f"{spec['name']} Lv.{level + 1} 업그레이드 완료.", "cost": cost}
    if kind == "reboot":
        gain = prestige_tokens(state["run_lifetime"])
        if gain < 1:
            raise ValueError("reboot is not available yet")
        state["tokens"] += gain
        state["reboots"] += 1
        state["requests"] = SEED_REQUESTS
        state["run_lifetime"] = 0.0
        state["servers"] = {"cache": 1}
        state["upgrades"] = {}
        return {"type": "reboot", "message": f"Reboot 완료. 가동 토큰 +{gain}.", "tokens": gain}
    if kind == "reset":
        created = state["created_at"]
        last_sim = state["last_sim_at"]
        fresh = initial_state(last_sim)
        fresh["created_at"] = created
        state.clear()
        state.update(fresh)
        return {"type": "reset", "message": "진행을 초기화했습니다."}
    raise ValueError("unknown action type")
