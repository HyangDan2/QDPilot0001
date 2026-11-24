"""Deterministic tests for the Idle Ops economy."""

import unittest

from server import economy

NOW = 1_700_000_000.0


def fresh(**overrides):
    state = economy.initial_state(NOW)
    state.update(overrides)
    return state


class CostTests(unittest.TestCase):
    def test_first_prices_and_growth(self):
        self.assertEqual(economy.server_cost("cache", 0), 15)
        self.assertEqual(economy.server_cost("cache", 1), 18)
        self.assertEqual(economy.server_cost("app", 0), 150)
        self.assertGreater(economy.server_cost("db", 5), economy.server_cost("db", 4))

    def test_bulk_cost_is_sum_of_single_prices(self):
        expected = sum(economy.server_cost("cache", index) for index in range(10))
        self.assertEqual(economy.bulk_cost("cache", 0, 10), expected)
        self.assertEqual(economy.bulk_cost("cache", 0, 0), 0)

    def test_max_affordable_respects_budget(self):
        count, cost = economy.max_affordable("cache", 0, 1000)
        self.assertGreaterEqual(count, 1)
        self.assertLessEqual(cost, 1000)
        self.assertGreater(economy.server_cost("cache", count), 1000 - cost)
        self.assertEqual(economy.max_affordable("cache", 0, 0), (0, 0))

    def test_upgrade_cost(self):
        self.assertEqual(economy.upgrade_cost("network", 0), 300)
        self.assertEqual(economy.upgrade_cost("network", 1), 720)

    def test_unknown_ids(self):
        with self.assertRaises(ValueError):
            economy.server_cost("nope", 0)
        with self.assertRaises(ValueError):
            economy.upgrade_cost("nope", 0)


class ProgressionTests(unittest.TestCase):
    def test_level_curve(self):
        self.assertEqual(economy.level_for(0), 1)
        self.assertEqual(economy.level_for(9_999), 1)
        self.assertEqual(economy.level_for(10_000), 2)
        self.assertEqual(economy.level_for(40_000), 3)
        self.assertEqual(economy.level_for(90_000), 4)

    def test_derived_progress_and_bonus(self):
        state = fresh(total_lifetime=10_000)
        info = economy.derived(state)
        self.assertEqual(info["level"], 2)
        self.assertAlmostEqual(info["level_bonus"], 0.01)
        self.assertAlmostEqual(info["level_progress"], 0.0)
        state["total_lifetime"] = 25_000
        self.assertAlmostEqual(economy.derived(state)["level_progress"], 0.5)

    def test_achievement_bonus_applies_to_rps(self):
        state = fresh(servers={"cache": 2})
        before = economy.rps(state)
        state["achievements"] = ["first_server"]
        after = economy.rps(state)
        self.assertAlmostEqual(after, before * 1.02, places=6)

    def test_heat_and_cooling(self):
        state = fresh(servers={"db": 5})
        self.assertAlmostEqual(economy.heat_load(state), 25.0)
        self.assertAlmostEqual(economy.cooling_capacity(state), 10.0)
        self.assertAlmostEqual(economy.thermal_efficiency(state), 0.4)
        state["upgrades"] = {"cooling": 3}
        self.assertAlmostEqual(economy.cooling_capacity(state), 28.0)
        self.assertAlmostEqual(economy.thermal_efficiency(state), 1.0)

    def test_efficiency_floor(self):
        state = fresh(servers={"quantum": 100})
        self.assertAlmostEqual(economy.thermal_efficiency(state), 0.25)

    def test_overclock_adds_heat_and_rps(self):
        state = fresh(servers={"app": 4})
        base = economy.rps(state)
        base_heat = economy.heat_load(state)
        state["upgrades"] = {"overclock": 2}
        self.assertAlmostEqual(economy.heat_load(state), base_heat * 1.5)
        self.assertAlmostEqual(economy.rps(state), base * 1.6, places=6)

    def test_token_multiplier(self):
        state = fresh(servers={"app": 4})
        before = economy.rps(state)
        state["tokens"] = 5
        self.assertAlmostEqual(economy.rps(state), before * 1.10, places=6)

    def test_prestige_tokens(self):
        self.assertEqual(economy.prestige_tokens(0), 0)
        self.assertEqual(economy.prestige_tokens(999_999), 0)
        self.assertEqual(economy.prestige_tokens(1_000_000), 1)
        self.assertEqual(economy.prestige_tokens(4_000_000), 2)
        self.assertEqual(economy.prestige_tokens(8_999_999), 2)


