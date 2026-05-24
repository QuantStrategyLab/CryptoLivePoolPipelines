from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTO_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "auto_optimization_pr.yml"
MERGE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "auto_merge_optimization_pr.yml"
FEEDBACK_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "codex_pr_feedback.yml"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


class AutoOptimizationPrWorkflowConfigTests(unittest.TestCase):
    def test_auto_optimization_workflow_handles_monthly_task_issues(self) -> None:
        self.assertFalse(AUTO_WORKFLOW.exists())

    def test_auto_merge_workflow_waits_for_ci_and_merges_only_safe_ready_prs(self) -> None:
        workflow = MERGE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn('workflows: ["CI"]', workflow)
        self.assertIn("codex/monthly-optimization-issue-", workflow)
        self.assertNotIn("automation/monthly-optimization-issue-", workflow)
        self.assertIn("gh pr view", workflow)
        self.assertIn("labels", workflow)
        self.assertIn("evaluate_changed_files", workflow)
        self.assertIn("Task-level auto-merge eligible: `yes`", workflow)
        self.assertIn("auto-merge-ok", workflow)
        self.assertIn("missing_auto_merge_label", workflow)
        self.assertIn("gh pr merge", workflow)

    def test_ci_workflow_supports_manual_dispatch(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)

    def test_codex_feedback_workflow_requeues_failed_ci_and_review_feedback(self) -> None:
        workflow = FEEDBACK_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn("pull_request_review:", workflow)
        self.assertIn("codex/monthly-optimization-issue-", workflow)
        self.assertIn("auto-optimization-pr:issue-", workflow)
        self.assertIn("gh issue comment", workflow)
        self.assertIn('MAX_CODEX_FEEDBACK_ROUNDS: "3"', workflow)
        self.assertIn("gh issue edit", workflow)
        self.assertIn("--remove-label codex-bridge", workflow)
        self.assertIn("Codex PR Retry Limit Reached", workflow)
        self.assertIn("Codex PR CI Feedback", workflow)
        self.assertIn("Codex PR Review Feedback", workflow)


if __name__ == "__main__":
    unittest.main()
