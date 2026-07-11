from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baseline_review", ROOT / "scripts/run_crypto_live_pool_baseline_review.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class BaselineReviewTests(unittest.TestCase):
    def test_walkforward_schema_is_checked_separately_from_performance_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            performance = root / "performance.csv"
            walkforward = root / "walkforward.csv"
            performance.write_text("CAGR,Sharpe\n0.2,1.1\n", encoding="utf-8")
            walkforward.write_text(
                "window_id,test_start,test_end,window_cagr,window_sharpe\n"
                "0,2025-01-01,2025-03-31,0.1,0.8\n",
                encoding="utf-8",
            )

            review = MODULE.build_review(performance_summary=performance, walkforward_summary=walkforward)

        self.assertEqual(review["blocking_reason_codes"], ["BASELINE_REVIEW_NOT_YET_FROZEN"])

    def test_missing_real_artifacts_is_insufficient_and_not_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = MODULE.build_review(
                performance_summary=root / "performance.csv",
                walkforward_summary=root / "walkforward.csv",
            )
        self.assertEqual(review["decision"], "insufficient_evidence")
        self.assertFalse(review["promotion_allowed"])
        self.assertEqual(len(review["hard_gates"]), 12)
        self.assertIn("MISSING_OR_INVALID_REAL_PERFORMANCE_ARTIFACT", review["blocking_reason_codes"])
        self.assertFalse(review["evidence"]["placeholder_metrics"])
        packet = review["decision_packet"]
        self.assertEqual(packet["system_recommendation"], "insufficient_evidence")
        self.assertEqual(packet["evidence_sufficiency"], "insufficient_evidence")
        self.assertEqual(packet["allowed_human_decisions"], ["approve_research", "reject_rollback"])


if __name__ == "__main__":
    unittest.main()
