from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

import pandas as pd

from src import btc_evidence as evidence


def synthetic_page():
    start = pd.Timestamp("2026-03-24T00:00:00Z")
    rows = []
    for i in range(220):
        opened = int((start + pd.Timedelta(days=i)).timestamp() * 1000)
        rows.append([opened, "100", "120", "90", str(100 + i), "1", opened + 86400000 - 1,
                     "100", 2, "1", "100", "0"])
    return json.dumps(rows).encode()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "bundle"
        self.now = pd.Timestamp("2026-10-30T12:00:00Z")
        self.source_patch = patch.object(evidence, "_source", return_value="a" * 40)
        self.versions_patch = patch.object(evidence, "_versions", return_value={"numpy": "test", "pandas": "test", "requests": "test"})
        self.source_patch.start()
        self.versions_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.versions_patch.stop)

    def bundle(self):
        with patch.object(evidence, "_now", return_value=self.now), patch.object(
            evidence, "_fetch", return_value=(synthetic_page(), self.now)
        ):
            return evidence.collect(self.directory)

    def test_collect_and_offline_replay(self):
        self.assertEqual(self.bundle()["status"], "verified_local_consistency")
        with patch.object(evidence, "_fetch", side_effect=AssertionError("offline verification accessed network")):
            self.assertEqual(evidence.verify(self.directory, decision_at=self.now)["source_authenticated"], False)
        envelope = json.loads((self.directory / "btc-cycle-input.json").read_text())
        self.assertEqual(set(envelope), {"contract_version", "source", "source_version", "symbol", "observed_at", "available_at", "ahr999"})
        self.assertEqual(envelope["source_version"], "a" * 40)

    def test_tamper_and_time_fail_closed(self):
        self.bundle()
        manifest_path = self.directory / "manifest.json"
        original = json.loads(manifest_path.read_text())
        cases = [{"source_revision": "b" * 40}, {"computed_at": "2026-10-31T00:00:00Z"},
                 {"source_authenticated": True}, {"formula_version": "other"}, {"endpoint": "https://evil.invalid"}]
        for change in cases:
            with self.subTest(change=change):
                manifest_path.write_bytes(evidence._encode({**original, **change}))
                with self.assertRaises(ValueError):
                    evidence.verify(self.directory, decision_at=self.now)
        manifest_path.write_bytes(evidence._encode(original))
        page = self.directory / "page-000.json"
        raw = page.read_bytes()
        page.write_bytes(raw + b" ")
        with self.assertRaises(ValueError):
            evidence.verify(self.directory, decision_at=self.now)
        page.write_bytes(raw)
        with self.assertRaises(ValueError):
            evidence.verify(self.directory, decision_at=self.now - pd.Timedelta(seconds=1))
        with self.assertRaises(ValueError):
            evidence.verify(self.directory, decision_at=self.now + pd.Timedelta(days=2))

    def test_rehashed_payload_or_envelope_tamper_fails_recalculation(self):
        self.bundle()
        manifest_path = self.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        envelope_path = self.directory / "btc-cycle-input.json"
        envelope = json.loads(envelope_path.read_text())
        envelope["ahr999"] *= 2
        envelope_raw = evidence._encode(envelope)
        envelope_path.write_bytes(envelope_raw)
        manifest["envelope_sha256"] = hashlib.sha256(envelope_raw).hexdigest()
        manifest_path.write_bytes(evidence._encode(manifest))
        with self.assertRaises(ValueError):
            evidence.verify(self.directory, decision_at=self.now)

    def test_rehashed_raw_bar_tamper_fails_recalculation(self):
        self.bundle()
        page_path, manifest_path = self.directory / "page-000.json", self.directory / "manifest.json"
        rows = json.loads(page_path.read_text())
        rows[-1][4] = "9999"
        raw = json.dumps(rows).encode()
        page_path.write_bytes(raw)
        manifest = json.loads(manifest_path.read_text())
        manifest["page_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_bytes(evidence._encode(manifest))
        with self.assertRaises(ValueError):
            evidence.verify(self.directory, decision_at=self.now)

    def test_invalid_daily_data_rejected(self):
        rows = json.loads(synthetic_page())
        for bad in (rows[:-1], rows[:-1] + [rows[-2]], rows[:100] + rows[101:] + [rows[-1]],
                    rows[:219] + [[*rows[-1][:4], "NaN", *rows[-1][5:]]]):
            with self.subTest(count=len(bad)), self.assertRaises(ValueError):
                evidence._compute(json.dumps(bad).encode(), start=pd.Timestamp("2026-03-24"),
                                  end=pd.Timestamp("2026-10-29"), received=self.now)

    def test_http_rejects_redirect_oversize_and_disabled_tls(self):
        params = {"symbol": "BTCUSDT", "interval": "1d", "startTime": 1, "endTime": 2, "limit": 220}
        class Request:
            def __init__(self, method, url, params):
                self.url = url + "?" + urlencode(params)
                self.headers = {}
            def prepare(self):
                return self
        class Response:
            def __init__(self, status, raw, url):
                self.status_code, self.raw, self.url = status, raw, url
            def iter_content(self, chunk_size):
                yield self.raw
            def close(self):
                pass
        for status, size, tls in ((302, 1, True), (200, evidence.MAX_BODY + 1, True), (200, 1, False)):
            with self.subTest(status=status, size=size, tls=tls):
                class Session:
                    def merge_environment_settings(self, url, proxies, stream, verify, cert):
                        return {"stream": True, "verify": tls}
                    def send(self, request, **kwargs):
                        return Response(status, b"x" * size, request.url)
                    def close(self):
                        pass
                fake = types.SimpleNamespace(Request=Request, Session=Session)
                with patch.dict("sys.modules", {"requests": fake}), self.assertRaises(ValueError):
                    evidence._fetch(params)


class SourceBindingTests(unittest.TestCase):
    def test_source_requires_tracked_code_and_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ("src/btc_evidence.py", "src/binance_client.py", "src/pipeline.py", "src/indicators.py",
                     "scripts/btc_cycle_evidence.py", "requirements-lock.txt")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n")
            subprocess.run(["git", "add", *paths[:-1]], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "commit", "-qm", "synthetic"], cwd=root, check=True)
            with patch.object(evidence, "ROOT", root):
                with self.assertRaises(RuntimeError):
                    evidence._source()
                subprocess.run(["git", "add", paths[-1]], cwd=root, check=True)
                subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                "commit", "-qm", "lock"], cwd=root, check=True)
                self.assertEqual(len(evidence._source()), 40)
                (root / paths[0]).write_text("dirty\n")
                with self.assertRaises(RuntimeError):
                    evidence._source()


if __name__ == "__main__":
    unittest.main()
