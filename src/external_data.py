from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .utils import clean_numeric_frame, get_logger


CANONICAL_HISTORY_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
]

MERGE_HISTORY_ROLES = {"history", "pre_binance_history", "alternate_exchange_history"}
CROSSCHECK_HISTORY_ROLES = {"crosscheck_history"}


@dataclass(frozen=True)
class ExternalProviderConfig:
    name: str
    provider_type: str
    source_name: str
    enabled: bool
    merge_role: str
    directory: Path | None = None
    path: Path | None = None
    settings: dict[str, Any] = field(default_factory=dict)


class LocalCsvHistoryProvider:
    """Simple local-CSV provider used for pre-Binance or alternate exchange daily history."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg

    def load_history(self, symbol: str, as_of_date: pd.Timestamp | None = None) -> pd.DataFrame:
        if self.cfg.directory is None:
            return pd.DataFrame()
        path = self.cfg.directory / f"{symbol}.csv"
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(path)
        return normalize_external_history_frame(
            frame,
            symbol=symbol,
            source_name=self.cfg.source_name,
            provider_name=self.cfg.name,
            as_of_date=as_of_date,
        )


class LocalCsvMetadataProvider:
    """Small local-CSV metadata provider, e.g. market-cap snapshots."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg

    def load_metadata(self) -> pd.DataFrame:
        if self.cfg.path is None or not self.cfg.path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(self.cfg.path)
        if frame.empty:
            return frame
        frame["source_name"] = self.cfg.source_name
        frame["provider_name"] = self.cfg.name
        return frame


class CryptoCompareDailyHistoryProvider:
    """Fetch and cache daily USD history from CryptoCompare for pre-Binance backfill."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg
        self.base_url = str(cfg.settings.get("base_url", "https://min-api.cryptocompare.com/data/v2/histoday"))
        cache_dir = cfg.settings.get("cache_dir")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = int(cfg.settings.get("timeout_seconds", 30))
        self.batch_limit = min(2000, max(30, int(cfg.settings.get("batch_limit", 2000))))
        self.max_batches = max(1, int(cfg.settings.get("max_batches", 3)))
        self.requests_sleep_seconds = float(cfg.settings.get("requests_sleep_seconds", 0.25))
        self.refresh_days = max(0, int(cfg.settings.get("refresh_days", 7)))
        self.quote_asset = str(cfg.settings.get("quote_asset", "USD")).upper()
        self.min_date = pd.Timestamp(cfg.settings["min_date"]).normalize() if cfg.settings.get("min_date") else None
        self.symbols = {str(item).upper() for item in cfg.settings.get("symbols", [])}
        self.symbol_mapping = {
            str(symbol).upper(): str(mapped).upper()
            for symbol, mapped in dict(cfg.settings.get("symbol_mapping", {})).items()
        }
        self.api_key_env = cfg.settings.get("api_key_env")
        self.logger = get_logger("cryptocompare_provider")

    def load_history(self, symbol: str, as_of_date: pd.Timestamp | None = None) -> pd.DataFrame:
        symbol = str(symbol).upper()
        if self.symbols and symbol not in self.symbols:
            return pd.DataFrame()

        cached = self._load_cached(symbol)
        if _cache_is_fresh(cached, as_of_date, self.refresh_days):
            return normalize_external_history_frame(
                cached,
                symbol=symbol,
                source_name=self.cfg.source_name,
                provider_name=self.cfg.name,
                as_of_date=as_of_date,
            )

        fetched = self._fetch_remote_history(symbol, as_of_date)
        if fetched.empty and cached.empty:
            return pd.DataFrame()
        merged_cache = _merge_cache_frames(cached, fetched)
        if not merged_cache.empty and self.cache_dir is not None:
            merged_cache.to_csv(self.cache_dir / f"{symbol}.csv", index=False)
        return normalize_external_history_frame(
            merged_cache,
            symbol=symbol,
            source_name=self.cfg.source_name,
            provider_name=self.cfg.name,
            as_of_date=as_of_date,
        )

    def _load_cached(self, symbol: str) -> pd.DataFrame:
        if self.cache_dir is None:
            return pd.DataFrame()
        path = self.cache_dir / f"{symbol}.csv"
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(path)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        return frame

    def _fetch_remote_history(self, symbol: str, as_of_date: pd.Timestamp | None) -> pd.DataFrame:
        fsym = self.symbol_mapping.get(symbol)
        if fsym is None:
            fsym = symbol[:-4] if symbol.endswith("USDT") else symbol
        headers = {}
        api_key = os.getenv(str(self.api_key_env)) if self.api_key_env else None
        if api_key:
            headers["authorization"] = f"Apikey {api_key}"

        to_timestamp = (
            int(pd.Timestamp(as_of_date).normalize().timestamp())
            if as_of_date is not None
            else int(pd.Timestamp.now(tz="UTC").normalize().timestamp())
        )
        frames = []
        for batch_number in range(self.max_batches):
            params = {
                "fsym": fsym,
                "tsym": self.quote_asset,
                "limit": self.batch_limit,
                "toTs": to_timestamp,
            }
            response = requests.get(self.base_url, params=params, headers=headers, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if payload.get("Response") != "Success":
                raise ValueError(
                    f"CryptoCompare returned an unsuccessful payload for {symbol}: {payload.get('Message') or payload}"
                )
            batch_rows = payload.get("Data", {}).get("Data", [])
            if not batch_rows:
                break
            batch_frame = pd.DataFrame(batch_rows)
            batch_frame["date"] = pd.to_datetime(batch_frame["time"], unit="s").dt.normalize()
            batch_frame = batch_frame.rename(columns={"volumefrom": "volume", "volumeto": "quote_volume"})
            batch_frame = batch_frame.loc[
                batch_frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").sum(axis=1) > 0
            ].copy()
            if not batch_frame.empty:
                if self.min_date is not None:
                    batch_frame = batch_frame.loc[batch_frame["date"] >= self.min_date]
                frames.append(batch_frame[["date", "open", "high", "low", "close", "volume", "quote_volume"]])
                if self.min_date is not None and batch_frame["date"].min() <= self.min_date:
                    break
            if len(batch_rows) < 2:
                break
            earliest_row_time = int(batch_rows[0]["time"])
            to_timestamp = earliest_row_time - 86400
            if self.min_date is not None and pd.Timestamp(to_timestamp, unit="s").normalize() < self.min_date:
                break
            if batch_number + 1 < self.max_batches and self.requests_sleep_seconds > 0.0:
                time.sleep(self.requests_sleep_seconds)

        if not frames:
            self.logger.warning("CryptoCompare returned no usable external history for %s.", symbol)
            return pd.DataFrame()

        merged = pd.concat(frames, ignore_index=True)
        merged["date"] = pd.to_datetime(merged["date"]).dt.normalize()
        merged = merged.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        self.logger.info(
            "Fetched %s CryptoCompare rows for %s (%s -> %s).",
            len(merged),
            symbol,
            merged["date"].min().date(),
            merged["date"].max().date(),
        )
        return merged


class CoinGeckoMarketChartProvider:
    """Fetch and cache daily close history from CoinGecko for cross-check validation."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg
        self.base_url = str(cfg.settings.get("base_url", "https://api.coingecko.com/api/v3"))
        cache_dir = cfg.settings.get("cache_dir")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = int(cfg.settings.get("timeout_seconds", 30))
        self.refresh_days = max(0, int(cfg.settings.get("refresh_days", 7)))
        self.requests_sleep_seconds = float(cfg.settings.get("requests_sleep_seconds", 1.0))
        self.quote_asset = str(cfg.settings.get("quote_asset", "usd")).lower()
        self.min_date = pd.Timestamp(cfg.settings["min_date"]).normalize() if cfg.settings.get("min_date") else None
        self.coin_ids = {
            str(symbol).upper(): str(coin_id)
            for symbol, coin_id in dict(cfg.settings.get("coin_ids", {})).items()
        }
        self.logger = get_logger("coingecko_provider")

    def load_history(self, symbol: str, as_of_date: pd.Timestamp | None = None) -> pd.DataFrame:
        symbol = str(symbol).upper()
        coin_id = self.coin_ids.get(symbol)
        if not coin_id:
            return pd.DataFrame()

        cached = self._load_cached(symbol)
        if _cache_is_fresh(cached, as_of_date, self.refresh_days):
            return normalize_external_history_frame(
                cached,
                symbol=symbol,
                source_name=self.cfg.source_name,
                provider_name=self.cfg.name,
                as_of_date=as_of_date,
            )

        fetched = self._fetch_remote_history(symbol, coin_id)
        if fetched.empty and cached.empty:
            return pd.DataFrame()
        merged_cache = _merge_cache_frames(cached, fetched)
        if not merged_cache.empty and self.cache_dir is not None:
            merged_cache.to_csv(self.cache_dir / f"{symbol}.csv", index=False)
        return normalize_external_history_frame(
            merged_cache,
            symbol=symbol,
            source_name=self.cfg.source_name,
            provider_name=self.cfg.name,
            as_of_date=as_of_date,
        )

    def _load_cached(self, symbol: str) -> pd.DataFrame:
        if self.cache_dir is None:
            return pd.DataFrame()
        path = self.cache_dir / f"{symbol}.csv"
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(path)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        return frame


