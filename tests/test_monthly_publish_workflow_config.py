from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "monthly_publish.yml"
LIFECYCLE_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "publish-lifecycle-inputs.yml"
README_ZH_PATH = PROJECT_ROOT / "README.zh-CN.md"
QPK_DEPENDENCY = (
    "quant-platform-kit @ "
    "git+https://github.com/QuantStrategyLab/QuantPlatformKit.git@7f140f07ac89f0b4b88347a903906825dde11c39"
)
PINNED_ACTIONS = (
    "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6",
    "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7",
)
EXPECTED_REMOTE_ACTIONS = {
    WORKFLOW_PATH: {
        "actions/checkout": ("d23441a48e516b6c34aea4fa41551a30e30af803", "v6"),
        "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6"),
        "actions/upload-artifact": ("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7"),
        "actions/download-artifact": ("9000827ccba6bdab643e8b6fd33ac0654aef8333", "v8"),
        "google-github-actions/auth": ("7c6bc770dae815cd3e89ee6cdf493a5fab2cc093", "v3"),
    },
    LIFECYCLE_WORKFLOW_PATH: {
        "actions/checkout": ("d23441a48e516b6c34aea4fa41551a30e30af803", "v6"),
        "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6"),
        "actions/upload-artifact": ("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7"),
    },
}


class MonthlyPublishWorkflowConfigTests(unittest.TestCase):
    def test_publish_targets_use_vars_only(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("actions: write", workflow)
        self.assertIn(PINNED_ACTIONS[0], workflow)
        self.assertIn("google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3", workflow)
        self.assertIn(PINNED_ACTIONS[2], workflow)
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
        # The retired AIAuditBridge Codex Audit dispatch must not return.
        for retired in (
            "CODEX_AUDIT_",
            "AIAuditBridge",
            "codex_audit.yml",
            "Trigger Monthly Review Automation",
            "create-github-app-token",
            "permission-actions: write",
        ):
            self.assertNotIn(retired, workflow)
        self.assertIn("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", workflow)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", workflow)

    def test_review_jobs_have_no_publish_cloud_or_repository_write_permissions(self) -> None:
        jobs = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]
        preflight = jobs["monthly-review-preflight"]
        review = jobs["monthly-dual-api-review"]
        self.assertEqual(preflight["permissions"], {"contents": "read", "issues": "write"})
        self.assertEqual(review["permissions"], {"contents": "read", "actions": "read", "issues": "write", "id-token": "write"})
        self.assertIn("github.ref_name == 'main'", preflight["if"])
        self.assertEqual(review["if"], "needs.monthly-review-preflight.outputs.ready == 'true'")
        self.assertEqual(review["concurrency"], {"group": "monthly-dual-api-review-budget", "cancel-in-progress": False})
        self.assertEqual(review["timeout-minutes"], 20)
        for job in (preflight, review):
            self.assertEqual(job["runs-on"], "ubuntu-latest")
            checkout = job["steps"][0]
            self.assertFalse(checkout["with"]["persist-credentials"])
            text = str(job)
            for forbidden in ("google-github-actions", "git push", "codex exec", "cursor", "review_and_fix"):
                self.assertNotIn(forbidden, text)
        self.assertNotIn("id-token", str(preflight))

    def test_privileged_workflows_pin_remote_actions_to_full_commit_shas(self) -> None:
        uses_pattern = re.compile(r"^\s*uses:\s*([^\s#]+)(?:\s+#\s*([^\s#]+))?\s*$", re.MULTILINE)
        for workflow_path, expected in EXPECTED_REMOTE_ACTIONS.items():
            workflow = workflow_path.read_text(encoding="utf-8")
            matches = uses_pattern.findall(workflow)
            observed = {}
            for reference, tag in matches:
                if reference.startswith("./"):
                    continue
                action, separator, revision = reference.rpartition("@")
                self.assertEqual(separator, "@", f"invalid remote action reference in {workflow_path}")
                self.assertRegex(revision, r"^[0-9a-f]{40}$")
                self.assertRegex(tag, r"^v\d+$")
                if action in observed:
                    self.assertEqual(observed[action], (revision, tag))
                observed[action] = (revision, tag)

            self.assertEqual(observed, expected)

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

        self.assertIn("必须从 GitHub variable 读取", readme)
        self.assertIn("本仓库不再保留 source-local `ai_review.yml`", readme)
        self.assertIn("不再触发 AIAuditBridge", readme)
        self.assertIn("16,000", readme)
        self.assertIn("review-only", readme)
        self.assertNotIn("CODEX_AUDIT_PROVIDER", readme)
        self.assertNotIn("CODEX_AUDIT_BRIDGE_REF", readme)


if __name__ == "__main__":
    unittest.main()
