from __future__ import annotations

import unittest
from datetime import date

from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner


class CryptoBacktestRunnerTests(unittest.TestCase):
    def test_placeholder_runner_fails_closed_before_returning_backtest_result(self) -> None:
        runner = build_backtest_runner()

        with self.assertRaisesRegex(RuntimeError, "test-only fixture"):
            runner.run(
                "crypto_live_pool_rotation",
                {},
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )


if __name__ == "__main__":
    unittest.main()
