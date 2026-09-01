from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "monthly_publish.yml"
README_ZH_PATH = PROJECT_ROOT / "README.zh-CN.md"
QPK_DEPENDENCY = (
    "quant-platform-kit @ "
    "git+https://github.com/QuantStrategyLab/QuantPlatformKit.git@ff70b162ac8e50e1ece617e570dab76b6740d41e"
)


class MonthlyPublishWorkflowConfigTests(unittest.TestCase):
    def test_publish_targets_use_vars_only(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("actions: write", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("google-github-actions/auth@v3", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}", workflow)
        self.assertIn("GCS_BUCKET: ${{ vars.GCS_BUCKET }}", workflow)
        self.assertIn("workload_identity_provider:", workflow)
        self.assertIn("service_account:", workflow)
        self.assertIn("issues: write", workflow)
        self.assertNotIn("secrets.GCP_PROJECT_ID", workflow)
        self.assertNotIn("secrets.GCS_BUCKET", workflow)
        self.assertNotIn("credentials_json:", workflow)
        self.assertNotIn("GCP_SERVICE_ACCOUNT_KEY", workflow)

    def test_monthly_review_issue_creation_does_not_require_gh_cli(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertNotIn("gh label create", workflow)
        self.assertNotIn("gh issue create", workflow)
        self.assertNotIn("gh workflow run", workflow)
        self.assertIn("run_monthly_shadow_build.py", workflow)
        self.assertIn("--skip-publish-dry-run", workflow)
        self.assertIn("--shadow-universe-mode", workflow)
        self.assertIn("https://api.github.com/repos/{repository}", workflow)
        self.assertIn('GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}', workflow)
        self.assertNotIn("GITHUB_OUTPUT: ${{ github.output }}", workflow)
        self.assertIn("issue_number=", workflow)
        self.assertIn("CODEX_AUDIT_BRIDGE_REPOSITORY", workflow)
        self.assertIn("CODEX_AUDIT_BRIDGE_REF", workflow)
        self.assertIn('"ref": os.environ["CODEX_AUDIT_BRIDGE_REF"]', workflow)
        self.assertIn("QuantStrategyLab/AIAuditBridge", workflow)
        self.assertIn("CROSS_REPO_GITHUB_APP_ID", workflow)
        self.assertIn("CROSS_REPO_GITHUB_APP_PRIVATE_KEY", workflow)
        self.assertIn("actions/create-github-app-token@v3", workflow)
        self.assertIn("AIAuditBridge", workflow)
        self.assertIn("permission-actions: write", workflow)
        self.assertIn("APP_TOKEN", workflow)
        self.assertIn("Trigger Monthly Review Automation", workflow)
        self.assertIn("CODEX_AUDIT_DISPATCH_TOKEN", workflow)
        self.assertIn("CODEX_AUDIT_PROVIDER", workflow)
        self.assertIn("CODEX_AUDIT_PROVIDER || 'auto'", workflow)
        self.assertIn("REVIEW_PROVIDER", workflow)
        self.assertIn('"provider": provider', workflow)
        self.assertIn('"anthropic"', workflow)
        self.assertIn('"api"', workflow)
        self.assertNotIn("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", workflow)
        self.assertNotIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", workflow)
        self.assertNotIn("legacy API review fallback", workflow)
        self.assertIn("codex_audit.yml", workflow)
        self.assertIn("/actions/workflows/codex_audit.yml/dispatches", workflow)
        self.assertNotIn("/repos/{target_repository}/dispatches", workflow)
        self.assertNotIn("LEGACY_API_REVIEW_ENABLED", workflow)
        self.assertNotIn("/actions/workflows/ai_review.yml/dispatches", workflow)

    def test_real_publish_dependency_is_locked(self) -> None:
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        requirements_lock = (PROJECT_ROOT / "requirements-lock.txt").read_text(encoding="utf-8")

        self.assertIn(QPK_DEPENDENCY, requirements)
        self.assertIn(QPK_DEPENDENCY, requirements_lock)

    def test_source_local_legacy_ai_workflows_are_removed(self) -> None:
        workflow_dir = PROJECT_ROOT / ".github" / "workflows"

        self.assertFalse((workflow_dir / "ai_review.yml").exists())
        self.assertFalse((workflow_dir / "auto_optimization_pr.yml").exists())
        self.assertFalse((workflow_dir / "monthly_optimization_planner.yml").exists())
        self.assertFalse((workflow_dir / "experiment_validation.yml").exists())
        self.assertFalse((workflow_dir / "auto_merge_optimization_pr.yml").exists())
        self.assertFalse((workflow_dir / "codex_pr_feedback.yml").exists())

    def test_chinese_readme_matches_current_monthly_review_defaults(self) -> None:
        readme = README_ZH_PATH.read_text(encoding="utf-8")

        self.assertIn("AIAuditBridge", readme)
        self.assertIn("CODEX_AUDIT_PROVIDER", readme)
        self.assertIn("OPENAI_API_KEY", readme)
        self.assertIn("ANTHROPIC_API_KEY", readme)
        self.assertIn("配置在 `AIAuditBridge`", readme)
        self.assertIn("必须从 GitHub variable 读取", readme)
        self.assertIn("本仓库不再保留 source-local `ai_review.yml`", readme)
        self.assertNotIn("只配置 `ANTHROPIC_API_KEY`", readme)
        self.assertNotIn("如果这两个旧值还在 secret 里，也会继续兼容", readme)


if __name__ == "__main__":
    unittest.main()
