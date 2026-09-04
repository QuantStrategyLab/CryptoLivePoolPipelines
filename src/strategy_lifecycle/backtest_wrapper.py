"""Crypto BacktestRunner — wraps BinancePlatform backtest for the lifecycle system."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, NoReturn


class CryptoBacktestRunner:
    """Test-only placeholder; never produces a lifecycle BacktestResult."""

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> NoReturn:
        """Reject placeholder execution before it can enter lifecycle persistence."""
        raise RuntimeError(
            "CryptoBacktestRunner is a test-only fixture and cannot create a BacktestResult; "
            "use a real artifact-backed runner instead."
        )


def build_backtest_runner() -> CryptoBacktestRunner:
    """Factory for the Crypto backtest runner."""
    return CryptoBacktestRunner()
