#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble the standard monthly report bundle from generated summary files."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing the generated monthly summary files.",
    )
    parser.add_argument(
        "--bundle-dir",
        default="",
        help="Optional explicit bundle directory. Defaults to <output-dir>/monthly_report_bundle.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_warning_lines(release_status: dict[str, Any], monthly_review: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    warnings: list[str] = []
    for item in list(release_status.get("validation", {}).get("errors", [])) + list(
        release_status.get("validation", {}).get("warnings", [])
    ) + list(monthly_review.get("warnings", [])):
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        warnings.append(text)
    return warnings


def build_bundle_manifest(output_dir: Path | str, bundle_dir: Path | str) -> dict[str, Any]:
    root = Path(output_dir)
    release_status = load_json(root / "release_status_summary.json")
    monthly_review = load_json(root / "monthly_review.json")

    as_of_date = str(release_status.get("official_release", {}).get("as_of_date", monthly_review.get("as_of_date", ""))).strip()
    report_month = as_of_date[:7] if len(as_of_date) >= 7 else "unknown-month"
    official_release = release_status.get("official_release", {})
    artifact_name = f"monthly-report-{as_of_date or report_month}"

    artifact_files = [
        "release_status_summary.json",
        "release_status_summary.md",
        "monthly_review.json",
        "monthly_review.md",
        "monthly_review_prompt.md",
        "monthly_telegram.txt",
        "ai_review_input.md",
        "job_summary.md",
        "monthly_report_bundle.json",
    ]

    return {
        "artifact_name": artifact_name,
        "report_month": report_month,
        "as_of_date": as_of_date,
        "mode": str(official_release.get("mode", "")),
        "pool_size": int(official_release.get("pool_size", 0) or 0),
        "symbols": list(official_release.get("symbols", [])),
        "version": str(official_release.get("version", "")),
        "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "bundle_dir": str(bundle_dir),
        "artifact_files": artifact_files,
    }


def render_job_summary(bundle: dict[str, Any], release_status: dict[str, Any], monthly_review: dict[str, Any]) -> str:
    warnings = collect_warning_lines(release_status, monthly_review)
    warning_lines = "\n".join(f"- {item}" for item in warnings[:8]) if warnings else "- none"
    return f"""# Monthly Report Bundle

- Artifact name: {bundle['artifact_name']}
- Report month: {bundle['report_month']}
- As-of date: {bundle['as_of_date']}
- Version / mode: {bundle['version']} / {bundle['mode']}
- Pool size: {bundle['pool_size']}
- Symbols: {", ".join(bundle['symbols']) or 'n/a'}
- Quick human file: `release_status_summary.md`
- AI review file: `ai_review_input.md`

## Warnings

{warning_lines}

## Bundle files

{chr(10).join(f"- `{name}`" for name in bundle['artifact_files'])}
"""


def strip_wrapped_h1(markdown: str, heading: str) -> str:
    text = markdown.strip()
    first_line, separator, rest = text.partition("\n")
    if separator and first_line.strip() == f"# {heading}":
        return rest.lstrip("\n")
    if not separator and first_line.strip() == f"# {heading}":
        return ""
    return text


def render_ai_review_input(bundle: dict[str, Any], release_status_md: str, monthly_review_md: str, telegram_text: str) -> str:
    release_status_body = strip_wrapped_h1(release_status_md, "Release Status Summary")
    monthly_review_body = strip_wrapped_h1(monthly_review_md, "Monthly Review")
    return f"""# Monthly Report Review Input

Use this file as the primary review input for the monthly upstream release package.

## Bundle metadata

- Artifact name: {bundle['artifact_name']}
- Report month: {bundle['report_month']}
- As-of date: {bundle['as_of_date']}
- Version / mode: {bundle['version']} / {bundle['mode']}
- Pool size: {bundle['pool_size']}
- Symbols: {", ".join(bundle['symbols']) or 'n/a'}

## Review intent

- This is an upstream selector review for CryptoLivePoolPipelines, not a downstream execution report.
- The main question is whether the current monthly 5-symbol pool still looks like a sound production selector output, and what additional research evidence is still missing.
- Shadow / challenger coverage should be used for strategy-optimization judgment when available. If missing, treat optimization evidence as incomplete rather than forcing a strong conclusion.
- Downstream BinancePlatform consumes this pool monthly and then applies its own execution logic on top.

## Strategy review questions

1. Does the official pool look internally consistent with the ranking preview and release metadata?
2. Are score spread and selected symbols reasonable for a Binance Spot mainstream leader selector?
3. Is shadow / challenger evidence present, and if not, what is the highest-value missing comparison?
4. What are the most useful next low-risk research directions before changing production selector logic?

## Release Status Summary

{release_status_body}

## Monthly Review

{monthly_review_body}

## Telegram Preview

```text
{telegram_text.strip()}
```
"""


def write_bundle(output_dir: Path | str, bundle_dir: Path | str) -> dict[str, Any]:
    root = Path(output_dir)
    bundle_root = Path(bundle_dir)
    bundle_root.mkdir(parents=True, exist_ok=True)

    release_status = load_json(root / "release_status_summary.json")
    monthly_review = load_json(root / "monthly_review.json")
    release_status_md = load_text(root / "release_status_summary.md")
    monthly_review_md = load_text(root / "monthly_review.md")
    telegram_text = load_text(root / "monthly_telegram.txt")

    bundle = build_bundle_manifest(root, bundle_root)

    for filename in (
        "release_status_summary.json",
        "release_status_summary.md",
        "monthly_review.json",
        "monthly_review.md",
        "monthly_review_prompt.md",
        "monthly_telegram.txt",
    ):
        shutil.copy2(root / filename, bundle_root / filename)

    job_summary_path = bundle_root / "job_summary.md"
    ai_review_path = bundle_root / "ai_review_input.md"
    manifest_path = bundle_root / "monthly_report_bundle.json"

    job_summary_path.write_text(render_job_summary(bundle, release_status, monthly_review), encoding="utf-8")
    ai_review_path.write_text(
        render_ai_review_input(bundle, release_status_md, monthly_review_md, telegram_text),
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "bundle": bundle,
        "job_summary": job_summary_path,
        "ai_review_input": ai_review_path,
        "manifest": manifest_path,
    }


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    bundle_dir = Path(args.bundle_dir).resolve() if args.bundle_dir else output_dir / "monthly_report_bundle"
    outputs = write_bundle(output_dir, bundle_dir)
    bundle = outputs["bundle"]

    print(f"artifact_name={bundle['artifact_name']}")
    print(f"report_month={bundle['report_month']}")
    print(f"as_of_date={bundle['as_of_date']}")
    print(f"bundle_dir={bundle_dir}")
    print(f"manifest={outputs['manifest']}")
    print(f"job_summary={outputs['job_summary']}")
    print(f"ai_review_input={outputs['ai_review_input']}")


if __name__ == "__main__":
    main()
