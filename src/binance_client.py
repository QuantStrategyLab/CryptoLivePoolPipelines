from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from .utils import date_to_str, get_logger, read_json, to_timestamp, write_json


BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


@dataclass
class BinanceClientConfig:
    base_url: str
    timeout_seconds: int
    kline_limit: int
    exchange_info_cache_ttl_hours: int
    requests_sleep_seconds: float


class BinanceSpotClient:
    """Minimal Binance Spot public client with local caching and CSV storage."""

    def __init__(self, config: dict[str, Any], paths: Any) -> None:
        self.logger = get_logger(self.__class__.__name__)
        client_cfg = config["binance"]
        self.config = BinanceClientConfig(**client_cfg)
        self.paths = paths
        self.session = requests.Session()

    def _request(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.config.base_url}{endpoint}"
        response = self.session.get(url, params=params, timeout=self.config.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def get_exchange_info(self, force_refresh: bool = False) -> dict[str, Any]:
        """Load exchange info from cache if fresh, otherwise fetch from Binance."""
        cache_path = Path(self.paths.cache_dir) / "exchange_info.json"
        cached = read_json(cache_path)
        now = pd.Timestamp.now(tz="UTC")
        ttl = pd.Timedelta(hours=self.config.exchange_info_cache_ttl_hours)
        if not force_refresh and cached is not None:
            fetched_at = cached.get("_fetched_at")
            if fetched_at and now - pd.Timestamp(fetched_at) <= ttl:
                return cached

        payload = self._request("/api/v3/exchangeInfo")
        payload["_fetched_at"] = now.isoformat()
        write_json(cache_path, payload)
        return payload

    def get_symbol_metadata(self, force_refresh: bool = False) -> pd.DataFrame:
        """Return symbol metadata as a dataframe and persist a local CSV snapshot."""
        exchange_info = self.get_exchange_info(force_refresh=force_refresh)
        symbols = exchange_info.get("symbols", [])
        rows = []
        for item in symbols:
            rows.append(
                {
                    "symbol": item["symbol"],
                    "status": item.get("status"),
                    "base_asset": item.get("baseAsset"),
                    "quote_asset": item.get("quoteAsset"),
                    "is_spot_trading_allowed": bool(item.get("isSpotTradingAllowed")),
                    "permissions": ",".join(item.get("permissions", [])),
                }
            )
        metadata = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
        metadata_path = Path(self.paths.cache_dir) / "symbol_metadata.csv"
        metadata.to_csv(metadata_path, index=False)
        return metadata

    def get_24h_ticker_stats(self) -> pd.DataFrame:
        """Fetch current 24h ticker stats for all Binance Spot symbols."""
        payload = self._request("/api/v3/ticker/24hr")
        if isinstance(payload, dict):
            payload = [payload]

        rows = []
        for item in payload:
            rows.append(
                {
                    "symbol": item.get("symbol"),
                    "quote_volume_24h": pd.to_numeric(item.get("quoteVolume"), errors="coerce"),
                    "base_volume_24h": pd.to_numeric(item.get("volume"), errors="coerce"),
                    "trade_count_24h": pd.to_numeric(item.get("count"), errors="coerce"),
                    "weighted_avg_price_24h": pd.to_numeric(item.get("weightedAvgPrice"), errors="coerce"),
                }
            )
        return pd.DataFrame(rows)

    def _normalize_kline_payload(self, symbol: str, payload: list[list[Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(payload, columns=BINANCE_KLINE_COLUMNS)
        frame["date"] = pd.to_datetime(frame["open_time"], unit="ms").dt.normalize()
        frame["symbol"] = symbol
        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trade_count",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ]
        for column in numeric_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        keep_columns = [
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trade_count",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        ]
        return frame[keep_columns].sort_values("date").reset_index(drop=True)

    def get_klines(
        self,
        symbol: str,
        start_date: str | pd.Timestamp,
        end_date: Optional[str | pd.Timestamp] = None,
    ) -> pd.DataFrame:
        """Fetch daily klines between two dates using Binance's paginated API."""
        start_ts = int(to_timestamp(start_date).timestamp() * 1000)
        end_ts = None
        if end_date is not None:
            end_ts = int((to_timestamp(end_date) + pd.Timedelta(days=1)).timestamp() * 1000)

        frames: list[pd.DataFrame] = []
        current_start = start_ts
        while True:
            params = {
                "symbol": symbol,
                "interval": "1d",
                "startTime": current_start,
                "limit": self.config.kline_limit,
            }
            if end_ts is not None:
                params["endTime"] = end_ts

            payload = self._request("/api/v3/klines", params=params)
            if not payload:
                break

            frame = self._normalize_kline_payload(symbol, payload)
            frames.append(frame)
            last_open_ms = int(payload[-1][0])
            next_start = last_open_ms + 24 * 60 * 60 * 1000
            if len(payload) < self.config.kline_limit or (end_ts is not None and next_start >= end_ts):
                break
            current_start = next_start
            time.sleep(self.config.requests_sleep_seconds)

        if not frames:
            return pd.DataFrame(
                columns=[
                    "date",
                    "symbol",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "quote_volume",
                    "trade_count",
                    "taker_buy_base_volume",
                    "taker_buy_quote_volume",
                ]
            )
        return pd.concat(frames, ignore_index=True).drop_duplicates("date", keep="last")

    def update_symbol_history(
        self,
        symbol: str,
        start_date: str | pd.Timestamp,
        end_date: Optional[str | pd.Timestamp] = None,
    ) -> pd.DataFrame:
        """Incrementally update a symbol CSV in data/raw/."""
        output_path = Path(self.paths.raw_dir) / f"{symbol}.csv"
        existing = None
        fetch_start = to_timestamp(start_date)
        if output_path.exists():
            existing = pd.read_csv(output_path)
            if not existing.empty:
                existing["date"] = pd.to_datetime(existing["date"]).dt.normalize()
                last_date = existing["date"].max()
                fetch_start = max(fetch_start, last_date + pd.Timedelta(days=1))

        if end_date is not None and fetch_start > to_timestamp(end_date):
            self.logger.info("Skipping %s because local history is already current.", symbol)
            return existing if existing is not None else pd.DataFrame()

        downloaded = self.get_klines(symbol=symbol, start_date=fetch_start, end_date=end_date)
        if downloaded.empty and existing is not None:
            combined = existing.copy()
        elif existing is not None and not existing.empty:
            combined = pd.concat([existing, downloaded], ignore_index=True)
        else:
            combined = downloaded
        if combined.empty:
            self.logger.warning("No history downloaded for %s.", symbol)
            return combined

        combined = combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        combined.to_csv(output_path, index=False)
        self.logger.info(
            "Saved %s rows for %s into %s (through %s).",
            len(combined),
            symbol,
            output_path,
            date_to_str(combined["date"].max()),
        )
        return combined

    def sync_history(
        self,
        symbols: list[str],
        start_date: str | pd.Timestamp,
        end_date: Optional[str | pd.Timestamp] = None,
    ) -> None:
        """Download or update a list of symbol histories."""
        for position, symbol in enumerate(symbols, start=1):
            self.logger.info("Downloading %s/%s: %s", position, len(symbols), symbol)
            try:
                self.update_symbol_history(symbol, start_date=start_date, end_date=end_date)
            except requests.HTTPError as exc:
                self.logger.error("Failed to download %s: %s", symbol, exc)
            time.sleep(self.config.requests_sleep_seconds)
