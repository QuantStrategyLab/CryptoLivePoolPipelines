from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "experiment_validation.yml"


class ExperimentValidationWorkflowConfigTests(unittest.TestCase):
    def test_workflow_runs_shadow_build_and_posts_comment(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("issue_number:", workflow)
        self.assertNotIn("types: [opened, edited, reopened, labeled]", workflow)
        self.assertNotIn("github.event.issue", workflow)
        self.assertIn("self-hosted", workflow)
        self.assertIn("prepare_experiment_validation.py", workflow)
        self.assertIn("download_history.py", workflow)
        self.assertIn("run_monthly_shadow_build.py", workflow)
        self.assertIn("run_walkforward_validation.py", workflow)
        self.assertIn("render_experiment_validation_summary.py", workflow)
        self.assertIn("post_experiment_validation_comment.py", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)


if __name__ == "__main__":
    unittest.main()
