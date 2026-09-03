from __future__ import annotations

import base64
import shlex
import unittest
from pathlib import Path

from scripts.classify_cc0_native_auto_merge import classify


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cc0_native_auto_merge.yml"
REPOSITORY = "QuantStrategyLab/CryptoLivePoolPipelines"
BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40


def _content(text: str = "# Operator notes\n") -> dict[str, object]:
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(raw),
        "content": base64.b64encode(raw).decode("ascii"),
        "sha": "3" * 40,
        "path": "docs/operator-notes.md",
        "submodule_git_url": None,
        "target": None,
    }


def _eligible_context() -> dict[str, object]:
    return {
        "repository": {
            "name_with_owner": REPOSITORY,
            "is_fork": False,
            "default_branch": "main",
            "default_branch_oid": BASE_SHA,
            "auto_merge_allowed": True,
            "squash_merge_allowed": True,
            "protection": {
                "pattern": "main",
                "requires_status_checks": True,
                "requires_strict_status_checks": True,
                "requires_conversation_resolution": True,
                "required_status_checks": [
                    {"context": "test", "app_id": 15368, "app_slug": "github-actions"}
                ],
            },
        },
        "workflow_run": {
            "action": "completed",
            "workflow_id": 253773518,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "codex/monthly-review-issue-42-20260903010101",
            "head_sha": HEAD_SHA,
            "repository": REPOSITORY,
            "repository_is_fork": False,
            "head_repository": REPOSITORY,
            "head_repository_is_fork": False,
            "actor_login": "quantcrossrepoautomation[bot]",
            "actor_type": "Bot",
            "triggering_actor_login": "quantcrossrepoautomation[bot]",
            "triggering_actor_type": "Bot",
            "pull_requests": [
                {
                    "number": 42,
                    "head_ref": "codex/monthly-review-issue-42-20260903010101",
                    "head_sha": HEAD_SHA,
                    "base_ref": "main",
                    "base_sha": BASE_SHA,
                }
            ],
        },
        "pull_request": {
            "number": 42,
            "state": "OPEN",
            "is_draft": False,
            "author_login": "quantcrossrepoautomation",
            "author_type": "Bot",
            "base_ref": "main",
            "base_oid": BASE_SHA,
            "base_repository": REPOSITORY,
            "base_repository_is_fork": False,
            "head_ref": "codex/monthly-review-issue-42-20260903010101",
            "head_oid": HEAD_SHA,
            "head_repository": REPOSITORY,
            "head_repository_is_fork": False,
            "is_cross_repository": False,
            "mergeable": "MERGEABLE",
            "merge_state_status": "CLEAN",
            "auto_merge_request": None,
            "changed_files": 1,
            "review_threads_complete": True,
            "review_threads": [{"is_resolved": True}],
        },
        "files": [
            {
                "path": "docs/operator-notes.md",
                "status": "modified",
                "previous_path": None,
                "additions": 1,
                "deletions": 1,
                "changes": 2,
                "sha": "3" * 40,
                "content": _content(),
            }
        ],
    }


def _set(context: dict[str, object], dotted_path: str, value: object) -> None:
    target: dict[str, object] = context
    keys = dotted_path.split(".")
    for key in keys[:-1]:
        target = target[key]  # type: ignore[assignment]
    target[keys[-1]] = value