class AdvanceTests(unittest.TestCase):
    def test_elapsed_time_earns_requests(self):
        state = fresh()
        economy.advance(state, NOW)  # settle the starting state (first_server)
        rate = economy.rps(state)
        gains = economy.advance(state, NOW + 100)
        self.assertAlmostEqual(gains["seconds"], 100.0)
        self.assertAlmostEqual(gains["requests"], rate * 100.0, places=6)
        self.assertAlmostEqual(state["requests"], 25.0 + rate * 100.0, places=6)
        self.assertEqual(state["last_sim_at"], NOW + 100)

    def test_absence_is_not_capped_by_a_day(self):
        state = fresh()
        year = 365 * 86400.0
        gains = economy.advance(state, NOW + year)
        self.assertAlmostEqual(gains["seconds"], year)
        self.assertGreater(state["requests"], 10_000)

    def test_backwards_clock_does_not_block_progress(self):
        state = fresh()
        state["last_sim_at"] = NOW + 10_000
        gains = economy.advance(state, NOW)
        self.assertEqual(gains["requests"], 0)
        self.assertEqual(state["last_sim_at"], NOW)
        gains = economy.advance(state, NOW + 10)
        self.assertGreater(gains["requests"], 0)

    def test_advance_unlocks_achievements_and_levels(self):
        state = fresh(total_lifetime=800_000, servers={"cache": 10})
        gains = economy.advance(state, NOW + 50_000)
        self.assertIn("first_server", gains["achievements"])
        self.assertIn("ten_servers", gains["achievements"])
        self.assertIn("lifetime_1m", state["achievements"])
        self.assertGreaterEqual(gains["level_to"], 10)
        self.assertGreater(gains["level_to"], gains["level_from"])

    def test_catalog_snapshot(self):
        state = fresh()
        snapshot = economy.catalog(state)
        cache = snapshot["tiers"][0]
        self.assertEqual(cache["id"], "cache")
        self.assertTrue(cache["can_afford"])
        self.assertFalse(snapshot["reboot"]["available"])
        self.assertEqual(len(snapshot["achievements"]), len(economy.ACHIEVEMENTS))


class SettlementFrequencyTests(unittest.TestCase):
    """Same start state and elapsed time must not depend on polling cadence."""

    @staticmethod
    def build_state():
        return fresh(
            requests=0.0,
            total_lifetime=795_000.0,
            servers={"cache": 4, "app": 3, "db": 1},
            upgrades={"network": 1, "cooling": 2},
        )

    def integrate(self, seconds, interval):
        state = self.build_state()
        now = NOW
        for _ in range(int(round(seconds / interval))):
            now += interval
            economy.advance(state, now)
        return state

    def assert_equivalent(self, expected, actual):
        for key in ("requests", "run_lifetime", "total_lifetime"):
            limit = max(1e-6, 1e-9 * max(abs(expected[key]), abs(actual[key])))
            self.assertLessEqual(abs(expected[key] - actual[key]), limit, key)
        for key in ("tokens", "reboots", "servers", "upgrades", "achievements"):
            self.assertEqual(actual[key], expected[key], key)
        self.assertEqual(
            economy.level_for(actual["total_lifetime"]),
            economy.level_for(expected["total_lifetime"]),
        )

    def test_fine_intervals_match_a_single_hour_intervals(self):
        bulk = self.integrate(6 * 3600, 6 * 3600)
        self.assertGreater(bulk["total_lifetime"], 1_000_000)
        self.assertIn("level_10", bulk["achievements"])
        self.assertIn("lifetime_1m", bulk["achievements"])
        for interval in (1, 8, 15):
            self.assert_equivalent(bulk, self.integrate(6 * 3600, interval))

    def test_coarse_intervals_match_a_month_bulk_settlement(self):
        bulk = self.integrate(30 * 86400, 30 * 86400)
        for interval in (15, 3600, 86400):
            self.assert_equivalent(bulk, self.integrate(30 * 86400, interval))

    def test_advance_reports_segmented_settlement(self):
        state = self.build_state()
        gains = economy.advance(state, NOW + 6 * 3600)
        self.assertGreater(gains["segments"], 5)
        self.assertEqual(gains["level_from"], 9)
        self.assertGreater(gains["level_to"], gains["level_from"])


