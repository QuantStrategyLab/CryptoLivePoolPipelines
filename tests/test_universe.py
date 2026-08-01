from __future__ import annotations

import unittest

import pandas as pd

from src.universe import _make_universe_refresh_schedule, build_dynamic_universe


class DynamicUniverseTests(unittest.TestCase):
    def test_monthly_refresh_schedule_keeps_regular_monthly_snapshots(self) -> None:
        dates = list(pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-30"]))

        self.assertEqual(_make_universe_refresh_schedule(dates, "monthly"), dates)

    def test_monthly_confirmations_do_not_double_count_adjacent_month_boundary(self) -> None:
        dates = pd.to_datetime(["2026-05-31", "2026-06-30", "2026-07-31", "2026-08-01"])
        rows = []
        for date in dates:
            for symbol in ("AAAUSDT", "BTCUSDT"):
                eligible_value = 100.0 if date <= pd.Timestamp("2026-06-30") else 0.0
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "age_days": 1_000,
                        "avg_quote_vol_30": eligible_value,
                        "avg_quote_vol_90": eligible_value,
                        "avg_quote_vol_180": eligible_value,
                        "liquidity_stability": 1.0,
                        "tradable_ratio_180": 1.0,
                        "quote_volume": eligible_value,
                    }
                )
        panel = pd.DataFrame(rows).set_index(["date", "symbol"])
        metadata = pd.DataFrame(
            [
                {
                    "symbol": "AAAUSDT",
                    "status": "TRADING",
                    "base_asset": "AAA",
                    "quote_asset": "USDT",
                    "is_spot_trading_allowed": True,
                },
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "is_spot_trading_allowed": True,
                },
            ]
        )
        config = {
            "universe": {
                "research_mode": "core_major",
                "live_mode": "core_major",
                "refresh_frequency": "monthly",
                "allowed_quote_assets": ["USDT"],
                "include_benchmark_symbols": ["BTCUSDT"],
                "exclude_base_assets": [],
                "exclude_symbols": [],
                "exclude_suffix_keywords": [],
                "modes": {
                    "core_major": {
                        "min_history_days": 1,
                        "min_avg_quote_vol_30": 1.0,
                        "min_avg_quote_vol_90": 1.0,
                        "min_avg_quote_vol_180": 1.0,
                        "min_liquidity_stability": 0.5,
                        "min_tradable_ratio_180": 0.5,
                        "min_daily_quote_vol": 0.0,
                        "min_liquidity_days_90": 0,
                        "min_liquidity_days_180": 0,
                        "entry_confirmations": 2,
                        "exit_confirmations": 2,
                    }
                },
            }
        }

        result = build_dynamic_universe(panel, metadata, config, universe_mode="core_major", purpose="live")

        self.assertTrue(result.loc[(pd.Timestamp("2026-08-01"), "AAAUSDT"), "in_universe"])


if __name__ == "__main__":
    unittest.main()
