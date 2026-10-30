from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.binance_client import BinanceSpotClient


def kline(day, close):
    start = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    return [start, "100", "120", "90", str(close), "1", start + 86400000 - 1,
            "100", "2", "1", "100", "0"]


class BinanceDailyHistoryTests(unittest.TestCase):
    def client(self, root):
        client = BinanceSpotClient.__new__(BinanceSpotClient)
        client.config = types.SimpleNamespace(kline_limit=1000, requests_sleep_seconds=0)
        client.paths = types.SimpleNamespace(raw_dir=root)
        client.logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
        return client

    def test_open_day_not_stored_then_fetched_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = self.client(root)
            payload = [kline("2026-10-29", 101), kline("2026-10-30", 102)]
            requests = []
            client._request = lambda endpoint, params: requests.append(params) or payload
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-30T12:00:00Z")):
                first = client.update_symbol_history("BTCUSDT", "2026-10-29")
            self.assertEqual(first["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-29"])
            self.assertIn("open_time", first.columns)
            self.assertIn("close_time", first.columns)
            self.assertIn("received_at", first.columns)
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T00:00:01Z")):
                second = client.update_symbol_history("BTCUSDT", "2026-10-29")
            self.assertEqual(second["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-29", "2026-10-30"])
            self.assertEqual(requests[-1]["startTime"], int(pd.Timestamp("2026-10-29", tz="UTC").timestamp() * 1000))
            self.assertGreater(int(second.iloc[-1]["received_at"]), int(second.iloc[-1]["close_time"]))

    def test_legacy_last_row_is_replaced_by_refetched_closed_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame({"date": ["2026-10-29", "2026-10-30"], "symbol": ["BTCUSDT"] * 2,
                          "close": [90, 91]}).to_csv(root / "BTCUSDT.csv", index=False)
            client = self.client(root)
            client._request = lambda endpoint, params: [kline("2026-10-30", 105)]
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T01:00:00Z")):
                result = client.update_symbol_history("BTCUSDT", "2026-10-01")
            self.assertEqual(result["close"].tolist(), [90, 105])
            self.assertTrue(pd.isna(result.iloc[0]["close_time"]))
            self.assertEqual(int(result.iloc[1]["close_time"]), kline("2026-10-30", 105)[6])

    def test_unverified_existing_tail_is_retained_when_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "BTCUSDT.csv"
            pd.DataFrame({"date": ["2026-10-30"], "symbol": ["BTCUSDT"], "close": [91]}).to_csv(path, index=False)
            client = self.client(root)
            client._request = lambda endpoint, params: [kline("2026-10-30", 105)]
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-30T12:00:00Z")):
                result = client.update_symbol_history("BTCUSDT", "2026-10-01")
            self.assertEqual(result.iloc[-1]["close"], 91)
            self.assertTrue("received_at" not in result or pd.isna(result.iloc[-1]["received_at"]))

    def test_empty_or_failed_refresh_keeps_verified_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = self.client(root)
            client._request = lambda endpoint, params: [kline("2026-10-29", 101)]
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-30T12:00:00Z")):
                client.update_symbol_history("BTCUSDT", "2026-10-29")
            path = root / "BTCUSDT.csv"
            original = path.read_bytes()
            client._request = lambda endpoint, params: []
            client.update_symbol_history("BTCUSDT", "2026-10-29")
            self.assertEqual(float(pd.read_csv(path).iloc[-1]["close"]), 101)
            def failed(endpoint, params):
                raise OSError("synthetic failure")
            client._request = failed
            with self.assertRaises(OSError):
                client.update_symbol_history("BTCUSDT", "2026-10-29")
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_exchange_bar_times_are_rejected(self):
        client = self.client(Path("."))
        bad = kline("2026-10-29", 101)
        bad[6] -= 1
        client._request = lambda endpoint, params: [bad]
        with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-30T12:00:00Z")):
            with self.assertRaises(ValueError):
                client.get_klines("BTCUSDT", "2026-10-29")

    def test_bar_not_closed_at_exact_recorded_close_millisecond(self):
        client = self.client(Path("."))
        bar = kline("2026-10-29", 101)
        client._request = lambda endpoint, params: [bar]
        instant = pd.Timestamp(bar[6], unit="ms", tz="UTC")
        with patch("src.binance_client._utc_now", return_value=instant):
            self.assertTrue(client.get_klines("BTCUSDT", "2026-10-29").empty)

    def test_out_of_range_page_does_not_replace_existing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = self.client(root)
            client._request = lambda endpoint, params: [kline("2026-10-27", 55), kline("2026-10-29", 105)]
            path = root / "BTCUSDT.csv"
            pd.DataFrame({"date": ["2026-10-27", "2026-10-28"], "symbol": ["BTCUSDT"] * 2,
                          "close": [100, 101]}).to_csv(path, index=False)
            original = path.read_bytes()
            with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T00:00:01Z")):
                with self.assertRaises(ValueError):
                    client.update_symbol_history("BTCUSDT", "2026-10-01", "2026-10-30")
            self.assertEqual(path.read_bytes(), original)

    def test_repeated_page_is_rejected_instead_of_looping(self):
        client = self.client(Path("."))
        client.config.kline_limit = 1
        calls = []
        def request(endpoint, params):
            calls.append(dict(params))
            if len(calls) > 2:
                raise AssertionError("pagination failed to terminate")
            return [kline("2026-10-28", 101)]
        client._request = request
        with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T00:00:01Z")):
            with self.assertRaises(ValueError):
                client.get_klines("BTCUSDT", "2026-10-28")
        self.assertEqual(len(calls), 2)

    def test_duplicate_and_descending_rows_and_end_bound_are_rejected(self):
        cases = ([kline("2026-10-29", 101), kline("2026-10-29", 102)],
                 [kline("2026-10-29", 101), kline("2026-10-28", 102)],
                 [kline("2026-10-30", 101)])
        for payload in cases:
            with self.subTest(payload=payload):
                client = self.client(Path("."))
                client._request = lambda endpoint, params: payload
                with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T00:00:01Z")):
                    with self.assertRaises(ValueError):
                        client.get_klines("BTCUSDT", "2026-10-28", "2026-10-29")

    def test_non_btc_sparse_history_is_not_forced_to_be_continuous(self):
        client = self.client(Path("."))
        client._request = lambda endpoint, params: [kline("2026-10-27", 101), kline("2026-10-29", 102)]
        with patch("src.binance_client._utc_now", return_value=pd.Timestamp("2026-10-31T00:00:01Z")):
            result = client.get_klines("ETHUSDT", "2026-10-27", "2026-10-29")
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-27", "2026-10-29"])


if __name__ == "__main__":
    unittest.main()
