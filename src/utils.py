from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


def get_logger(name: str = "crypto_live_pool_rotation") -> logging.Logger:
    """Create a process-wide console logger with a simple format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def ensure_directory(path: Path | str) -> Path:
    """Create a directory if needed and return it as a Path."""
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def to_timestamp(value: Any) -> pd.Timestamp:
    """Convert a user/config value into a normalized pandas timestamp."""
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    return pd.Timestamp(value).normalize()


def date_to_str(value: Any) -> str:
    """Format a timestamp-like object as YYYY-MM-DD."""
    return to_timestamp(value).strftime("%Y-%m-%d")


def read_json(path: Path | str, default: Optional[Any] = None) -> Any:
    """Read JSON if it exists, otherwise return a default value."""
    file_path = Path(path)
    if not file_path.exists():
        return default
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path | str, payload: Any) -> None:
    """Write a JSON payload with a stable, readable format."""
    file_path = Path(path)
    ensure_directory(file_path.parent)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False, ensure_ascii=False)


def clean_numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace inf with NaN and keep the original frame shape."""
    cleaned = frame.replace([np.inf, -np.inf], np.nan)
    return downcast_numeric_frame(cleaned)


def downcast_numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Reduce pandas numeric dtypes to lower-memory equivalents when safe."""
    downcasted = frame.copy()
    float_columns = list(downcasted.select_dtypes(include=["floating"]).columns)
    integer_columns = list(downcasted.select_dtypes(include=["integer"]).columns)

    for column in float_columns:
        downcasted[column] = pd.to_numeric(downcasted[column], errors="coerce", downcast="float")
    for column in integer_columns:
        downcasted[column] = pd.to_numeric(downcasted[column], errors="coerce", downcast="integer")

    return downcasted


def safe_divide(
    numerator: pd.Series | pd.DataFrame | np.ndarray | float,
    denominator: pd.Series | pd.DataFrame | np.ndarray | float,
    fill_value: float = np.nan,
) -> pd.Series | pd.DataFrame | np.ndarray | float:
    """Divide without throwing on zero denominators."""
    result = numerator / denominator
    if isinstance(result, (pd.Series, pd.DataFrame)):
        result = result.replace([np.inf, -np.inf], np.nan)
        if fill_value is not np.nan:
            result = result.fillna(fill_value)
        return result
    if np.isscalar(result):
        if np.isinf(result) or np.isnan(result):
            return fill_value
    return result


def rank_pct(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Convert a cross section into a [0, 1] rank percentile."""
    valid = series.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index)
    if len(valid) == 1:
        output = pd.Series(np.nan, index=series.index)
        output.loc[valid.index] = 1.0
        return output
    ranked = valid.rank(pct=True, ascending=ascending, method="average")
    output = pd.Series(np.nan, index=series.index)
    output.loc[ranked.index] = ranked
    return output


def normalize_component_by_date(
    panel: pd.DataFrame,
    column: str,
    universe_mask: Optional[pd.Series] = None,
) -> pd.Series:
    """Cross-sectionally rank-normalize a score column by date."""
    if universe_mask is None:
        universe_mask = panel[column].notna()
    target = panel[column].where(universe_mask)
    return target.groupby(level="date", group_keys=False).transform(rank_pct)


def make_schedule(dates: Sequence[pd.Timestamp], frequency: str) -> list[pd.Timestamp]:
    """Create rebalancing or refresh dates from a trading calendar."""
    if len(dates) == 0:
        return []
    date_index = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values().unique()
    frequency = frequency.lower()
    if frequency == "daily":
        return list(date_index)

    groups = pd.Series(date_index, index=date_index)
    if frequency == "weekly":
        grouped = groups.groupby(date_index.to_period("W-SUN")).last()
    elif frequency == "monthly":
        grouped = groups.groupby(date_index.to_period("M")).last()
    else:
        raise ValueError(f"Unsupported frequency: {frequency}")

    schedule = list(grouped.astype("datetime64[ns]").tolist())
    first_date = date_index[0]
    if schedule[0] != first_date:
        schedule.insert(0, first_date)
    return schedule


def next_trading_date(
    dates: Sequence[pd.Timestamp],
    current_date: pd.Timestamp,
    lag_days: int = 1,
) -> Optional[pd.Timestamp]:
    """Return the next available trading date after an integer lag."""
    ordered = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values().unique()
    current_date = to_timestamp(current_date)
    try:
        current_position = ordered.get_loc(current_date)
    except KeyError:
        return None
    next_position = current_position + lag_days
    if next_position >= len(ordered):
        return None
    return pd.Timestamp(ordered[next_position]).normalize()


def load_local_histories(
    raw_dir: Path | str,
    symbols: Optional[Iterable[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, pd.DataFrame]:
    """Load local symbol CSV files into a symbol -> dataframe mapping."""
    raw_path = Path(raw_dir)
    chosen = set(symbols) if symbols is not None else None
    histories: dict[str, pd.DataFrame] = {}
    for file_path in sorted(raw_path.glob("*.csv")):
        symbol = file_path.stem.upper()
        if chosen is not None and symbol not in chosen:
            continue
        frame = pd.read_csv(file_path)
        if frame.empty:
            continue
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        if start_date is not None:
            frame = frame.loc[frame["date"] >= to_timestamp(start_date)]
        if end_date is not None:
            frame = frame.loc[frame["date"] <= to_timestamp(end_date)]
        if frame.empty:
            continue
        numeric_columns = [
            column
            for column in frame.columns
            if column not in {"date", "symbol", "base_asset", "quote_asset"}
        ]
        for column in numeric_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
        frame["symbol"] = symbol
        histories[symbol] = downcast_numeric_frame(frame.reset_index(drop=True))
    return histories


def wide_field_from_panel(panel: pd.DataFrame, column: str) -> pd.DataFrame:
    """Pivot a multi-index panel into a date x symbol matrix for one field."""
    return panel[column].unstack("symbol").sort_index()


def flatten_metrics_table(metrics: Mapping[str, Any]) -> pd.DataFrame:
    """Turn a nested metrics mapping into a tidy dataframe."""
    rows = []
    for key, value in metrics.items():
        if isinstance(value, Mapping):
            row = {"name": key}
            row.update(value)
            rows.append(row)
        else:
            rows.append({"name": key, "value": value})
    return pd.DataFrame(rows)


def trading_day_count(index: pd.Index) -> int:
    """Return the number of rows in a time index as an int."""
    return int(len(pd.Index(index)))
