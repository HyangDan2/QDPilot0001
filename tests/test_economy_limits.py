"""WO-03: saturation, price overflow and clock edge cases."""

import json
import math
import os
import sqlite3
import tempfile
import unittest

from server import economy
from server.store import Store

NOW = 1_700_000_000.0
MAX = economy.MAX_STATE_VALUE


def capped_state(**overrides):
    state = economy.initial_state(NOW)
    state.update({
        "requests": 0.0,
        "run_lifetime": 0.0,
        "total_lifetime": MAX - 50.0,
        "servers": {"cache": 1_000},
    })
    state.update(overrides)
    return state


class CapTests(unittest.TestCase):
    def test_lifetime_saturation_does_not_drop_remaining_time(self):
        state = capped_state()
        gains = economy.advance(state, NOW + 100)
        self.assertEqual(state["total_lifetime"], MAX)
        self.assertTrue(gains["capped"])
        self.assertGreater(state["requests"], 0.0)
        self.assertGreater(state["run_lifetime"], 0.0)
        self.assertEqual(state["last_sim_at"], NOW + 100)
        self.assertAlmostEqual(gains["requests"], state["requests"], places=3)

        before = state["requests"]
        second = economy.advance(state, NOW + 10_100)
        self.assertEqual(state["total_lifetime"], MAX)
        self.assertGreater(state["requests"], before)
        self.assertAlmostEqual(second["requests"], state["requests"] - before, places=3)
        self.assertEqual(state["last_sim_at"], NOW + 10_100)

    def test_full_saturation_grants_zero_and_still_consumes_time(self):
        state = capped_state(requests=MAX, run_lifetime=MAX)
        gains = economy.advance(state, NOW + 1_000)
        self.assertEqual(gains["requests"], 0.0)
        self.assertEqual(state["requests"], MAX)
        self.assertEqual(state["run_lifetime"], MAX)
        self.assertTrue(gains["capped"])
        self.assertEqual(state["last_sim_at"], NOW + 1_000)
        again = economy.advance(state, NOW + 2_000)
        self.assertEqual(again["requests"], 0.0)

    def test_gains_requests_is_exactly_what_was_granted(self):
        state = capped_state(requests=MAX - 10.0, run_lifetime=MAX - 5.0)
        before_requests = state["requests"]
        gains = economy.advance(state, NOW + 50)
        self.assertAlmostEqual(gains["requests"], state["requests"] - before_requests, places=6)
        self.assertLessEqual(state["requests"], MAX)
        self.assertLessEqual(state["run_lifetime"], MAX)
        self.assertLessEqual(state["total_lifetime"], MAX)

    def test_settlement_is_independent_of_polling_near_the_cap(self):
        coarse = capped_state()
        fine = capped_state()
        span = 30 * 86400.0
        economy.advance(coarse, NOW + span)
        for step in range(1, 31):
            economy.advance(fine, NOW + step * 86400.0)
        self.assertEqual(coarse["total_lifetime"], MAX)
        self.assertEqual(fine["total_lifetime"], MAX)
        scale = max(1.0, coarse["requests"])
        self.assertLessEqual(abs(coarse["requests"] - fine["requests"]) / scale, 1e-9)
        self.assertLessEqual(abs(coarse["run_lifetime"] - fine["run_lifetime"]) / MAX, 1e-9)

    def test_segment_guard_settles_the_remainder_and_reports_reality(self):
        original = economy.MAX_SETTLE_SEGMENTS
        economy.MAX_SETTLE_SEGMENTS = 1
        try:
            state = capped_state(total_lifetime=0.0, servers={"cache": 10}, upgrades={"network": 15})
            state["last_sim_at"] = NOW
            gains = economy.advance(state, NOW + 86400.0)
        finally:
            economy.MAX_SETTLE_SEGMENTS = original
        self.assertEqual(state["last_sim_at"], NOW + 86400.0)
        self.assertAlmostEqual(gains["requests"], state["requests"], places=6)
        self.assertGreater(state["requests"], 0.0)