class YahooFinanceChartProvider:
    """Fetch and cache daily Yahoo Finance crypto history for cross-check validation."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg
        self.base_url = str(cfg.settings.get("base_url", "https://query1.finance.yahoo.com/v8/finance/chart"))
        cache_dir = cfg.settings.get("cache_dir")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = int(cfg.settings.get("timeout_seconds", 30))
        self.refresh_days = max(0, int(cfg.settings.get("refresh_days", 7)))
        self.requests_sleep_seconds = float(cfg.settings.get("requests_sleep_seconds", 0.5))
        self.min_daily_density = float(cfg.settings.get("min_daily_density", 0.85))
        self.min_date = pd.Timestamp(cfg.settings["min_date"]).normalize() if cfg.settings.get("min_date") else None
        self.symbol_mapping = {
            str(symbol).upper(): str(mapped).upper()
            for symbol, mapped in dict(cfg.settings.get("symbol_mapping", {})).items()
        }
        self.logger = get_logger("yahoo_finance_provider")

    def load_history(self, symbol: str, as_of_date: pd.Timestamp | None = None) -> pd.DataFrame:
        symbol = str(symbol).upper()
        yahoo_symbol = self.symbol_mapping.get(symbol)
        if not yahoo_symbol:
            return pd.DataFrame()

        cached = self._load_cached(symbol)
        if _cache_is_fresh(cached, as_of_date, self.refresh_days) and _has_dense_daily_index(
            cached, min_daily_density=self.min_daily_density
        ):
            return normalize_external_history_frame(
                cached,
                symbol=symbol,
                source_name=self.cfg.source_name,
                provider_name=self.cfg.name,
                as_of_date=as_of_date,
            )

        fetched = self._fetch_remote_history(symbol, yahoo_symbol)
        if fetched.empty and cached.empty:
            return pd.DataFrame()
        merged_cache = _merge_cache_frames(cached, fetched)
        if not merged_cache.empty and self.cache_dir is not None:
            merged_cache.to_csv(self.cache_dir / f"{symbol}.csv", index=False)
        return normalize_external_history_frame(
            merged_cache,
            symbol=symbol,
            source_name=self.cfg.source_name,
            provider_name=self.cfg.name,
            as_of_date=as_of_date,
        )

    def _load_cached(self, symbol: str) -> pd.DataFrame:
        if self.cache_dir is None:
            return pd.DataFrame()
        path = self.cache_dir / f"{symbol}.csv"
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(path)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        return frame

    def _fetch_remote_history(self, symbol: str, yahoo_symbol: str) -> pd.DataFrame:
        url = f"{self.base_url}/{yahoo_symbol}"
        params = {
            "interval": "1d",
            "period1": 0,
            "period2": int(pd.Timestamp.now(tz="UTC").timestamp()),
            "includeAdjustedClose": "false",
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("chart", {}).get("result", [])
        if not result:
            self.logger.warning("Yahoo Finance returned no history for %s.", symbol)
            return pd.DataFrame()
        current = result[0]
        timestamps = current.get("timestamp", [])
        quotes = current.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return pd.DataFrame()
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(timestamps, unit="s").normalize(),
                "open": quotes.get("open", []),
                "high": quotes.get("high", []),
                "low": quotes.get("low", []),
                "close": quotes.get("close", []),
                "volume": quotes.get("volume", []),
            }
        )
        frame["quote_volume"] = pd.to_numeric(frame["close"], errors="coerce") * pd.to_numeric(
            frame["volume"], errors="coerce"
        )
        frame = frame.dropna(subset=["close"]).copy()
        if self.min_date is not None:
            frame = frame.loc[pd.to_datetime(frame["date"]).dt.normalize() >= self.min_date]
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        if self.requests_sleep_seconds > 0.0:
            time.sleep(self.requests_sleep_seconds)
        self.logger.info(
            "Fetched %s Yahoo Finance rows for %s (%s -> %s).",
            len(frame),
            symbol,
            frame["date"].min().date(),
            frame["date"].max().date(),
        )
        return frame[["date", "open", "high", "low", "close", "volume", "quote_volume"]]


class CryptoDataDownloadDailyHistoryProvider:
    """Fetch and cache daily exchange-archive history from CryptoDataDownload."""

    def __init__(self, cfg: ExternalProviderConfig) -> None:
        self.cfg = cfg
        cache_dir = cfg.settings.get("cache_dir")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = int(cfg.settings.get("timeout_seconds", 30))
        self.refresh_days = max(0, int(cfg.settings.get("refresh_days", 30)))
        self.requests_sleep_seconds = float(cfg.settings.get("requests_sleep_seconds", 0.5))
        self.min_date = pd.Timestamp(cfg.settings["min_date"]).normalize() if cfg.settings.get("min_date") else None
        self.symbol_urls = {
            str(symbol).upper(): str(url)
            for symbol, url in dict(cfg.settings.get("symbol_urls", {})).items()
        }
        self.logger = get_logger("cryptodatadownload_provider")

    def load_history(self, symbol: str, as_of_date: pd.Timestamp | None = None) -> pd.DataFrame:
        symbol = str(symbol).upper()
        source_url = self.symbol_urls.get(symbol)
        if not source_url:
            return pd.DataFrame()

        cached = self._load_cached(symbol)
        if _cache_is_fresh(cached, as_of_date, self.refresh_days):
            return normalize_external_history_frame(
                cached,
                symbol=symbol,
                source_name=self.cfg.source_name,
                provider_name=self.cfg.name,
                as_of_date=as_of_date,
            )

        fetched = self._fetch_remote_history(symbol, source_url)
        if fetched.empty and cached.empty:
            return pd.DataFrame()
        merged_cache = _merge_cache_frames(cached, fetched)
        if not merged_cache.empty and self.cache_dir is not None:
            merged_cache.to_csv(self.cache_dir / f"{symbol}.csv", index=False)
        return normalize_external_history_frame(
            merged_cache,
            symbol=symbol,
            source_name=self.cfg.source_name,
            provider_name=self.cfg.name,
            as_of_date=as_of_date,
        )

    def _load_cached(self, symbol: str) -> pd.DataFrame:
        if self.cache_dir is None:
            return pd.DataFrame()
        path = self.cache_dir / f"{symbol}.csv"
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(path)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        return frame

    def _fetch_remote_history(self, symbol: str, source_url: str) -> pd.DataFrame:
        response = requests.get(source_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        lines = response.text.splitlines()
        if not lines:
            return pd.DataFrame()
        data_lines = lines[1:] if "CryptoDataDownload" in lines[0] else lines
        if not data_lines:
            return pd.DataFrame()

        from io import StringIO

        frame = pd.read_csv(StringIO("\n".join(data_lines)))
        if frame.empty:
            return frame
        frame = frame.rename(columns={column: str(column).strip().lower().replace(" ", "_") for column in frame.columns})
        if "date" not in frame.columns:
            return pd.DataFrame()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()

        quote_volume_column = next(
            (column for column in frame.columns if column.startswith("volume_") and any(token in column for token in ["usd", "usdt", "usdc"])),
            None,
        )
        base_volume_column = next(
            (
                column
                for column in frame.columns
                if column.startswith("volume_") and column not in {"volume_usd", "volume_usdt", "volume_usdc"}
            ),
            None,
        )
        standardized = pd.DataFrame(
            {
                "date": frame["date"],
                "open": pd.to_numeric(frame.get("open"), errors="coerce"),
                "high": pd.to_numeric(frame.get("high"), errors="coerce"),
                "low": pd.to_numeric(frame.get("low"), errors="coerce"),
                "close": pd.to_numeric(frame.get("close"), errors="coerce"),
                "volume": pd.to_numeric(frame.get(base_volume_column), errors="coerce") if base_volume_column else pd.NA,
                "quote_volume": pd.to_numeric(frame.get(quote_volume_column), errors="coerce") if quote_volume_column else pd.NA,
            }
        )
        if standardized["quote_volume"].isna().all() and standardized["close"].notna().any() and standardized["volume"].notna().any():
            standardized["quote_volume"] = standardized["close"] * standardized["volume"]
        standardized = standardized.dropna(subset=["close"]).copy()
        standardized = standardized.loc[
            standardized[["open", "high", "low", "close"]].sum(axis=1, min_count=4).fillna(0.0) > 0.0
        ]
        if self.min_date is not None:
            standardized = standardized.loc[standardized["date"] >= self.min_date]
        standardized = standardized.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        if standardized.empty:
            return standardized
        if self.requests_sleep_seconds > 0.0:
            time.sleep(self.requests_sleep_seconds)
        self.logger.info(
            "Fetched %s CryptoDataDownload rows for %s (%s -> %s).",
            len(standardized),
            symbol,
            standardized["date"].min().date(),
            standardized["date"].max().date(),
        )
        return standardized

def resolve_external_provider_configs(config: dict[str, Any]) -> list[ExternalProviderConfig]:
    external_cfg = config.get("external_data", {})
    root = config["paths"].project_root
    provider_rows = []
    for name, raw in external_cfg.get("providers", {}).items():
        directory = raw.get("directory")
        path = raw.get("path")
        provider_rows.append(
            ExternalProviderConfig(
                name=str(name),
                provider_type=str(raw.get("type", "local_csv_history")),
                source_name=str(raw.get("source_name", name)),
                enabled=bool(raw.get("enabled", False)),
                merge_role=str(raw.get("merge_role", "history")),
                directory=(root / directory) if directory else None,
                path=(root / path) if path else None,
                settings={
                    key: ((root / value) if key == "cache_dir" and value else value)
                    for key, value in raw.items()
                    if key not in {"type", "source_name", "enabled", "merge_role", "directory", "path"}
                },
            )
        )
    return provider_rows


def normalize_external_history_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    source_name: str,
    provider_name: str,
    as_of_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=CANONICAL_HISTORY_COLUMNS + ["data_source", "data_provider", "is_external_source"])

    normalized = frame.copy()
    if "date" not in normalized.columns:
        raise ValueError(f"External history for {symbol} is missing a date column.")
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.normalize()
    if as_of_date is not None:
        normalized = normalized.loc[normalized["date"] <= pd.Timestamp(as_of_date).normalize()]
    if normalized.empty:
        return pd.DataFrame(columns=CANONICAL_HISTORY_COLUMNS + ["data_source", "data_provider", "is_external_source"])

    if "quote_volume" not in normalized.columns:
        if "close" in normalized.columns and "volume" in normalized.columns:
            normalized["quote_volume"] = pd.to_numeric(normalized["close"], errors="coerce") * pd.to_numeric(
                normalized["volume"], errors="coerce"
            )
        else:
            normalized["quote_volume"] = pd.NA

    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        normalized[column] = pd.to_numeric(normalized.get(column), errors="coerce")

    normalized["symbol"] = symbol
    normalized["data_source"] = source_name
    normalized["data_provider"] = provider_name
    normalized["is_external_source"] = True
    normalized = normalized[
        CANONICAL_HISTORY_COLUMNS + ["data_source", "data_provider", "is_external_source"]
    ].sort_values("date")
    normalized = normalized.drop_duplicates("date", keep="last").reset_index(drop=True)
    return clean_numeric_frame(normalized)


def normalize_binance_history_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.normalize()
    normalized["symbol"] = symbol
    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized["data_source"] = "binance"
    normalized["data_provider"] = "binance"
    normalized["is_external_source"] = False
    normalized = normalized[
        CANONICAL_HISTORY_COLUMNS + ["data_source", "data_provider", "is_external_source"]
    ].sort_values("date")
    normalized = normalized.drop_duplicates("date", keep="last").reset_index(drop=True)
    return clean_numeric_frame(normalized)


def _cache_is_fresh(frame: pd.DataFrame, as_of_date: pd.Timestamp | None, refresh_days: int) -> bool:
    if frame.empty:
        return False
    latest_date = pd.to_datetime(frame["date"]).max().normalize()
    if as_of_date is not None and latest_date >= pd.Timestamp(as_of_date).normalize():
        return True
    if refresh_days <= 0:
        return True
    freshness_cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=refresh_days)
    return latest_date >= freshness_cutoff


def _merge_cache_frames(cached: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (cached, fetched) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"]).dt.normalize()
    merged = merged.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return merged


def _has_dense_daily_index(frame: pd.DataFrame, min_daily_density: float = 0.85) -> bool:
    if frame.empty or len(frame) < 30:
        return False
    diff_days = pd.to_datetime(frame["date"]).sort_values().diff().dt.days.dropna()
    if diff_days.empty:
        return False
    return bool((diff_days <= 1).mean() >= float(min_daily_density))


def _build_source_priority_map(config: dict[str, Any]) -> dict[str, int]:
    priority_list = [str(item) for item in config.get("external_data", {}).get("provider_priority", ["binance"])]
    return {name: rank for rank, name in enumerate(priority_list)}


def _instantiate_history_providers(
    config: dict[str, Any],
    *,
    merge_roles: set[str] | None = None,
) -> list[Any]:
    providers = []
    for cfg in resolve_external_provider_configs(config):
        if not cfg.enabled:
            continue
        if merge_roles is not None and cfg.merge_role not in merge_roles:
            continue
        if cfg.provider_type == "local_csv_history":
            providers.append(LocalCsvHistoryProvider(cfg))
        elif cfg.provider_type == "cryptocompare_daily_history":
            providers.append(CryptoCompareDailyHistoryProvider(cfg))
        elif cfg.provider_type == "coingecko_market_chart":
            providers.append(CoinGeckoMarketChartProvider(cfg))
        elif cfg.provider_type == "yahoo_finance_chart":
            providers.append(YahooFinanceChartProvider(cfg))
        elif cfg.provider_type == "cryptodatadownload_daily_history":
            providers.append(CryptoDataDownloadDailyHistoryProvider(cfg))
    return providers


def _combine_external_candidate_frames(
    frames: list[pd.DataFrame],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, int]:
    if not frames:
        return pd.DataFrame(), 0
    usable_frames = [frame.copy() for frame in frames if frame is not None and not frame.empty]
    if not usable_frames:
        return pd.DataFrame(), 0
    priority_map = _build_source_priority_map(config)
    combined = pd.concat(usable_frames, ignore_index=True)
    duplicate_dates = int(combined["date"].duplicated().sum())
    combined["source_priority"] = combined["data_source"].map(priority_map).fillna(len(priority_map) + 100).astype(int)
    combined = combined.sort_values(["date", "source_priority", "data_provider"]).drop_duplicates("date", keep="first")
    combined = combined.sort_values("date").reset_index(drop=True)
    return clean_numeric_frame(combined.drop(columns=["source_priority"])), duplicate_dates


def _compute_gap_stats(frame: pd.DataFrame) -> tuple[int, int]:
    if frame.empty:
        return 0, 0
    diff_days = pd.to_datetime(frame["date"]).sort_values().diff().dt.days.fillna(1).astype(int)
    return int((diff_days > 1).sum()), int(diff_days.max())


def _compute_overlap_consistency(
    reference_frame: pd.DataFrame,
    candidate_frame: pd.DataFrame,
    *,
    prefix: str,
) -> dict[str, Any]:
    output = {
        f"{prefix}_days": 0,
        f"{prefix}_return_corr": pd.NA,
        f"{prefix}_median_abs_return_diff": pd.NA,
        f"{prefix}_close_ratio_cv": pd.NA,
    }
    if reference_frame.empty or candidate_frame.empty:
        return output

    overlap = (
        reference_frame[["date", "close"]]
        .rename(columns={"close": "reference_close"})
        .merge(
            candidate_frame[["date", "close"]].rename(columns={"close": "candidate_close"}),
            on="date",
            how="inner",
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    output[f"{prefix}_days"] = int(len(overlap))
    if len(overlap) < 2:
        return output

    reference_returns = overlap["reference_close"].pct_change(fill_method=None)
    candidate_returns = overlap["candidate_close"].pct_change(fill_method=None)
    valid_mask = reference_returns.notna() & candidate_returns.notna()
    if int(valid_mask.sum()) >= 2:
        output[f"{prefix}_return_corr"] = float(reference_returns.loc[valid_mask].corr(candidate_returns.loc[valid_mask]))
        output[f"{prefix}_median_abs_return_diff"] = float(
            (reference_returns.loc[valid_mask] - candidate_returns.loc[valid_mask]).abs().median()
        )

    ratio = overlap["reference_close"] / overlap["candidate_close"].replace(0.0, pd.NA)
    ratio = ratio.dropna()
    if not ratio.empty and abs(float(ratio.mean())) > 0.0:
        output[f"{prefix}_close_ratio_cv"] = float(ratio.std(ddof=0) / abs(ratio.mean()))
    return output


def _quality_gate_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("external_data", {}).get("quality_gate", {}))


def _whitelist_tier_map(config: dict[str, Any]) -> dict[str, str]:
    external_cfg = config.get("external_data", {})
    tier_map: dict[str, str] = {}
    for symbol in external_cfg.get("core_backfill_whitelist", []):
        tier_map[str(symbol).upper()] = "core"
    for symbol in external_cfg.get("cautious_backfill_whitelist", []):
        tier_map[str(symbol).upper()] = "cautious"
    if not tier_map and external_cfg.get("backfill_symbol_whitelist"):
        for symbol in external_cfg.get("backfill_symbol_whitelist", []):
            tier_map[str(symbol).upper()] = "core"
    return tier_map


def _caution_reason_map(config: dict[str, Any]) -> dict[str, str]:
    external_cfg = config.get("external_data", {})
    return {
        str(symbol).upper(): str(reason)
        for symbol, reason in dict(external_cfg.get("cautious_symbol_reasons", {})).items()
    }


def _evaluate_external_candidate_quality(
    symbol: str,
    normalized_binance: pd.DataFrame,
    external_candidate: pd.DataFrame,
    crosscheck_candidate: pd.DataFrame,
    config: dict[str, Any],
    *,
    duplicate_dates_external: int,
) -> dict[str, Any]:
    quality_cfg = _quality_gate_config(config)
    whitelist_tiers = _whitelist_tier_map(config)
    caution_reasons = _caution_reason_map(config)
    whitelist_tier = whitelist_tiers.get(symbol, "none")
    earliest_binance_date = pd.to_datetime(normalized_binance["date"]).min().normalize()
    earliest_merged_date = earliest_binance_date
    if not external_candidate.empty:
        earliest_merged_date = min(earliest_binance_date, pd.to_datetime(external_candidate["date"]).min().normalize())

    gap_count, max_gap_days = _compute_gap_stats(external_candidate)
    crosscheck_gap_count, crosscheck_max_gap_days = _compute_gap_stats(crosscheck_candidate)
    overlap_metrics = _compute_overlap_consistency(normalized_binance, external_candidate, prefix="overlap")
    crosscheck_metrics = _compute_overlap_consistency(external_candidate, crosscheck_candidate, prefix="crosscheck")
    missing_core_field_rows = 0
    suspicious_jump_count = 0
    max_abs_daily_return = pd.NA
    if not external_candidate.empty:
        missing_core_field_rows = int(external_candidate[["open", "high", "low", "close"]].isna().any(axis=1).sum())
        external_returns = external_candidate["close"].pct_change(fill_method=None).abs()
        threshold = float(quality_cfg.get("abnormal_jump_threshold", 2.5))
        suspicious_jump_count = int((external_returns > threshold).sum())
        if external_returns.notna().any():
            max_abs_daily_return = float(external_returns.max())

    pre_binance_rows_added = 0
    if not external_candidate.empty:
        pre_binance_rows_added = int((pd.to_datetime(external_candidate["date"]) < earliest_binance_date).sum())

    quality_row: dict[str, Any] = {
        "symbol": symbol,
        "whitelist_tier": whitelist_tier,
        "caution_reason": caution_reasons.get(symbol, ""),
        "whitelist_candidate": whitelist_tier in {"core", "cautious"},
        "quality_passed": False,
        "merge_applied": False,
        "quality_status": "binance_only",
        "quality_reasons": "",
        "primary_provider": ",".join(sorted(external_candidate["data_provider"].dropna().unique()))
        if not external_candidate.empty
        else "",
        "secondary_provider": ",".join(sorted(crosscheck_candidate["data_provider"].dropna().unique()))
        if not crosscheck_candidate.empty
        else "",
        "external_sources_used": ",".join(sorted(external_candidate["data_source"].dropna().unique()))
        if not external_candidate.empty
        else "",
        "approved_sources_used": "",
        "crosscheck_status": "unavailable" if crosscheck_candidate.empty else "informational",
        "final_decision": "binance_only",
        "notes": "",
        "binance_rows": int(len(normalized_binance)),
        "external_candidate_rows": int(len(external_candidate)),
        "merged_rows": int(len(normalized_binance)),
        "earliest_binance_date": str(earliest_binance_date.date()),
        "earliest_external_candidate_date": (
            str(pd.to_datetime(external_candidate["date"]).min().date()) if not external_candidate.empty else ""
        ),
        "earliest_merged_date": str(earliest_merged_date.date()),
        "pre_binance_rows_added": pre_binance_rows_added,
        "duplicate_dates_external": int(duplicate_dates_external),
        "monotonic_external": bool(
            external_candidate.empty or pd.to_datetime(external_candidate["date"]).is_monotonic_increasing
        ),
        "duplicate_check": int(duplicate_dates_external) == 0,
        "monotonic_check": bool(
            external_candidate.empty or pd.to_datetime(external_candidate["date"]).is_monotonic_increasing
        ),
        "gap_count_gt_1d": int(gap_count),
        "max_gap_days": int(max_gap_days),
        "missing_core_field_rows": int(missing_core_field_rows),
        "suspicious_jump_count": int(suspicious_jump_count),
        "max_abs_daily_return": max_abs_daily_return,
        "crosscheck_provider": ",".join(sorted(crosscheck_candidate["data_provider"].dropna().unique()))
        if not crosscheck_candidate.empty
        else "",
        "crosscheck_gap_count_gt_1d": int(crosscheck_gap_count),
        "crosscheck_max_gap_days": int(crosscheck_max_gap_days),
        **overlap_metrics,
        **crosscheck_metrics,
    }

    if whitelist_tier == "none":
        quality_row["quality_status"] = "not_whitelisted"
        quality_row["quality_reasons"] = "symbol_not_in_backfill_whitelist"
        quality_row["final_decision"] = "binance_only"
        return quality_row

    if whitelist_tier == "cautious" and not bool(config.get("external_data", {}).get("merge_cautious_symbols", True)):
        quality_row["quality_status"] = "cautious_holdout"
        quality_row["quality_reasons"] = "cautious_tier_not_enabled_for_merge"
        quality_row["final_decision"] = "cautious_holdout"
        quality_row["notes"] = quality_row["caution_reason"]
        return quality_row

    if external_candidate.empty:
        quality_row["quality_status"] = "no_external_rows"
        quality_row["quality_reasons"] = "no_external_rows_available"
        quality_row["final_decision"] = "rejected"
        return quality_row

    if not bool(quality_cfg.get("enabled", True)):
        quality_row["quality_passed"] = True
        quality_row["merge_applied"] = True
        quality_row["quality_status"] = "merged"
        quality_row["approved_sources_used"] = quality_row["external_sources_used"]
        quality_row["crosscheck_status"] = "informational"
        quality_row["final_decision"] = "approved_cautious" if whitelist_tier == "cautious" else "approved_core"
        quality_row["notes"] = quality_row["caution_reason"]
        return quality_row

    if pre_binance_rows_added < int(quality_cfg.get("min_pre_binance_rows_added", 1)):
        quality_row["quality_status"] = "insufficient_extension"
        quality_row["quality_reasons"] = "insufficient_pre_binance_extension"
        quality_row["final_decision"] = "rejected"
        return quality_row

    failures: list[str] = []
    if duplicate_dates_external > int(quality_cfg.get("max_duplicate_dates", 0)):
        failures.append("duplicate_dates_external")
    if not quality_row["monotonic_external"]:
        failures.append("non_monotonic_external_time")
    if gap_count > int(quality_cfg.get("max_gap_count", 5)):
        failures.append("excessive_gap_count")
    if max_gap_days > int(quality_cfg.get("max_gap_days", 7)):
        failures.append("excessive_max_gap")
    if missing_core_field_rows > int(quality_cfg.get("max_missing_core_field_rows", 0)):
        failures.append("missing_core_fields")
    if suspicious_jump_count > int(quality_cfg.get("max_suspicious_jump_count", 10)):
        failures.append("suspicious_jump_count")

    min_overlap_days = int(quality_cfg.get("min_overlap_days", 180))
    if int(quality_row["overlap_days"]) < min_overlap_days:
        failures.append("insufficient_overlap_days")
    else:
        overlap_return_corr = pd.to_numeric(pd.Series([quality_row["overlap_return_corr"]]), errors="coerce").iloc[0]
        overlap_abs_diff = pd.to_numeric(
            pd.Series([quality_row["overlap_median_abs_return_diff"]]), errors="coerce"
        ).iloc[0]
        overlap_ratio_cv = pd.to_numeric(pd.Series([quality_row["overlap_close_ratio_cv"]]), errors="coerce").iloc[0]
        if pd.notna(overlap_return_corr) and overlap_return_corr < float(quality_cfg.get("min_overlap_return_corr", 0.97)):
            failures.append("low_overlap_return_corr")
        if pd.notna(overlap_abs_diff) and overlap_abs_diff > float(
            quality_cfg.get("max_overlap_median_abs_return_diff", 0.03)
        ):
            failures.append("high_overlap_return_diff")
        if pd.notna(overlap_ratio_cv) and overlap_ratio_cv > float(quality_cfg.get("max_overlap_close_ratio_cv", 0.05)):
            failures.append("unstable_overlap_price_ratio")

    crosscheck_notes: list[str] = []
    if not crosscheck_candidate.empty:
        min_crosscheck_days = int(quality_cfg.get("min_crosscheck_overlap_days", 180))
        crosscheck_return_corr = pd.to_numeric(
            pd.Series([quality_row["crosscheck_return_corr"]]), errors="coerce"
        ).iloc[0]
        crosscheck_abs_diff = pd.to_numeric(
            pd.Series([quality_row["crosscheck_median_abs_return_diff"]]), errors="coerce"
        ).iloc[0]
        crosscheck_ratio_cv = pd.to_numeric(
            pd.Series([quality_row["crosscheck_close_ratio_cv"]]), errors="coerce"
        ).iloc[0]
        if int(quality_row["crosscheck_days"]) < min_crosscheck_days:
            quality_row["crosscheck_status"] = "limited"
            crosscheck_notes.append("limited_crosscheck_history")
        else:
            quality_row["crosscheck_status"] = "pass"
            reject_trigger = False
            if pd.notna(crosscheck_return_corr) and crosscheck_return_corr < float(
                quality_cfg.get("crosscheck_reject_return_corr", 0.80)
            ):
                reject_trigger = True
                crosscheck_notes.append("crosscheck_return_corr_reject")
            elif pd.notna(crosscheck_return_corr) and crosscheck_return_corr < float(
                quality_cfg.get("crosscheck_warn_return_corr", 0.92)
            ):
                quality_row["crosscheck_status"] = "warn"
                crosscheck_notes.append("crosscheck_return_corr_warn")
            if pd.notna(crosscheck_ratio_cv) and crosscheck_ratio_cv > float(
                quality_cfg.get("crosscheck_reject_close_ratio_cv", 0.18)
            ):
                reject_trigger = True
                crosscheck_notes.append("crosscheck_price_ratio_reject")
            elif pd.notna(crosscheck_ratio_cv) and crosscheck_ratio_cv > float(
                quality_cfg.get("crosscheck_warn_close_ratio_cv", 0.08)
            ):
                quality_row["crosscheck_status"] = "warn"
                crosscheck_notes.append("crosscheck_price_ratio_warn")
            if pd.notna(crosscheck_abs_diff) and crosscheck_abs_diff > float(
                quality_cfg.get("crosscheck_reject_median_abs_return_diff", 0.08)
            ):
                reject_trigger = True
                crosscheck_notes.append("crosscheck_return_diff_reject")
            elif pd.notna(crosscheck_abs_diff) and crosscheck_abs_diff > float(
                quality_cfg.get("crosscheck_warn_median_abs_return_diff", 0.03)
            ):
                quality_row["crosscheck_status"] = "warn"
                crosscheck_notes.append("crosscheck_return_diff_warn")
            if int(quality_row["crosscheck_gap_count_gt_1d"]) > int(quality_cfg.get("crosscheck_reject_gap_count", 60)):
                reject_trigger = True
                crosscheck_notes.append("crosscheck_gap_count_reject")
            elif int(quality_row["crosscheck_gap_count_gt_1d"]) > int(quality_cfg.get("crosscheck_warn_gap_count", 15)):
                quality_row["crosscheck_status"] = "warn"
                crosscheck_notes.append("crosscheck_gap_count_warn")
            if bool(quality_cfg.get("use_crosscheck_provider", False)) and reject_trigger:
                failures.append("crosscheck_severe_anomaly")

    if failures:
        quality_row["quality_status"] = "rejected_quality_gate"
        quality_row["quality_reasons"] = ",".join(failures)
        quality_row["final_decision"] = "rejected"
        quality_row["notes"] = ",".join(filter(None, [quality_row["caution_reason"], *crosscheck_notes]))
        return quality_row

    quality_row["quality_passed"] = True
    quality_row["merge_applied"] = True
    quality_row["quality_status"] = "merged"
    quality_row["approved_sources_used"] = quality_row["external_sources_used"]
    quality_row["final_decision"] = "approved_cautious" if whitelist_tier == "cautious" else "approved_core"
    quality_row["notes"] = ",".join(filter(None, [quality_row["caution_reason"], *crosscheck_notes]))
    return quality_row


def merge_symbol_histories(
    binance_history: pd.DataFrame,
    external_frames: list[pd.DataFrame],
    config: dict[str, Any],
    *,
    symbol: str,
) -> pd.DataFrame:
    if binance_history.empty:
        raise ValueError(f"Binance history for {symbol} is empty.")

    external_cfg = config.get("external_data", {})
    normalized_binance = normalize_binance_history_frame(binance_history, symbol)
    if not external_cfg.get("enabled", False) or not external_cfg.get("merge_pre_binance_history", False):
        return normalized_binance

    frames = [normalized_binance]
    for frame in external_frames:
        if frame is not None and not frame.empty:
            frames.append(frame.copy())

    priority_map = _build_source_priority_map(config)
    combined = pd.concat(frames, ignore_index=True)
    combined["source_priority"] = combined["data_source"].map(priority_map).fillna(len(priority_map) + 100).astype(int)
    combined = combined.sort_values(["date", "source_priority", "data_provider"]).drop_duplicates("date", keep="first")
    combined = combined.sort_values("date").reset_index(drop=True)
    if not combined["date"].is_monotonic_increasing:
        raise ValueError(f"Merged history for {symbol} is not monotonic after merge.")
    return clean_numeric_frame(combined.drop(columns=["source_priority"]))


def merge_histories_with_external(
    histories: dict[str, pd.DataFrame],
    config: dict[str, Any],
    *,
    as_of_date: pd.Timestamp | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    logger = get_logger("external_data")
    external_cfg = config.get("external_data", {})
    if not external_cfg.get("enabled", False):
        return histories, pd.DataFrame(columns=["symbol", "external_sources_used", "binance_rows", "merged_rows"])

    merge_providers = _instantiate_history_providers(config, merge_roles=MERGE_HISTORY_ROLES)
    crosscheck_providers = _instantiate_history_providers(config, merge_roles=CROSSCHECK_HISTORY_ROLES)
    merged_histories: dict[str, pd.DataFrame] = {}
    summary_rows = []

    for symbol, history in histories.items():
        normalized_binance = normalize_binance_history_frame(history, symbol)
        external_frames = [provider.load_history(symbol, as_of_date=as_of_date) for provider in merge_providers]
        crosscheck_frames = [provider.load_history(symbol, as_of_date=as_of_date) for provider in crosscheck_providers]
        external_candidate, duplicate_dates_external = _combine_external_candidate_frames(external_frames, config)
        crosscheck_candidate, _ = _combine_external_candidate_frames(crosscheck_frames, config)

        quality_row = _evaluate_external_candidate_quality(
            symbol,
            normalized_binance,
            external_candidate,
            crosscheck_candidate,
            config,
            duplicate_dates_external=duplicate_dates_external,
        )

        if quality_row["merge_applied"]:
            merged = merge_symbol_histories(history, [external_candidate], config, symbol=symbol)
            quality_row["merged_rows"] = int(len(merged))
            quality_row["earliest_merged_date"] = str(pd.to_datetime(merged["date"]).min().date())
        else:
            merged = normalized_binance

        merged_histories[symbol] = merged
        summary_rows.append(quality_row)

    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        applied = int(summary["merge_applied"].fillna(False).astype(bool).sum())
        rejected = int(summary["quality_status"].eq("rejected_quality_gate").sum())
        logger.info(
            "External data merge evaluated for %s symbols. Applied merges for %s symbols; rejected %s symbols by quality gate.",
            len(summary),
            applied,
            rejected,
        )
    return merged_histories, summary


def load_optional_market_cap_metadata(config: dict[str, Any]) -> pd.DataFrame:
    external_cfg = config.get("external_data", {})
    if not external_cfg.get("enabled", False) or not external_cfg.get("use_market_cap_filter", False):
        return pd.DataFrame()
    providers = [
        LocalCsvMetadataProvider(cfg)
        for cfg in resolve_external_provider_configs(config)
        if cfg.enabled and cfg.provider_type == "local_csv_metadata"
    ]
    frames = [provider.load_metadata() for provider in providers]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
