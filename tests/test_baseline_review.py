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
        self.assertIn("MISSING_REAL_PERFORMANCE_ARTIFACT", review["blocking_reason_codes"])
        self.assertFalse(review["evidence"]["placeholder_metrics"])


if __name__ == "__main__":
    unittest.main()
