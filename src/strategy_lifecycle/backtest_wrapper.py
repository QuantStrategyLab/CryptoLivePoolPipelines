"""Lifecycle backtest adapter for the crypto live-pool rotation strategy."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

from .orchestrator_runner import CryptoLivePoolBacktestRunner


class InsufficientEvidenceError(RuntimeError):
    """Raised when a real market panel was not provided for a backtest."""


class CryptoBacktestRunner:
    """Expose the real crypto backtest engine through the lifecycle contract.

    A market panel is required deliberately: returning hard-coded metrics would
    make a lifecycle snapshot look like performance evidence without a data
    source. Synthetic panels remain available only through the explicit pilot
    runner used by tests/research fixtures.
    """

    def __init__(self, *, panel: pd.DataFrame | None = None) -> None:
        self._runner = CryptoLivePoolBacktestRunner(panel=panel) if panel is not None else None

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        if self._runner is None:
            raise InsufficientEvidenceError("CryptoBacktestRunner requires a real prepared market panel")
        return self._runner.run(strategy_profile, params, start_date=start_date, end_date=end_date)


def build_backtest_runner(*, panel: pd.DataFrame | None = None) -> CryptoBacktestRunner:
    """Build a compatible adapter; missing real data yields insufficient evidence."""
    return CryptoBacktestRunner(panel=panel)
