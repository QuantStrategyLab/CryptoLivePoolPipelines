"""Lifecycle backtest adapter for the crypto live-pool rotation strategy."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

from .orchestrator_runner import CryptoLivePoolBacktestRunner, PROFILE_NAME


class CryptoBacktestRunner:
    """Expose the real crypto backtest engine through the lifecycle contract.

    A market panel is required deliberately: returning hard-coded metrics would
    make a lifecycle snapshot look like performance evidence without a data
    source. Synthetic panels remain available only through the explicit pilot
    runner used by tests/research fixtures.
    """

    def __init__(self, *, panel: pd.DataFrame | None = None) -> None:
        if panel is None:
            raise ValueError("CryptoBacktestRunner requires a real prepared market panel")
        self._runner = CryptoLivePoolBacktestRunner(panel=panel)

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        if strategy_profile != PROFILE_NAME:
            raise ValueError(f"Unsupported strategy_profile={strategy_profile!r}")
        return self._runner.run(strategy_profile, params, start_date=start_date, end_date=end_date)


def build_backtest_runner(*, panel: pd.DataFrame) -> CryptoBacktestRunner:
    """Build a production adapter from a required prepared real-data panel."""
    return CryptoBacktestRunner(panel=panel)
