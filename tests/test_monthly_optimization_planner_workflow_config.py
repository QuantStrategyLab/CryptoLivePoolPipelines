from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "monthly_optimization_planner.yml"


class MonthlyOptimizationPlannerWorkflowConfigTests(unittest.TestCase):
    def test_planner_workflow_downloads_artifacts_posts_issue_and_fans_out_tasks(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("upstream_run_id:", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("gh run download", workflow)
        self.assertIn("Resolve downloaded artifact paths", workflow)
        self.assertIn("Prepare upstream review payload", workflow)
        self.assertIn("build_ai_review_payload.py", workflow)
        self.assertIn("build_monthly_optimization_plan.py", workflow)
        self.assertIn("post_monthly_optimization_issue.py", workflow)
        self.assertIn("fanout_monthly_optimization_tasks.py", workflow)
        self.assertIn("Fan out CryptoSnapshotPipelines task issue", workflow)
        self.assertIn("Resolve upstream experiment validation target", workflow)
        self.assertIn("Dispatch CryptoSnapshotPipelines experiment validation", workflow)
        self.assertIn("gh workflow run experiment_validation.yml", workflow)
        self.assertIn("Append fanout summary", workflow)
        self.assertIn("upstream_review_payload.json", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("monthly-optimization-plan-", workflow)
        self.assertNotIn("CryptoStrategies", workflow)
        self.assertNotIn("CROSS_REPO_GITHUB_TOKEN", workflow)
        self.assertNotIn("actions/create-github-app-token@v3", workflow)
        self.assertNotIn("BinancePlatform", workflow)
        self.assertNotIn("downstream_run_id", workflow)
        self.assertNotIn("downstream_repo", workflow)


if __name__ == "__main__":
    unittest.main()
