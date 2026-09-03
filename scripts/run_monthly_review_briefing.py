#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reporting-only monthly review package from monthly build outputs."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing monthly build outputs.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return load_json(path)


def load_track_summary(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV is empty: {path}")
    return rows


def load_optional_track_summary(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return load_track_summary(path)


def load_optional_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number:
        return None
    return number


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _display_float(value: float | None) -> str:
    return f"{value:.6f}" if value is not None else "n/a"


def resolve_as_of_date(
    summary: dict[str, Any],
    release_status_summary: dict[str, Any],
    live_pool: dict[str, Any],
) -> str:
    return str(
        summary.get(
            "as_of_date",
            release_status_summary.get("official_release", {}).get("as_of_date", live_pool.get("as_of_date", "")),
        )
    ).strip()


def build_review_inputs(output_dir: Path | str) -> dict[str, Any]:
    root = Path(output_dir)
    summary_path = root / "monthly_shadow_build_summary.json"
    release_status_summary_path = root / "release_status_summary.json"
    live_pool_path = root / "live_pool.json"
    manifest_path = root / "release_manifest.json"
    track_summary_path = root / "shadow_candidate_tracks" / "track_summary.csv"
    latest_ranking_path = root / "latest_ranking.csv"

    return {
        "summary": load_optional_json(summary_path),
        "release_status_summary": load_optional_json(release_status_summary_path),
        "live_pool": load_json(live_pool_path),
        "manifest": load_json(manifest_path),
        "track_rows": load_optional_track_summary(track_summary_path),
        "latest_ranking_rows": load_optional_csv(latest_ranking_path),
        "paths": {
            "monthly_shadow_build_summary": str(summary_path),
            "release_status_summary": str(release_status_summary_path),
            "live_pool": str(live_pool_path),
            "release_manifest": str(manifest_path),
            "track_summary": str(track_summary_path),
            "latest_ranking": str(latest_ranking_path),
        },
        "availability": {
            "monthly_shadow_build_summary": summary_path.exists(),
            "release_status_summary": release_status_summary_path.exists(),
            "track_summary": track_summary_path.exists(),
        },
    }


def derive_warnings(inputs: dict[str, Any]) -> list[str]:
    summary = inputs["summary"] or {}
    release_status_summary = inputs["release_status_summary"] or {}
    live_pool = inputs["live_pool"]
    manifest = inputs["manifest"]
    track_rows = inputs["track_rows"]
    availability = inputs["availability"]

    warnings: list[str] = []
    as_of_date = resolve_as_of_date(summary, release_status_summary, live_pool)
    version = str(live_pool.get("version", "")).strip()
    mode = str(live_pool.get("mode", "")).strip()

    if not as_of_date:
        warnings.append("missing upstream as_of_date")
    if not version:
        warnings.append("missing live_pool version")
    if not mode:
        warnings.append("missing live_pool mode")

    if str(manifest.get("as_of_date", "")).strip() != as_of_date:
        warnings.append("release_manifest as_of_date does not match monthly summary")
    if str(manifest.get("version", "")).strip() != version:
        warnings.append("release_manifest version does not match live_pool version")
    if str(manifest.get("mode", "")).strip() != mode:
        warnings.append("release_manifest mode does not match live_pool mode")

    if availability["monthly_shadow_build_summary"] and not availability["track_summary"]:
        warnings.append("monthly shadow summary exists but track_summary.csv is missing")
    if availability["track_summary"] and not availability["monthly_shadow_build_summary"]:
        warnings.append("track_summary.csv exists but monthly_shadow_build_summary.json is missing")

    if track_rows:
        track_map = {row.get("track_id", ""): row for row in track_rows}
        for track_id in ("official_baseline", "challenger_topk_60"):
            row = track_map.get(track_id)
            if row is None:
                warnings.append(f"missing track summary row for {track_id}")
                continue
            if str(row.get("last_as_of_date", "")).strip() != as_of_date:
                warnings.append(f"{track_id} last_as_of_date does not match monthly summary")

    if not live_pool.get("symbols"):
        warnings.append("live_pool symbols are empty")
    if _safe_int(live_pool.get("pool_size")) != len(live_pool.get("symbols", [])):
        warnings.append("live_pool pool_size does not match symbols length")

    validation = release_status_summary.get("validation", {})
    for item in validation.get("errors", []):
        warnings.append(f"release_status_summary error: {item}")
    for item in validation.get("warnings", []):
        warnings.append(f"release_status_summary warning: {item}")

    return warnings


def _resolve_release_index_path(output_dir: Path, track_summary_path: Path, path_value: str) -> Path | None:
    raw_path = Path(path_value)
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.extend(
            [
                output_dir / raw_path,
                track_summary_path.parent / raw_path,
                PROJECT_ROOT / raw_path,
            ]
        )
        path_text = str(raw_path)
        if path_text.startswith("data/output/"):
            candidates.insert(0, output_dir / path_text.removeprefix("data/output/"))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _latest_track_release(
    output_dir: Path,
    track_summary_path: Path,
    track_row: dict[str, str],
    as_of_date: str,
    official_symbols: list[str],
) -> dict[str, Any]:
    index_path = _resolve_release_index_path(output_dir, track_summary_path, track_row.get("release_index_path", ""))
    preview = {
        "available": False,
        "release_index_path": str(index_path) if index_path is not None else str(track_row.get("release_index_path", "")),
        "as_of_date": "",
        "version": "",
        "symbols": [],
        "overlap_with_official": 0,
        "only_in_track": [],
        "only_in_official": list(official_symbols),
        "pool_stability": None,
        "pool_churn": None,
    }
    if index_path is None:
        return preview

    rows = load_optional_csv(index_path)
    if not rows:
        return preview

    matching = [row for row in rows if str(row.get("as_of_date", "")).strip() == as_of_date]
    release_row = matching[-1] if matching else rows[-1]
    symbols = [symbol for symbol in str(release_row.get("symbols", "")).split("|") if symbol]
    official_set = set(official_symbols)
    symbol_set = set(symbols)

    preview.update(
        {
            "available": True,
            "as_of_date": str(release_row.get("as_of_date", "")),
            "version": str(release_row.get("version", "")),
            "symbols": symbols,
            "overlap_with_official": len(symbol_set & official_set),
            "only_in_track": [symbol for symbol in symbols if symbol not in official_set],
            "only_in_official": [symbol for symbol in official_symbols if symbol not in symbol_set],
            "pool_stability": _safe_float(release_row.get("pool_stability")),
            "pool_churn": _safe_float(release_row.get("pool_churn")),
        }
    )
    return preview


def build_track_release_previews(inputs: dict[str, Any], as_of_date: str, official_symbols: list[str]) -> dict[str, Any]:
    root = Path(inputs["paths"]["latest_ranking"]).parent
    track_summary_path = Path(inputs["paths"]["track_summary"])
    track_map = {row.get("track_id", ""): row for row in inputs["track_rows"]}
    previews: dict[str, Any] = {}
    for track_id in ("official_baseline", "challenger_topk_60"):
        row = track_map.get(track_id)
        if row is None:
            previews[track_id] = {"available": False, "symbols": []}
            continue
        previews[track_id] = _latest_track_release(root, track_summary_path, row, as_of_date, official_symbols)
    return previews


def _ranking_row_summary(row: dict[str, str] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "rank": _safe_int(row.get("current_rank")),
        "symbol": str(row.get("symbol", "")),
        "final_score": _safe_float(row.get("final_score")),
        "confidence": _safe_float(row.get("confidence")),
        "liquidity_stability": _safe_float(row.get("liquidity_stability")),
        "avg_quote_vol_180": _safe_float(row.get("avg_quote_vol_180")),
        "selected_flag": _coerce_bool(row.get("selected_flag")),
    }


def build_selection_boundary(ranking_rows: list[dict[str, str]], pool_size: int) -> dict[str, Any]:
    tie_break_order = ["final_score", "confidence", "liquidity_stability", "avg_quote_vol_180", "symbol"]
    if not ranking_rows:
        return {
            "available": False,
            "tie_break_order": tie_break_order,
            "selected_cutoff": None,
            "next_candidate": None,
            "score_gap_to_next": None,
        }

    def sort_key(row: dict[str, str]) -> tuple[float, float, float, float, float, str]:
        rank_value = _safe_float(row.get("current_rank"))
        if rank_value is not None and rank_value > 0:
            return (0.0, rank_value, 0.0, 0.0, 0.0, str(row.get("symbol", "")).upper())
        return (
            1.0,
            -(_safe_float(row.get("final_score")) or float("-inf")),
            -(_safe_float(row.get("confidence")) or float("-inf")),
            -(_safe_float(row.get("liquidity_stability")) or float("-inf")),
            -(_safe_float(row.get("avg_quote_vol_180")) or float("-inf")),
            str(row.get("symbol", "")).upper(),
        )

    ordered = sorted(ranking_rows, key=sort_key)
    selected_rows = [row for row in ordered if _coerce_bool(row.get("selected_flag"))]
    cutoff_index = min(max(len(selected_rows), pool_size), len(ordered))
    selected_cutoff = ordered[cutoff_index - 1] if cutoff_index else None
    next_candidate = ordered[cutoff_index] if cutoff_index < len(ordered) else None
    cutoff_score = _safe_float(selected_cutoff.get("final_score")) if selected_cutoff is not None else None
    next_score = _safe_float(next_candidate.get("final_score")) if next_candidate is not None else None
    score_gap = cutoff_score - next_score if cutoff_score is not None and next_score is not None else None

    return {
        "available": True,
        "tie_break_order": tie_break_order,
        "selected_cutoff": _ranking_row_summary(selected_cutoff),
        "next_candidate": _ranking_row_summary(next_candidate),
        "score_gap_to_next": score_gap,
    }


def require_shadow_outputs(inputs: dict[str, Any]) -> None:
    availability = inputs["availability"]
    missing_items: list[str] = []
    if not availability["monthly_shadow_build_summary"]:
        missing_items.append("monthly_shadow_build_summary.json")
    if not availability["track_summary"]:
        missing_items.append("shadow_candidate_tracks/track_summary.csv")

    if missing_items:
        missing_text = ", ".join(missing_items)
        raise RuntimeError(
            "monthly shadow build outputs are required before generating the monthly review package: "
            f"{missing_text}"
        )


def build_review_questions() -> list[str]:
    return [
        "Does the official baseline publish chain look internally consistent for this month?",
        "If generated, are the shadow candidate track artifacts current and aligned with the same as_of_date?",
        "Is there any operational mismatch between the monthly summary, live pool, and release manifest?",
        "Before the next monthly cycle, what operator follow-up items should be tracked explicitly?",
    ]


def build_review_payload(inputs: dict[str, Any]) -> dict[str, Any]:
    summary = inputs["summary"] or {}
    release_status_summary = inputs["release_status_summary"] or {}
    live_pool = inputs["live_pool"]
    manifest = inputs["manifest"]
    track_rows = inputs["track_rows"]
    track_map = {row.get("track_id", ""): row for row in track_rows}
    official_track = track_map.get("official_baseline", {})
    challenger_track = track_map.get("challenger_topk_60", {})

    warnings = derive_warnings(inputs)
    official_baseline = summary.get("official_baseline", {})
    release_official = release_status_summary.get("official_release", {})
    shadow_available = bool(track_rows)
    as_of_date = resolve_as_of_date(summary, release_status_summary, live_pool)
    official_symbols = list(release_official.get("symbols", live_pool.get("symbols", [])))
    pool_size = _safe_int(
        official_baseline.get("pool_size", release_official.get("pool_size", live_pool.get("pool_size", 0)))
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of_date,
        "status": "warning" if warnings else "ok",
        "official_baseline": {
            "profile": str(official_baseline.get("profile", official_track.get("profile_name", "baseline_blended_rank"))),
            "version": str(official_baseline.get("version", release_official.get("version", live_pool.get("version", "")))),
            "mode": str(official_baseline.get("mode", release_official.get("mode", live_pool.get("mode", "")))),
            "pool_size": pool_size,
            "symbols": official_symbols,
            "source_project": str(release_official.get("source_project", live_pool.get("source_project", ""))),
        },
        "publish": {
            "stage": str(manifest.get("stage", "activated")),
            "activation_status": str(manifest.get("activation", {}).get("status", "activated")),
            "dry_run": bool(manifest.get("dry_run")),
            "publish_enabled": bool(manifest.get("publish_enabled")),
            "release_prefix": str(manifest.get("release_prefix", "")),
            "current_prefix": str(manifest.get("current_prefix", "")),
            "firestore_collection": str(manifest.get("firestore", {}).get("collection", "")),
            "firestore_document": str(manifest.get("firestore", {}).get("document", "")),
        },
        "tracks": {
            "official_baseline": {
                "available": bool(official_track),
                "release_count": _safe_int(official_track.get("release_count", 0)),
                "first_as_of_date": str(official_track.get("first_as_of_date", "")),
                "last_as_of_date": str(official_track.get("last_as_of_date", "")),
                "candidate_status": str(official_track.get("candidate_status", "")),
                "release_index_path": str(official_track.get("release_index_path", "")),
            },
            "challenger_topk_60": {
                "available": bool(challenger_track),
                "release_count": _safe_int(challenger_track.get("release_count", 0)),
                "first_as_of_date": str(challenger_track.get("first_as_of_date", "")),
                "last_as_of_date": str(challenger_track.get("last_as_of_date", "")),
                "candidate_status": str(challenger_track.get("candidate_status", "")),
                "release_index_path": str(challenger_track.get("release_index_path", "")),
            },
        },
        "track_release_previews": build_track_release_previews(inputs, as_of_date, official_symbols),
        "selection_boundary": build_selection_boundary(inputs["latest_ranking_rows"], pool_size),
        "shadow_analysis_available": shadow_available,
        "warnings": warnings,
        "operator_checklist": [
            "Run `make monthly-shadow-build` before generating the extended shadow review package.",
            "Confirm `live_pool.json` and `release_manifest.json` point to the same month; if generated, confirm `track_summary.csv` matches too.",
            "Review warning lines before any manual publish or communication follow-up.",
            "Keep the official baseline as the only production reference unless a separate governance process approves a change.",
        ],
        "review_questions": build_review_questions(),
        "source_files": inputs["paths"],
    }


def render_review_markdown(payload: dict[str, Any]) -> str:
    official = payload["official_baseline"]
    publish = payload["publish"]
    tracks = payload["tracks"]
    previews = payload["track_release_previews"]
    boundary = payload["selection_boundary"]
    official_track_line = (
        f"releases={tracks['official_baseline']['release_count']} first={tracks['official_baseline']['first_as_of_date']} "
        f"last={tracks['official_baseline']['last_as_of_date']} status={tracks['official_baseline']['candidate_status']}"
        if tracks["official_baseline"]["available"]
        else "not generated in this run"
    )
    challenger_track_line = (
        f"releases={tracks['challenger_topk_60']['release_count']} first={tracks['challenger_topk_60']['first_as_of_date']} "
        f"last={tracks['challenger_topk_60']['last_as_of_date']} status={tracks['challenger_topk_60']['candidate_status']}"
        if tracks["challenger_topk_60"]["available"]
        else "not generated in this run"
    )
    warning_lines = "\n".join(f"- {item}" for item in payload["warnings"]) if payload["warnings"] else "- none"
    checklist_lines = "\n".join(f"{idx}. {item}" for idx, item in enumerate(payload["operator_checklist"], start=1))
    symbols = ", ".join(official["symbols"]) if official["symbols"] else "n/a"
    comparison_lines = "\n".join(
        render_track_preview_line(track_id, preview)
        for track_id, preview in previews.items()
    )
    boundary_lines = render_boundary_lines(boundary)

    return f"""# Monthly Review

Generated: {payload['generated_at_utc']}

## Current release status

- Status: {payload['status']}
- As-of date: {payload['as_of_date']}
- Official profile: {official['profile']}
- Official version / mode: {official['version']} / {official['mode']}
- Official pool size: {official['pool_size']}
- Official symbols: {symbols}
- Source project: {official['source_project']}

## Publish summary

- stage: {publish['stage']}
- activation_status: {publish['activation_status']}
- dry_run: {publish['dry_run']}
- publish_enabled: {publish['publish_enabled']}
- release_prefix: {publish['release_prefix'] or 'n/a'}
- current_prefix: {publish['current_prefix'] or 'n/a'}
- firestore target: {publish['firestore_collection'] or 'n/a'} / {publish['firestore_document'] or 'n/a'}

## Track coverage

- official_baseline: {official_track_line}
- challenger_topk_60: {challenger_track_line}

## Track release comparison

{comparison_lines}

## Selection boundary

{boundary_lines}

## Warnings

{warning_lines}

## Operator checklist

{checklist_lines}
"""


def render_track_preview_line(track_id: str, preview: dict[str, Any]) -> str:
    if not preview.get("available"):
        return f"- {track_id}: release preview unavailable"
    symbols = ", ".join(preview.get("symbols", [])) or "n/a"
    only_in_track = ", ".join(preview.get("only_in_track", [])) or "none"
    only_in_official = ", ".join(preview.get("only_in_official", [])) or "none"
    return (
        f"- {track_id}: as_of={preview['as_of_date']} symbols={symbols} "
        f"overlap_with_official={preview['overlap_with_official']} "
        f"only_in_track={only_in_track} only_in_official={only_in_official} "
        f"stability={_display_float(preview.get('pool_stability'))} churn={_display_float(preview.get('pool_churn'))}"
    )


def render_boundary_lines(boundary: dict[str, Any]) -> str:
    tie_break = " > ".join(boundary["tie_break_order"])
    if not boundary.get("available"):
        return f"- ranking preview unavailable\n- deterministic tie-break order: {tie_break}"

    cutoff = boundary.get("selected_cutoff")
    next_candidate = boundary.get("next_candidate")
    cutoff_line = (
        f"rank={cutoff['rank']} symbol={cutoff['symbol']} score={_display_float(cutoff['final_score'])}"
        if cutoff
        else "n/a"
    )
    next_line = (
        f"rank={next_candidate['rank']} symbol={next_candidate['symbol']} score={_display_float(next_candidate['final_score'])}"
        if next_candidate
        else "n/a"
    )
    return "\n".join(
        [
            f"- selected cutoff: {cutoff_line}",
            f"- next candidate: {next_line}",
            f"- score gap to next: {_display_float(boundary.get('score_gap_to_next'))}",
            f"- deterministic tie-break order: {tie_break}",
        ]
    )


def render_review_prompt(payload: dict[str, Any]) -> str:
    questions = "\n".join(f"{idx}. {item}" for idx, item in enumerate(payload["review_questions"], start=1))
    warnings = "\n".join(f"- {item}" for item in payload["warnings"]) if payload["warnings"] else "- none"
    challenger = payload["track_release_previews"].get("challenger_topk_60", {})
    challenger_symbols = ", ".join(challenger.get("symbols", [])) if challenger.get("available") else "n/a"
    next_candidate = payload["selection_boundary"].get("next_candidate")
    next_candidate_text = (
        f"{next_candidate['symbol']} score={_display_float(next_candidate['final_score'])}"
        if next_candidate
        else "n/a"
    )
    return f"""Monthly release review prompt

Context:
- This package is reporting-only.
- official_baseline remains the production reference.
- challenger_topk_60 remains a shadow candidate artifact when generated.
- No automatic switch or publish decision should be inferred from this file alone.

Current month:
- as_of_date: {payload['as_of_date']}
- status: {payload['status']}
- official version: {payload['official_baseline']['version']}
- official mode: {payload['official_baseline']['mode']}
- official symbols: {", ".join(payload['official_baseline']['symbols']) or 'n/a'}
- challenger_topk_60 symbols: {challenger_symbols}
- next candidate after selected pool: {next_candidate_text}

Warnings:
{warnings}

Questions:
{questions}
"""


def write_outputs(payload: dict[str, Any], output_dir: Path | str) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    review_md_path = root / "monthly_review.md"
    review_json_path = root / "monthly_review.json"
    review_prompt_path = root / "monthly_review_prompt.md"

    review_md_path.write_text(render_review_markdown(payload), encoding="utf-8")
    review_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    review_prompt_path.write_text(render_review_prompt(payload), encoding="utf-8")
    return {
        "review_markdown": review_md_path,
        "review_json": review_json_path,
        "review_prompt": review_prompt_path,
    }


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    inputs = build_review_inputs(output_dir)
    require_shadow_outputs(inputs)
    payload = build_review_payload(inputs)
    outputs = write_outputs(payload, output_dir)

    print(f"status={payload['status']}")
    print(f"as_of_date={payload['as_of_date']}")
    print(f"review_markdown={outputs['review_markdown']}")
    print(f"review_json={outputs['review_json']}")
    print(f"review_prompt={outputs['review_prompt']}")


if __name__ == "__main__":
    main()
