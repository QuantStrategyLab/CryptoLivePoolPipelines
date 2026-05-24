from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "monthly_publish.yml"
AI_REVIEW_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ai_review.yml"
README_ZH_PATH = PROJECT_ROOT / "README.zh-CN.md"


class MonthlyPublishWorkflowConfigTests(unittest.TestCase):
    def test_publish_targets_use_vars_only(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("actions: write", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("google-github-actions/auth@v3", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}", workflow)
        self.assertIn("GCS_BUCKET: ${{ vars.GCS_BUCKET }}", workflow)
        self.assertIn("credentials_json: ${{ secrets.GCP_SERVICE_ACCOUNT_KEY }}", workflow)
        self.assertIn("issues: write", workflow)
        self.assertNotIn("secrets.GCP_PROJECT_ID", workflow)
        self.assertNotIn("secrets.GCS_BUCKET", workflow)

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
        self.assertIn("SELFHOSTED_CODEX_REVIEW_REPOSITORY", workflow)
        self.assertIn("QuantStrategyLab/CryptoCodexAuditBridge", workflow)
        self.assertIn("CROSS_REPO_GITHUB_APP_ID", workflow)
        self.assertIn("CROSS_REPO_GITHUB_APP_PRIVATE_KEY", workflow)
        self.assertIn("actions/create-github-app-token@v3", workflow)
        self.assertIn("CryptoCodexAuditBridge", workflow)
        self.assertIn("permission-actions: write", workflow)
        self.assertIn("APP_TOKEN", workflow)
        self.assertIn("Trigger Monthly Review Automation", workflow)
        self.assertIn("CODEX_AUDIT_DISPATCH_TOKEN", workflow)
        self.assertIn("SELFHOSTED_CODEX_REVIEW_PROVIDER", workflow)
        self.assertIn("REVIEW_PROVIDER", workflow)
        self.assertIn('"provider": provider', workflow)
        self.assertNotIn("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", workflow)
        self.assertNotIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", workflow)
        self.assertNotIn("legacy API review fallback", workflow)
        self.assertIn("selfhosted_monthly_review.yml", workflow)
        self.assertIn("/actions/workflows/selfhosted_monthly_review.yml/dispatches", workflow)
        self.assertNotIn("/repos/{target_repository}/dispatches", workflow)
        self.assertNotIn("LEGACY_API_REVIEW_ENABLED", workflow)
        self.assertNotIn("/actions/workflows/ai_review.yml/dispatches", workflow)

    def test_ai_review_workflow_supports_dispatch_and_comment_posting(self) -> None:
        workflow = AI_REVIEW_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("issue_number:", workflow)
        self.assertIn("vars.LEGACY_AI_REVIEW_ENABLED == 'true'", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("Load review issue context", workflow)
        self.assertIn("api.github.com/repos/{repo}/issues/{issue_number}", workflow)
        self.assertIn("github_token: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertIn('FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"', workflow)
        self.assertIn("This is a strategy-optimization review, not only a release QA check.", workflow)
        self.assertIn("If shadow or challenger tracks are missing, say evidence is incomplete", workflow)
        self.assertIn("Strategy Optimization Directions", workflow)
        self.assertIn("post_monthly_ai_review_comment.py", workflow)
        self.assertIn("render_monthly_ai_review.py", workflow)
        self.assertIn("run_openai_secondary_review.py", workflow)
        self.assertIn("build_ai_review_payload.py", workflow)
        self.assertIn("steps.claude_review.outputs.execution_file", workflow)
        self.assertIn("OPENAI_API_KEY", workflow)
        self.assertIn("OPENAI_SECONDARY_MODEL", workflow)
        self.assertIn("secondary_review.json", workflow)
        self.assertIn("final_review_payload.json", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertNotIn("allowed_tools:", workflow)
        self.assertNotIn("custom_instructions:", workflow)

    def test_chinese_readme_matches_current_monthly_review_defaults(self) -> None:
        readme = README_ZH_PATH.read_text(encoding="utf-8")

        self.assertIn("CryptoCodexAuditBridge", readme)
        self.assertIn("SELFHOSTED_CODEX_REVIEW_PROVIDER", readme)
        self.assertIn("OPENAI_API_KEY", readme)
        self.assertIn("配置在 `CryptoCodexAuditBridge`", readme)
        self.assertIn("必须从 GitHub variable 读取", readme)
        self.assertNotIn("只配置 `ANTHROPIC_API_KEY`", readme)
        self.assertNotIn("如果这两个旧值还在 secret 里，也会继续兼容", readme)


if __name__ == "__main__":
    unittest.main()