class Cc0NativeAutoMergeTests(unittest.TestCase):
    def assert_rejected(self, cases: list[tuple[str, object]]) -> None:
        for field, value in cases:
            with self.subTest(field=field, value=value):
                context = _eligible_context()
                _set(context, field, value)
                self.assertFalse(classify(context)["eligible"])

    def test_accepts_complete_docs_only_cc0_contract(self) -> None:
        self.assertEqual(
            classify(_eligible_context()),
            {"eligible": True, "reason": "eligible_cc0", "pr_number": 42, "head_sha": HEAD_SHA},
        )

    def test_does_not_require_duplicate_ruleset_snapshot(self) -> None:
        context = _eligible_context()
        self.assertTrue(classify(context)["eligible"])

    def test_rejects_identity_or_merge_gate_mismatch(self) -> None:
        self.assert_rejected(
            [
                ("repository.is_fork", True),
                ("repository.default_branch", "develop"),
                ("repository.auto_merge_allowed", False),
                ("repository.squash_merge_allowed", False),
                ("workflow_run.action", "requested"),
                ("workflow_run.workflow_id", 999),
                ("workflow_run.name", "Other CI"),
                ("workflow_run.path", ".github/workflows/other.yml"),
                ("workflow_run.event", "push"),
                ("workflow_run.status", "in_progress"),
                ("workflow_run.conclusion", "failure"),
                ("workflow_run.repository", "attacker/fork"),
                ("workflow_run.repository_is_fork", True),
                ("workflow_run.head_repository", "attacker/fork"),
                ("workflow_run.head_repository_is_fork", True),
                ("workflow_run.actor_login", "Pigbibi"),
                ("workflow_run.actor_type", "User"),
                ("workflow_run.triggering_actor_login", "Pigbibi"),
                ("workflow_run.triggering_actor_type", "User"),
                ("pull_request.state", "CLOSED"),
                ("pull_request.is_draft", True),
                ("pull_request.author_login", "Pigbibi"),
                ("pull_request.author_type", "User"),
                ("pull_request.base_ref", "release"),
                ("pull_request.base_repository", "attacker/fork"),
                ("pull_request.base_repository_is_fork", True),
                ("pull_request.head_ref", "codex/not-an-allowlisted-bot-branch"),
                ("pull_request.head_repository", "attacker/fork"),
                ("pull_request.head_repository_is_fork", True),
                ("pull_request.is_cross_repository", True),
                ("pull_request.mergeable", "UNKNOWN"),
                ("pull_request.merge_state_status", "BEHIND"),
                ("pull_request.auto_merge_request", {"enabledAt": "now"}),
            ]
        )

    def test_rejects_non_strict_ci_or_unresolved_review_state(self) -> None:
        self.assert_rejected(
            [
                ("repository.protection.requires_status_checks", False),
                ("repository.protection.requires_strict_status_checks", False),
                ("repository.protection.requires_conversation_resolution", False),
                ("repository.protection.required_status_checks", []),
                (
                    "repository.protection.required_status_checks",
                    [{"context": "test", "app_id": None, "app_slug": None}],
                ),
                (
                    "repository.protection.required_status_checks",
                    [{"context": "lint", "app_id": 15368, "app_slug": "github-actions"}],
                ),
                (
                    "repository.protection.required_status_checks",
                    [
                        {"context": "test", "app_id": 15368, "app_slug": "github-actions"},
                        {"context": "lint", "app_id": 15368, "app_slug": "github-actions"},
                    ],
                ),
                ("pull_request.review_threads_complete", False),
                ("pull_request.review_threads", [{"is_resolved": False}]),
            ]
        )

    def test_rejects_non_cc0_path_or_file_operation(self) -> None:
        for path, status in [
            ("tests/test_docs.py", "modified"),
            (".github/workflows/ci.yml", "modified"),
            ("requirements-lock.txt", "modified"),
            ("src/risk.py", "modified"),
            ("data/report.md", "modified"),
            ("docs/data/report.md", "modified"),
            ("docs/generated/report.md", "modified"),
            ("docs/chart.png", "modified"),
            ("docs/../.github/workflows/ci.yml", "modified"),
            ("docs/operator-notes.md", "removed"),
            ("docs/operator-notes.md", "renamed"),
            ("docs/operator-notes.md", "copied"),
        ]:
            with self.subTest(path=path, status=status):
                context = _eligible_context()
                file_info = context["files"][0]  # type: ignore[index]
                file_info["path"] = path
                file_info["status"] = status
                file_info["content"]["path"] = path
                self.assertFalse(classify(context)["eligible"])

    def test_rejects_unknown_binary_or_non_regular_file_metadata(self) -> None:
        for field, value in [
            ("previous_path", "docs/old.md"),
            ("sha", "not-a-sha"),
            ("content.type", "submodule"),
            ("content.encoding", "none"),
            ("content.size", 999),
            ("content.sha", "4" * 40),
            ("content.submodule_git_url", "https://example.invalid/submodule.git"),
            ("content.target", "../README.md"),
        ]:
            with self.subTest(field=field, value=value):
                context = _eligible_context()
                target = context["files"][0]  # type: ignore[index]
                if field.startswith("content."):
                    _set(target, field, value)
                else:
                    target[field] = value
                self.assertFalse(classify(context)["eligible"])

    def test_rejects_binary_or_generated_content(self) -> None:
        for raw_content in [
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xfe\x00\x00",
            b"<!-- AUTO-GENERATED; DO NOT EDIT -->\n",
        ]:
            with self.subTest(raw_content=raw_content):
                context = _eligible_context()
                content = context["files"][0]["content"]  # type: ignore[index]
                content["size"] = len(raw_content)
                content["content"] = base64.b64encode(raw_content).decode("ascii")
                self.assertFalse(classify(context)["eligible"])

    def test_rejects_incomplete_or_unknown_metadata(self) -> None:
        context = _eligible_context()
        del context["pull_request"]["mergeable"]  # type: ignore[index]
        self.assertEqual(classify(context), {"eligible": False, "reason": "unknown_or_invalid_metadata"})

        context = _eligible_context()
        context["pull_request"]["changed_files"] = 2  # type: ignore[index]
        self.assertFalse(classify(context)["eligible"])

        context = _eligible_context()
        context["workflow_run"]["pull_requests"] = []  # type: ignore[index]
        self.assertFalse(classify(context)["eligible"])

    def test_workflow_uses_trusted_default_branch_code_and_native_auto_merge(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn('workflows: ["CI"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertEqual(workflow.count("permissions:"), 1)
        self.assertIn("permissions:\n  contents: write\n  pull-requests: write", workflow)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", workflow)
        self.assertIn("ref: ${{ github.workflow_sha }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn(
            "github.event.workflow_run.head_sha",
            workflow.split("with:", 1)[1].split("- name:", 1)[0],
        )

        merge_line = next(line.strip() for line in workflow.splitlines() if line.strip().startswith("gh pr merge"))
        self.assertEqual(
            shlex.split(merge_line),
            [
                "gh",
                "pr",
                "merge",
                "${PR_NUMBER}",
                "--repo",
                "${GITHUB_REPOSITORY}",
                "--auto",
                "--squash",
                "--match-head-commit",
                "${HEAD_SHA}",
            ],
        )
