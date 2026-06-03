#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a short Telegram notification for monthly build/publish health."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing monthly shadow build outputs.",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the message and skip Telegram sending.",
    )
    parser.add_argument(
        "--output-path",
        default="",
        help="Optional path to write the rendered message for bundle packaging.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_track_summary(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def build_health_payload(output_dir: Path | str) -> dict[str, Any]:
    root = Path(output_dir)
    summary_path = root / "monthly_shadow_build_summary.json"
    release_status_summary_path = root / "release_status_summary.json"
    live_pool_path = root / "live_pool.json"
    manifest_path = root / "release_manifest.json"
    track_summary_path = root / "shadow_candidate_tracks" / "track_summary.csv"

    warnings: list[str] = []
    summary = load_json(summary_path)
    release_status_summary = load_json(release_status_summary_path)
    live_pool = load_json(live_pool_path)
    manifest = load_json(manifest_path)
    track_rows = load_track_summary(track_summary_path)

    if live_pool is None:
        warnings.append("missing live_pool.json")
    if manifest is None:
        warnings.append("missing release_manifest.json")

    as_of_date = ""
    if release_status_summary is not None:
        as_of_date = str(release_status_summary.get("official_release", {}).get("as_of_date", "")).strip()
    if not as_of_date and summary is not None:
        as_of_date = str(summary.get("as_of_date", "")).strip()
    if not as_of_date and live_pool is not None:
        as_of_date = str(live_pool.get("as_of_date", "")).strip()
    if not as_of_date:
        as_of_date = "unknown"

    official = summary.get("official_baseline", {}) if summary is not None else {}
    official_release = release_status_summary.get("official_release", {}) if release_status_summary is not None else {}
    validation = release_status_summary.get("validation", {}) if release_status_summary is not None else {}
    track_map = {row.get("track_id", ""): row for row in track_rows}
    official_track = track_map.get("official_baseline", {})
    challenger_track = track_map.get("challenger_topk_60", {})

    if summary is not None and not track_rows:
        warnings.append("monthly shadow summary exists but track_summary.csv is missing")
    if track_rows:
        for track_id, track in (
            ("official_baseline", official_track),
            ("challenger_topk_60", challenger_track),
        ):
            if not track:
                warnings.append(f"missing track summary row for {track_id}")
                continue
            if str(track.get("last_as_of_date", "")).strip() != as_of_date:
                warnings.append(f"{track_id} last_as_of_date != {as_of_date}")

    for item in validation.get("errors", []):
        warnings.append(f"release_status_summary error: {item}")
    for item in validation.get("warnings", []):
        warnings.append(f"release_status_summary warning: {item}")

    status = "ok" if not warnings else "warning"
    manifest_present = manifest is not None
    manifest_dry_run = bool(manifest.get("dry_run")) if manifest_present else None

    return {
        "status": status,
        "as_of_date": as_of_date,
        "official_baseline": {
            "profile": str(official.get("profile", "baseline_blended_rank")),
            "version": str(official_release.get("version", official.get("version", live_pool.get("version", "") if live_pool else ""))),
            "mode": str(official_release.get("mode", official.get("mode", live_pool.get("mode", "") if live_pool else ""))),
            "pool_size": int(official_release.get("pool_size", official.get("pool_size", live_pool.get("pool_size", 0) if live_pool else 0))),
        },
        "manifest": {
            "present": manifest_present,
            "dry_run": manifest_dry_run,
            "path": str(manifest_path),
        },
        "validation": {
            "ok": bool(validation.get("ok")) if release_status_summary is not None else None,
            "manifest_present": bool(validation.get("manifest_present")) if release_status_summary is not None else manifest_present,
            "age_days": validation.get("age_days") if release_status_summary is not None else None,
        },
        "shadow_tracks": {
            "official_baseline": {
                "available": bool(official_track),
                "last_as_of_date": str(official_track.get("last_as_of_date", "")),
                "release_count": int(official_track.get("release_count", 0) or 0),
            },
            "challenger_topk_60": {
                "available": bool(challenger_track),
                "last_as_of_date": str(challenger_track.get("last_as_of_date", "")),
                "release_count": int(challenger_track.get("release_count", 0) or 0),
            },
        },
        "warnings": warnings,
    }


def format_message(payload: dict[str, Any]) -> str:
    official = payload["official_baseline"]
    manifest = payload["manifest"]
    validation = payload["validation"]
    shadow = payload["shadow_tracks"]
    shadow_line = (
        "shadow: "
        f"official_baseline last={shadow['official_baseline']['last_as_of_date']} "
        f"releases={shadow['official_baseline']['release_count']}; "
        f"challenger_topk_60 last={shadow['challenger_topk_60']['last_as_of_date']} "
        f"releases={shadow['challenger_topk_60']['release_count']}"
        if shadow["official_baseline"]["available"] or shadow["challenger_topk_60"]["available"]
        else "shadow: not_generated_in_this_run"
    )
    lines = [
        "CryptoLivePoolPipelines monthly release",
        f"status: {payload['status']}",
        f"as_of_date: {payload['as_of_date']}",
        (
            "official: "
            f"profile={official['profile']} "
            f"version={official['version']} "
            f"mode={official['mode']} "
            f"pool_size={official['pool_size']}"
        ),
        shadow_line,
        f"manifest: {'present' if manifest['present'] else 'missing'} dry_run={manifest['dry_run']}",
    ]
    if validation["ok"] is not None:
        lines.append(
            f"validation: ok={validation['ok']} manifest_present={validation['manifest_present']} age_days={validation['age_days']}"
        )
    if payload["warnings"]:
        lines.append("warnings: " + " | ".join(payload["warnings"][:4]))
    else:
        lines.append("warnings: none")
    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, message: str) -> None:
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"telegram api returned not ok: {payload}")


def main() -> None:
    args = parse_args()
    payload = build_health_payload(args.output_dir)
    message = format_message(payload)
    print(message)
    if args.output_path:
        output_path = Path(args.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(message + "\n", encoding="utf-8")

    if args.print_only:
        print("telegram_send=skipped print_only")
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("GLOBAL_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("telegram_send=skipped missing TELEGRAM_BOT_TOKEN or GLOBAL_TELEGRAM_CHAT_ID")
        return

    try:
        send_telegram_message(token, chat_id, message)
    except Exception as exc:
        print(f"telegram_send=failed {exc}")
        return
    print("telegram_send=ok")


if __name__ == "__main__":
    main()
