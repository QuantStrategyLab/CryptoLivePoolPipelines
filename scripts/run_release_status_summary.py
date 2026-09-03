#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.release_contract import validate_release_outputs


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a structured release status summary from the canonical monthly artifacts."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing canonical release artifacts.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=45,
        help="Maximum allowed age before freshness warnings become validation errors.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow historical artifact summaries without failing freshness validation.",
    )
    parser.add_argument(
        "--ranking-preview-size",
        type=int,
        default=5,
        help="Number of top ranking rows to include in the summary preview.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return load_json(path)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ranking_preview(ranking: pd.DataFrame, size: int) -> pd.DataFrame:
    preview_size = max(0, int(size))
    if preview_size == 0:
        return ranking.head(0)

    if "current_rank" not in ranking.columns:
        return ranking.head(preview_size)

    ordered = ranking.copy()
    ordered["_current_rank_numeric"] = pd.to_numeric(ordered["current_rank"], errors="coerce")
    if ordered["_current_rank_numeric"].notna().any():
        ordered = ordered.sort_values("_current_rank_numeric", na_position="last", kind="mergesort")
    return ordered.drop(columns=["_current_rank_numeric"], errors="ignore").head(preview_size)


def build_release_status_payload(
    output_dir: Path | str,
    *,
    max_age_days: int = 45,
    require_freshness: bool = True,
    ranking_preview_size: int = 5,
    reference_date: Any = None,
) -> dict[str, Any]:
    root = Path(output_dir)
    universe = load_json(root / "latest_universe.json")
    live_pool = load_json(root / "live_pool.json")
    manifest = load_optional_json(root / "release_manifest.json") or {}
    artifact_manifest = load_optional_json(root / "artifact_manifest.json") or {}
    ranking = pd.read_csv(root / "latest_ranking.csv")

    validation = validate_release_outputs(
        root,
        expected_mode=live_pool.get("mode"),
        expected_source_project=live_pool.get("source_project"),
        expected_pool_size=live_pool.get("pool_size"),
        max_age_days=max_age_days,
        reference_date=reference_date,
        require_manifest=True,
        require_artifact_manifest=True,
        require_freshness=require_freshness,
    )

    selected_mask = ranking["selected_flag"].map(_coerce_bool) if "selected_flag" in ranking.columns else pd.Series(dtype=bool)
    ranking_preview_rows = []
    preview = _ranking_preview(ranking, ranking_preview_size)
    for _, row in preview.iterrows():
        ranking_preview_rows.append(
            {
                "current_rank": _safe_int(row.get("current_rank")),
                "symbol": str(row.get("symbol", "")).strip(),
                "final_score": float(row.get("final_score", 0.0)),
                "selected_flag": _coerce_bool(row.get("selected_flag", False)),
            }
        )

    if validation["errors"]:
        status = "error"
    elif validation["warnings"]:
        status = "warning"
    else:
        status = "ok"

    firestore = manifest.get("firestore", {}) if isinstance(manifest.get("firestore"), dict) else {}
    activation = manifest.get("activation", {}) if isinstance(manifest.get("activation"), dict) else {}

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "official_release": {
            "as_of_date": str(live_pool.get("as_of_date", "")).strip(),
            "version": str(live_pool.get("version", "")).strip(),
            "mode": str(live_pool.get("mode", "")).strip(),
            "pool_size": _safe_int(live_pool.get("pool_size")),
            "symbols": list(live_pool.get("symbols", [])),
            "source_project": str(live_pool.get("source_project", "")).strip(),
        },
        "artifact_summary": {
            "latest_universe_symbol_count": len(list(universe.get("symbols", []))),
            "latest_ranking_row_count": int(len(ranking)),
            "latest_ranking_selected_count": int(selected_mask.sum()) if not selected_mask.empty else 0,
            "artifact_contract_version": str(artifact_manifest.get("contract_version", "")).strip(),
            "ranking_preview": ranking_preview_rows,
        },
        "publish_summary": {
            "stage": str(manifest.get("stage", "activated")).strip(),
            "activation_status": str(activation.get("status", "activated")).strip(),
            "dry_run": bool(manifest.get("dry_run")),
            "publish_enabled": bool(manifest.get("publish_enabled")),
            "release_prefix": str(manifest.get("release_prefix", "")).strip(),
            "current_prefix": str(manifest.get("current_prefix", "")).strip(),
            "firestore_collection": str(firestore.get("collection", "")).strip(),
            "firestore_document": str(firestore.get("document", "")).strip(),
        },
        "validation": {
            "ok": bool(validation.get("ok")),
            "manifest_present": bool(validation.get("manifest_present")),
            "artifact_manifest_present": bool(validation.get("artifact_manifest_present")),
            "age_days": validation.get("age_days"),
            "errors": list(validation.get("errors", [])),
            "warnings": list(validation.get("warnings", [])),
        },
        "artifact_paths": {
            "latest_universe": str(root / "latest_universe.json"),
            "latest_ranking": str(root / "latest_ranking.csv"),
            "live_pool": str(root / "live_pool.json"),
            "release_manifest": str(root / "release_manifest.json"),
            "artifact_manifest": str(root / "artifact_manifest.json"),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    release = payload["official_release"]
    artifact = payload["artifact_summary"]
    publish = payload["publish_summary"]
    validation = payload["validation"]
    ranking_lines = "\n".join(
        f"- #{row['current_rank']} {row['symbol']} score={row['final_score']:.6f} selected={row['selected_flag']}"
        for row in artifact["ranking_preview"]
    ) or "- none"
    error_lines = "\n".join(f"- {item}" for item in validation["errors"]) or "- none"
    warning_lines = "\n".join(f"- {item}" for item in validation["warnings"]) or "- none"

    return f"""# Release Status Summary

Generated: {payload['generated_at_utc']}

## Official release

- Status: {payload['status']}
- As-of date: {release['as_of_date']}
- Version / mode: {release['version']} / {release['mode']}
- Pool size: {release['pool_size']}
- Symbols: {", ".join(release['symbols']) or 'n/a'}
- Source project: {release['source_project']}

## Artifact summary

- latest_universe symbol count: {artifact['latest_universe_symbol_count']}
- latest_ranking row count: {artifact['latest_ranking_row_count']}
- latest_ranking selected count: {artifact['latest_ranking_selected_count']}
- artifact contract version: {artifact['artifact_contract_version'] or 'n/a'}

### Ranking preview

{ranking_lines}

## Publish summary

- stage: {publish['stage']}
- activation_status: {publish['activation_status']}
- dry_run: {publish['dry_run']}
- publish_enabled: {publish['publish_enabled']}
- release_prefix: {publish['release_prefix'] or 'n/a'}
- current_prefix: {publish['current_prefix'] or 'n/a'}
- firestore target: {publish['firestore_collection'] or 'n/a'} / {publish['firestore_document'] or 'n/a'}

## Validation

- ok: {validation['ok']}
- manifest_present: {validation['manifest_present']}
- artifact_manifest_present: {validation['artifact_manifest_present']}
- age_days: {validation['age_days']}

### Errors

{error_lines}

### Warnings

{warning_lines}
"""


def write_outputs(payload: dict[str, Any], output_dir: Path | str) -> dict[str, Path]:
    root = Path(output_dir)
    json_path = root / "release_status_summary.json"
    md_path = root / "release_status_summary.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def main() -> None:
    args = parse_args()
    payload = build_release_status_payload(
        args.output_dir,
        max_age_days=args.max_age_days,
        require_freshness=not args.allow_stale,
        ranking_preview_size=args.ranking_preview_size,
    )
    outputs = write_outputs(payload, args.output_dir)
    print(f"status={payload['status']}")
    print(f"as_of_date={payload['official_release']['as_of_date']}")
    print(f"summary_json={outputs['json']}")
    print(f"summary_markdown={outputs['markdown']}")

    if payload["validation"]["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
