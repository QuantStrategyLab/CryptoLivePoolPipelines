#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import write_json


def build_run_url(explicit_run_url: str | None = None) -> str | None:
    if explicit_run_url:
        return explicit_run_url
    run_id = os.getenv("GITHUB_RUN_ID")
    repository = os.getenv("GITHUB_REPOSITORY")
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    if run_id and repository:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a small release heartbeat JSON for the logs branch.")
    parser.add_argument("--manifest", default="data/output/release_manifest.json", help="Path to release_manifest.json.")
    parser.add_argument("--output-dir", required=True, help="Root directory where monthly/<version>.json will be written.")
    parser.add_argument("--run-id", default=None, help="Optional workflow run id override.")
    parser.add_argument("--run-url", default=None, help="Optional workflow run URL override.")
    return parser.parse_args()


def build_heartbeat(
    manifest: dict[str, object],
    live_pool: dict[str, object] | None = None,
    *,
    run_id: str | None = None,
    run_url: str | None = None,
) -> dict[str, object]:
    if manifest.get("stage") == "candidate":
        if not isinstance(live_pool, dict):
            raise ValueError("Candidate heartbeat requires live_pool.json.")
        artifacts = manifest.get("artifacts")
        live_pool_artifact = (
            artifacts.get("live_pool", {}) if isinstance(artifacts, dict) else {}
        )
        release_uri = (
            live_pool_artifact.get("release_uri", "")
            if isinstance(live_pool_artifact, dict)
            else ""
        )
        if not isinstance(release_uri, str) or "/" not in release_uri:
            raise ValueError("Candidate heartbeat requires a versioned live_pool release_uri.")
        return {
            "stage": "candidate",
            "version": str(manifest["version"]),
            "as_of_date": live_pool["as_of_date"],
            "mode": live_pool["mode"],
            "pool_size": live_pool["pool_size"],
            "symbols": live_pool["symbols"],
            "storage_prefix": release_uri.rsplit("/", 1)[0],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "workflow_run_id": str(run_id) if run_id is not None else None,
            "workflow_run_url": run_url,
        }

    firestore = manifest.get("firestore")
    if not isinstance(firestore, dict) or not isinstance(firestore.get("payload"), dict):
        raise ValueError("Activated release heartbeat requires firestore.payload.")
    firestore_payload = firestore["payload"]
    return {
        "stage": "activated",
        "version": str(manifest["version"]),
        "as_of_date": firestore_payload["as_of_date"],
        "mode": firestore_payload["mode"],
        "pool_size": firestore_payload["pool_size"],
        "symbols": firestore_payload["symbols"],
        "storage_prefix": firestore_payload["storage_prefix"],
        "generated_at": firestore_payload["generated_at"],
        "workflow_run_id": str(run_id) if run_id is not None else None,
        "workflow_run_url": run_url,
    }


def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    live_pool_path = manifest_path.with_name("live_pool.json")
    live_pool = (
        json.loads(live_pool_path.read_text(encoding="utf-8"))
        if live_pool_path.exists()
        else None
    )
    version = str(manifest["version"])
    output_path = Path(args.output_dir) / "monthly" / f"{version}.json"

    run_id = args.run_id or os.getenv("GITHUB_RUN_ID")
    run_url = build_run_url(args.run_url)
    heartbeat = build_heartbeat(
        manifest,
        live_pool,
        run_id=str(run_id) if run_id is not None else None,
        run_url=run_url,
    )
    write_json(output_path, heartbeat)
    print(output_path)


if __name__ == "__main__":
    main()