class ActionTests(unittest.TestCase):
    def test_buy_server_qty_one(self):
        state = fresh(requests=100)
        result = economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": 1})
        self.assertEqual(state["servers"]["cache"], 2)
        self.assertEqual(state["requests"], 100 - economy.server_cost("cache", 1))
        self.assertEqual(result["qty"], 1)

    def test_buy_server_max(self):
        state = fresh(requests=10_000)
        result = economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": "max"})
        self.assertGreater(result["qty"], 0)
        expected = 1 + result["qty"]
        self.assertEqual(state["servers"]["cache"], expected)
        self.assertLess(state["requests"], economy.server_cost("cache", expected))

    def test_insufficient_requests(self):
        state = fresh(requests=1)
        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": 1})
        self.assertEqual(state["servers"], {"cache": 1})
        self.assertEqual(state["requests"], 1)

    def test_invalid_qty(self):
        state = fresh(requests=100)
        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "buyServer", "tier": "cache", "qty": 2})

    def test_buy_upgrade_and_max(self):
        state = fresh(requests=10_000)
        economy.apply_action(state, {"type": "buyUpgrade", "id": "cooling"})
        self.assertEqual(state["upgrades"]["cooling"], 1)
        state["upgrades"]["cooling"] = economy.UPGRADES["cooling"]["max"]
        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "buyUpgrade", "id": "cooling"})

    def test_reboot_requires_threshold(self):
        state = fresh(run_lifetime=999_999)
        with self.assertRaises(ValueError):
            economy.apply_action(state, {"type": "reboot"})

    def test_reboot_keeps_tokens_and_progress(self):
        state = fresh(
            run_lifetime=1_000_000,
            total_lifetime=5_000_000,
            tokens=2,
            reboots=1,
            servers={"app": 3},
            upgrades={"network": 2},
            achievements=["first_server", "first_reboot"],
        )
        result = economy.apply_action(state, {"type": "reboot"})
        self.assertEqual(result["tokens"], 1)
        self.assertEqual(state["tokens"], 3)
        self.assertEqual(state["reboots"], 2)
        self.assertEqual(state["servers"], {"cache": 1})
        self.assertEqual(state["upgrades"], {})
        self.assertEqual(state["requests"], economy.SEED_REQUESTS)
        self.assertEqual(state["run_lifetime"], 0.0)
        self.assertEqual(state["total_lifetime"], 5_000_000)
        self.assertIn("first_server", state["achievements"])

    def test_reset_keeps_timestamps(self):
        state = fresh(tokens=4, total_lifetime=123_456)
        state["created_at"] = NOW - 500
        state["last_sim_at"] = NOW
        economy.apply_action(state, {"type": "reset"})
        self.assertEqual(state["tokens"], 0)
        self.assertEqual(state["total_lifetime"], 0.0)
        self.assertEqual(state["created_at"], NOW - 500)
        self.assertEqual(state["last_sim_at"], NOW)
        self.assertEqual(state["servers"], {"cache": 1})

    def test_unknown_action(self):
        with self.assertRaises(ValueError):
            economy.apply_action(fresh(), {"type": "nope"})


class NormalizeTests(unittest.TestCase):
    def test_round_trip(self):
        state = fresh(servers={"cache": 2, "gpu": 1}, upgrades={"network": 2}, achievements=["first_server"])
        restored = economy.normalize_state(state)
        self.assertEqual(restored["servers"], {"cache": 2, "gpu": 1})
        self.assertEqual(restored["upgrades"], {"network": 2})
        self.assertEqual(restored["achievements"], ["first_server"])

    def test_rejects_bad_version_and_numbers(self):
        state = fresh()
        state["version"] = 99
        with self.assertRaises(ValueError):
            economy.normalize_state(state)
        state = fresh(requests=-1)
        with self.assertRaises(ValueError):
            economy.normalize_state(state)

    def test_filters_unknown_achievements(self):
        state = fresh(achievements=["first_server", "bogus"])
        self.assertEqual(economy.normalize_state(state)["achievements"], ["first_server"])

    def test_rejects_out_of_range_upgrades(self):
        state = fresh(upgrades={"network": 999})
        with self.assertRaises(ValueError):
            economy.normalize_state(state)


if __name__ == "__main__":
    unittest.main()
