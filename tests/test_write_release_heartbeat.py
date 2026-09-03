from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "write_release_heartbeat.py"
SPEC = importlib.util.spec_from_file_location("write_release_heartbeat", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class WriteReleaseHeartbeatTests(unittest.TestCase):
    def test_candidate_heartbeat_uses_versioned_release_without_activation_targets(self) -> None:
        manifest = {
            "stage": "candidate",
            "version": "2026-03-13-core_major",
            "mode": "core_major",
            "as_of_date": "2026-03-13",
            "release_prefix": "crypto-live-pool-pipelines/releases/2026-03-13-core_major",
            "artifacts": {
                "live_pool": {
                    "release_uri": "gs://bucket/crypto-live-pool-pipelines/releases/2026-03-13-core_major/live_pool.json"
                }
            },
        }
        live_pool = {
            "as_of_date": "2026-03-13",
            "mode": "core_major",
            "pool_size": 5,
            "symbols": ["BTCUSDT", "ETHUSDT"],
        }

        heartbeat = MODULE.build_heartbeat(
            manifest,
            live_pool,
            run_id="123",
            run_url="https://example.test/run/123",
        )

        self.assertEqual(heartbeat["stage"], "candidate")
        self.assertEqual(
            heartbeat["storage_prefix"],
            "gs://bucket/crypto-live-pool-pipelines/releases/2026-03-13-core_major",
        )
        self.assertNotIn("current_prefix", heartbeat)
        self.assertNotIn("firestore", heartbeat)


if __name__ == "__main__":
    unittest.main()
