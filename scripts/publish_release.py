#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.publish import run_release_activation, run_release_publish
from src.utils import get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish immutable versioned candidates. Activation is a separate Promotion Manifest-gated operation."
    )
    parser.add_argument("--config", default="config/default.yaml", help="Path to the YAML config file.")
    parser.add_argument("--mode", default=None, help="Release mode, e.g. core_major.")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--candidate-only",
        action="store_true",
        help="Publish immutable versioned candidate objects only. This is the default.",
    )
    operation.add_argument(
        "--activate",
        action="store_true",
        help="Activate a candidate after Promotion Manifest validation.",
    )
    parser.add_argument(
        "--promotion-manifest",
        default=None,
        help="Promotion Manifest required by --activate. Validation is not implemented yet.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build publish payloads without writing to GCS or Firestore.")
    parser.add_argument("--mock", action="store_true", help="Alias of --dry-run for local smoke validation.")
    parser.add_argument("--project-id", default=None, help="Optional explicit cloud project override.")
    parser.add_argument("--cloud-bucket", default=None, help="Optional explicit cloud bucket override.")
    # Backward-compat aliases
    parser.add_argument("--gcp-project-id", default=None, dest="project_id", help=argparse.SUPPRESS)
    parser.add_argument("--gcs-bucket", default=None, dest="cloud_bucket", help=argparse.SUPPRESS)
    parser.add_argument("--firestore-collection", default=None, help="Optional Firestore collection override.")
    parser.add_argument("--firestore-document", default=None, help="Optional Firestore document override.")
    parser.add_argument(
        "--contract-max-age-days",
        type=int,
        default=45,
        help="Maximum allowed output age before publish preflight treats artifacts as stale.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow publishing explicitly historical artifacts without freshness failures.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger = get_logger("publish_release")
    config = load_config(args.config)
    if args.activate and not args.promotion_manifest:
        raise ValueError("--activate requires --promotion-manifest.")
    if args.promotion_manifest and not args.activate:
        raise ValueError("--promotion-manifest is only valid with --activate.")

    if args.activate:
        run_release_activation(
            config,
            promotion_manifest_path=args.promotion_manifest,
            mode=args.mode,
            dry_run=bool(args.dry_run or args.mock),
            project_id=args.project_id,
            cloud_bucket=args.cloud_bucket,
            firestore_collection=args.firestore_collection,
            firestore_document=args.firestore_document,
            max_age_days=args.contract_max_age_days,
            require_freshness=not args.allow_stale,
        )
        return

    result = run_release_publish(
        config,
        mode=args.mode,
        dry_run=bool(args.dry_run or args.mock),
        project_id=args.project_id,
        cloud_bucket=args.cloud_bucket,
        firestore_collection=args.firestore_collection,
        firestore_document=args.firestore_document,
        max_age_days=args.contract_max_age_days,
        require_freshness=not args.allow_stale,
    )

    settings = result["settings"]
    artifacts = result["artifacts"]
    storage_layout = result["storage_layout"]

    logger.info(
        "Release candidate prepared | version=%s | mode=%s | dry_run=%s",
        artifacts.version,
        settings.mode,
        settings.dry_run,
    )
    logger.info("Release prefix: %s", storage_layout["storage_prefix_uri"])
    logger.info("Manifest written to %s", result["manifest_path"])
    logger.info(
        "Contract validation: version=%s | pool_size=%s | manifest_present=%s",
        result["validation"]["version"],
        result["validation"]["pool_size"],
        result["validation"]["manifest_present"],
    )


if __name__ == "__main__":
    main()
