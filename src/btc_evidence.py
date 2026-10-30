"""Manual BTC daily evidence; hashes are consistency checks, not attestations."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from .binance_client import BinanceSpotClient
from .export import resolve_clean_source_revision
from .pipeline import _export_btc_cycle_from_history

ENDPOINT = "https://api.binance.com/api/v3/klines"
COUNT = 220
MAX_BODY = 1024 * 1024
MAX_AGE_SECONDS = 172800
FORMULA_VERSION = "btc-power-law-gma200/v1"
ROOT = Path(__file__).resolve().parents[1]


class RejectedResponse(ValueError):
    """Bounded public response retained only as failed collection evidence."""

    def __init__(self, *, status, endpoint_matches, raw, received_at):
        super().__init__("unexpected response endpoint or status")
        self.status = status
        self.endpoint_matches = endpoint_matches
        self.raw = raw
        self.received_at = received_at


def _now():
    return pd.Timestamp.now(tz="UTC")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _constant(value):
    raise ValueError("non-standard JSON constant")


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)


def _utc(value):
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None or t.utcoffset() != pd.Timedelta(0):
        raise ValueError("explicit UTC time required")
    return t.tz_convert("UTC")


def _versions():
    return {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "requests")}


def _source():
    revision = resolve_clean_source_revision(ROOT)
    try:
        subprocess.run(["git", "ls-files", "--error-unmatch", "src/btc_evidence.py", "src/binance_client.py",
                        "src/pipeline.py", "src/indicators.py", "scripts/btc_cycle_evidence.py", "requirements-lock.txt"],
                       cwd=ROOT, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Evidence code is not tracked by the producer commit") from exc
    return revision


def _lock_hash():
    return _hash((ROOT / "requirements-lock.txt").read_bytes())


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _parse_payload(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BODY:
        raise ValueError("response size invalid")
    payload = _json(raw)
    if not isinstance(payload, list) or len(payload) != COUNT:
        raise ValueError("daily bar count invalid")
    for row in payload:
        if not isinstance(row, list) or len(row) != 12:
            raise ValueError("daily bar shape invalid")
        for index in (1, 2, 3, 4, 5, 7, 9, 10):
            value = row[index]
            if not isinstance(value, str):
                raise ValueError("daily bar number invalid")
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("daily bar number invalid") from exc
            if (not number.is_finite() or not math.isfinite(float(number)) or number < 0
                    or (index in (1, 2, 3, 4) and number == 0)):
                raise ValueError("daily bar number invalid")
        if type(row[8]) is not int or row[8] < 0:
            raise ValueError("daily trade count invalid")
    return payload


def _compute(raw, *, start, end, received):
    payload = _parse_payload(raw)
    client = BinanceSpotClient.__new__(BinanceSpotClient)
    client.config = SimpleNamespace(kline_limit=COUNT, requests_sleep_seconds=0)
    calls = []
    def read_page(endpoint, params):
        calls.append((endpoint, params))
        if len(calls) != 1:
            raise ValueError("unexpected pagination")
        return payload
    client._request = read_page
    frame = client.get_klines("BTCUSDT", start, end, received_at_fn=lambda: received)
    expected = pd.date_range(start, end, freq="D")
    if len(frame) != COUNT or not pd.DatetimeIndex(frame["date"]).equals(expected):
        raise ValueError("daily bars incomplete")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "BTCUSDT.csv"
        frame.to_csv(path, index=False)
        result = _export_btc_cycle_from_history(path, Path(tmp), expected[-1], available_by=received)
    ahr = result.get("ahr999")
    if isinstance(ahr, bool) or not isinstance(ahr, (int, float)) or not math.isfinite(ahr) or ahr <= 0:
        raise ValueError("AHR unavailable")
    return float(ahr), calls[0][1], int(frame.iloc[-1]["close_time"])


def _fetch(params):
    import requests
    session = requests.Session()
    request = requests.Request("GET", ENDPOINT, params=params).prepare()
    if any(key.lower() in {"authorization", "cookie", "x-mbx-apikey"} for key in request.headers):
        raise ValueError("credential headers forbidden")
    settings = session.merge_environment_settings(request.url, {}, True, None, None)
    if settings.get("verify") is False:
        raise ValueError("TLS verification required")
    response = session.send(request, timeout=30, allow_redirects=False, **settings)
    try:
        parts = urlsplit(response.url)
        endpoint_matches = (parts.scheme == "https" and parts.netloc == "api.binance.com"
                            and parts.path == "/api/v3/klines" and not parts.fragment
                            and parse_qs(parts.query) == {key: [str(value)] for key, value in params.items()})
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_BODY:
                raise ValueError("response too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        received = _now()
        if response.status_code != 200 or not endpoint_matches:
            raise RejectedResponse(status=response.status_code, endpoint_matches=bool(endpoint_matches),
                                   raw=raw, received_at=received)
        return raw, received
    finally:
        response.close()
        session.close()


def collect(output: Path):
    """Explicit network action, using only a fixed public endpoint."""
    output = Path(output)
    if output.exists():
        raise ValueError("output directory must not exist")
    revision, versions, lock_hash = _source(), _versions(), _lock_hash()
    started = _now()
    end = started.normalize().tz_localize(None) - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=COUNT - 1)
    params = {"symbol": "BTCUSDT", "interval": "1d", "startTime": int(start.tz_localize("UTC").timestamp() * 1000),
              "endTime": int((end.tz_localize("UTC") + pd.Timedelta(days=1)).timestamp() * 1000) - 1,
              "limit": COUNT}
    try:
        raw, received = _fetch(params)
    except RejectedResponse as exc:
        output.mkdir(parents=True, exist_ok=False)
        (output / "rejected-response.bin").write_bytes(exc.raw)
        failure = {"schema": "btc-cycle-collection-failure/v1", "status": "failed",
                   "source_revision": revision, "lock_sha256": lock_hash, "dependencies": versions,
                   "endpoint": ENDPOINT, "params": params, "http_status": exc.status,
                   "endpoint_matches": exc.endpoint_matches, "started_at": started.isoformat(),
                   "received_at": exc.received_at.isoformat(), "response_sha256": _hash(exc.raw),
                   "source_authenticated": False, "clock_authenticated": False,
                   "historical_pit_verified": False}
        (output / "failure-manifest.json").write_bytes(_encode(failure))
        raise
    ahr, replay_params, close_ms = _compute(raw, start=start, end=end, received=received)
    if replay_params != params:
        raise ValueError("request binding mismatch")
    computed = _now()
    if not (started <= received <= computed) or _source() != revision:
        raise ValueError("clock or source changed")
    envelope = {"contract_version": "btc-cycle-input/v1", "source": "binance-spot-daily",
                "source_version": revision, "symbol": "BTCUSDT",
                "observed_at": pd.Timestamp(close_ms, unit="ms", tz="UTC").isoformat(),
                "available_at": computed.isoformat(), "ahr999": ahr}
    output.mkdir(parents=True, exist_ok=False)
    raw_path, envelope_path = output / "page-000.json", output / "btc-cycle-input.json"
    raw_path.write_bytes(raw)
    envelope_bytes = _encode(envelope)
    envelope_path.write_bytes(envelope_bytes)
    if raw_path.read_bytes() != raw or envelope_path.read_bytes() != envelope_bytes:
        raise ValueError("file readback mismatch")
    readback = _now()
    if readback < computed:
        raise ValueError("clock moved backward")
    manifest = {"schema": "btc-cycle-evidence/v1", "source_revision": revision, "formula_version": FORMULA_VERSION,
                "dependencies": versions, "lock_sha256": lock_hash, "endpoint": ENDPOINT, "status": 200,
                "params": params, "start_date": str(start.date()), "end_date": str(end.date()),
                "started_at": started.isoformat(), "received_at": received.isoformat(),
                "computed_at": computed.isoformat(), "readback_at": readback.isoformat(),
                "page_sha256": _hash(raw), "envelope_sha256": _hash(envelope_bytes),
                "source_authenticated": False, "clock_authenticated": False, "historical_pit_verified": False}
    (output / "manifest.json").write_bytes(_encode(manifest))
    return verify(output, decision_at=_now())


def verify(directory: Path, *, decision_at=None):
    """Offline replay; self-declared metadata is not independent provenance."""
    directory = Path(directory)
    manifest = _json((directory / "manifest.json").read_bytes())
    env_raw, raw = (directory / "btc-cycle-input.json").read_bytes(), (directory / "page-000.json").read_bytes()
    envelope = _json(env_raw)
    fields = {"contract_version", "source", "source_version", "symbol", "observed_at", "available_at", "ahr999"}
    if not isinstance(envelope, dict) or set(envelope) != fields:
        raise ValueError("envelope fields invalid")
    if (manifest.get("schema") != "btc-cycle-evidence/v1" or manifest.get("endpoint") != ENDPOINT
            or manifest.get("status") != 200 or manifest.get("source_revision") != _source()
            or manifest.get("formula_version") != FORMULA_VERSION or manifest.get("dependencies") != _versions()
            or manifest.get("lock_sha256") != _lock_hash() or manifest.get("page_sha256") != _hash(raw)
            or manifest.get("envelope_sha256") != _hash(env_raw)
            or any(manifest.get(key) is not False for key in ("source_authenticated", "clock_authenticated", "historical_pit_verified"))):
        raise ValueError("evidence binding invalid")
    started, received, computed, readback = map(_utc, (manifest["started_at"], manifest["received_at"],
                                                      manifest["computed_at"], manifest["readback_at"]))
    decision = _utc(decision_at if decision_at is not None else _now())
    if not started <= received <= computed <= readback <= decision:
        raise ValueError("evidence times invalid")
    end = decision.normalize().tz_localize(None) - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=COUNT - 1)
    if manifest.get("start_date") != str(start.date()) or manifest.get("end_date") != str(end.date()):
        raise ValueError("daily bars stale or future")
    ahr, params, close_ms = _compute(raw, start=start, end=end, received=received)
    observed = pd.Timestamp(close_ms, unit="ms", tz="UTC")
    if (manifest.get("params") != params or envelope.get("contract_version") != "btc-cycle-input/v1"
            or envelope.get("source") != "binance-spot-daily" or envelope.get("source_version") != _source()
            or envelope.get("symbol") != "BTCUSDT" or _utc(envelope["observed_at"]) != observed
            or _utc(envelope["available_at"]) != computed or not observed <= computed <= decision
            or (decision - observed).total_seconds() > MAX_AGE_SECONDS
            or isinstance(envelope.get("ahr999"), bool) or envelope.get("ahr999") != ahr):
        raise ValueError("envelope or calculation mismatch")
    return {"status": "verified_local_consistency", "source_authenticated": False,
            "clock_authenticated": False, "historical_pit_verified": False}
