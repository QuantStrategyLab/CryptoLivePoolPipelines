import ast
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from scripts.monthly_review_loop import apply_monthly_loop


ROOT = Path(__file__).resolve().parents[1]


class HookBindingTests(unittest.TestCase):
    def test_one_shadow_target_and_only_environment_references(self):
        binding = json.loads((ROOT / "config/ai-hook-shadow.json").read_text())
        self.assertEqual(set(binding), {"schema", "service_target", "mode", "purpose", "environment"})
        self.assertEqual(binding["service_target"], "quant-ai-service-v2")
        self.assertEqual(binding["mode"], "shadow")
        self.assertEqual(binding["purpose"], "research")
        self.assertTrue(all(value.startswith("AI_") for value in binding["environment"].values()))

    def test_hook_has_no_import_or_calls_to_issue_remote_or_actuator(self):
        tree = ast.parse(inspect.getsource(apply_monthly_loop))
        self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)))
        called = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertTrue(called.isdisjoint({"dot.submit", "dot.result", "issue.post", "issue.auto_close",
                                          "finish_review", "DotGateway", "pause_cycle"}))
        self.assertFalse(any(name.startswith(("issue.", "dot.")) for name in called))

    def test_no_formal_or_unknown_target_setting_can_enable_work(self):
        for changes in ({"AI_HOOK_MODE": "live"}, {"AI_SERVICE_TARGET": "other"}, {}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "record.json"
                record = apply_monthly_loop(None, [(role, {"verdict": "approve", "severity": "none", "summary": "ok"})
                                                   for role in ("OpenAI", "Anthropic")],
                                            identity="test", materials="do-not-save", archive=path, env=changes)
                self.assertEqual(record["binding_status"], "blocked_configuration")
                self.assertFalse(record["formal_actions_enabled"])
                self.assertFalse(record["ai_submitted"])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("do-not-save", path.read_text())