class PriceSafetyTests(unittest.TestCase):
    def test_exponential_price_never_overflows_or_wraps(self):
        price = economy.server_cost("cache", 20_000)
        self.assertEqual(price, economy.UNBUYABLE)
        self.assertGreater(price, MAX)
        small = economy.server_cost("cache", 500)
        self.assertTrue(math.isfinite(small))
        self.assertGreater(small, 0)

    def test_unbuyable_catalog_entry_is_clamped_and_never_affordable(self):
        state = economy.initial_state(NOW)
        state["requests"] = MAX
        state["servers"] = {"cache": 20_000}
        catalog = economy.catalog(state)
        entry = next(tier for tier in catalog["tiers"] if tier["id"] == "cache")
        self.assertEqual(entry["cost"], int(MAX))
        self.assertEqual(entry["cost10"], int(MAX))
        self.assertEqual(entry["max_count"], 0)
        self.assertEqual(entry["max_cost"], 0)
        self.assertFalse(entry["can_afford"])
        json.dumps(catalog, allow_nan=False)

        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": 1})
        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": "max"})
        self.assertEqual(state["requests"], MAX)
        self.assertEqual(state["servers"], {"cache": 20_000})

    def test_bulk_and_max_hold_agree_at_large_but_finite_prices(self):
        owned = 300
        one = economy.server_cost("cache", owned)
        ten = economy.bulk_cost("cache", owned, 10)
        self.assertEqual(ten, sum(economy.server_cost("cache", owned + i) for i in range(10)))
        count, cost = economy.max_affordable("cache", owned, ten)
        self.assertEqual(count, 10)
        self.assertEqual(cost, ten)
        self.assertLessEqual(one, ten)

    def test_max_hold_never_exceeds_the_documented_batch(self):
        count, cost = economy.max_affordable("cache", 0, MAX)
        self.assertGreater(count, 0)
        self.assertLessEqual(count, economy.MAX_BUY)
        self.assertLessEqual(cost, MAX)
        self.assertEqual(cost, economy.bulk_cost("cache", 0, count))


class ClockCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clock.sqlite3")
        self.store = Store(self.path)

    def test_admin_checkpoints_do_not_regress_after_a_long_rewind(self):
        self.store.world(NOW, actor="alice")
        ahead = self.store.world(NOW + 3600, actor="alice")
        self.assertAlmostEqual(ahead.away_seconds, 3600.0)

        rewound = self.store.world(NOW - 10 * 365 * 86400, actor="alice")
        self.assertEqual(rewound.gains["seconds"], 0.0)
        self.assertEqual(rewound.gains["clock"], "resync")
        self.assertEqual(rewound.timestamps["adminLastSeen"], NOW + 3600)
        self.assertEqual(rewound.timestamps["lastRewardAt"], NOW + 3600)
        self.assertEqual(rewound.timestamps["awaySeconds"], 0.0)
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT last_seen, last_reward_at FROM admin_seen WHERE username = 'alice'"
            ).fetchone()
        self.assertEqual(row[0], NOW + 3600)
        self.assertEqual(row[1], NOW + 3600)

        # Once the clock catches up, normal absence resumes from the last
        # checkpoint instead of granting the rewound window twice.
        recovered = self.store.world(NOW + 3700, actor="alice")
        self.assertAlmostEqual(recovered.away_seconds, 100.0)
        self.assertEqual(recovered.state["last_sim_at"], NOW + 3700)

    def test_small_rewind_holds_without_regressing_simulation(self):
        self.store.world(NOW)
        held = self.store.world(NOW - 100)
        self.assertEqual(held.gains["clock"], "rewind")
        self.assertEqual(held.state["last_sim_at"], NOW)
        forward = self.store.world(NOW + 50)
        self.assertEqual(forward.gains["clock"], "ok")
        self.assertAlmostEqual(forward.gains["seconds"], 50.0)


if __name__ == "__main__":
    unittest.main()
