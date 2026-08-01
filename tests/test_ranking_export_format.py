from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.export import export_latest_ranking


class LatestRankingExportFormatTests(unittest.TestCase):
    def test_current_rank_is_exported_as_an_integer(self) -> None:
        as_of_date = pd.Timestamp("2026-08-01")
        index = pd.MultiIndex.from_tuples(
            [
                (as_of_date, "SOLUSDT"),
                (as_of_date, "ZECUSDT"),
            ],
            names=["date", "symbol"],
        )
        panel = pd.DataFrame(
            {
                "in_universe": [True, True],
                "rule_score": [0.5, 0.5],
                "linear_score": [0.5, 0.5],
                "ml_score": [0.5, 0.5],
                "final_score": [0.545455, 0.545455],
                "regime": ["risk_off", "risk_off"],
                "confidence": [0.7, 0.6],
                "selected_flag": [True, False],
                "current_rank": [5.0, 6.0],
                "liquidity_stability": [0.8, 0.7],
                "avg_quote_vol_180": [20_000_000.0, 19_000_000.0],
            },
            index=index,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            exported = export_latest_ranking(panel, output_dir, as_of_date)
            with (output_dir / "latest_ranking.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(str(exported["current_rank"].dtype), "Int64")
        self.assertEqual([row["current_rank"] for row in rows], ["5", "6"])


if __name__ == "__main__":
    unittest.main()
