from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .release_contract import assert_release_outputs
from .utils import read_json, write_json


REQUIRED_OUTPUT_FILES = (
    "latest_universe.json",
    "latest_ranking.csv",
    "live_pool.json",
    "live_pool_legacy.json",
    "artifact_manifest.json",
)


@dataclass(frozen=True)
class PublishSettings:
    enabled: bool
    dry_run: bool
    mode: str
    project_id: str | None
    cloud_bucket: str | None
    cloud_root_prefix: str
    firestore_collection: str
    firestore_document: str
    source_project: str
    upload_current_pointer: bool


@dataclass(frozen=True)
class ReleaseArtifacts:
    as_of_date: str
    version: str
    output_dir: Path
    latest_universe_path: Path
    latest_ranking_path: Path
    live_pool_path: Path
    live_pool_legacy_path: Path
    artifact_manifest_path: Path
    latest_universe: dict[str, Any]
    latest_ranking: pd.DataFrame
    live_pool: dict[str, Any]
    live_pool_legacy: dict[str, Any]
    artifact_manifest: dict[str, Any]


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_release_version(as_of_date: str, mode: str) -> str:
    return f"{as_of_date}-{mode}"


def resolve_publish_settings(
    config: dict[str, Any],
    *,
    mode: str | None = None,
    dry_run: bool = False,
    project_id: str | None = None,
    cloud_bucket: str | None = None,
    firestore_collection: str | None = None,
    firestore_document: str | None = None,
) -> PublishSettings:
    publish_cfg = config.get("publish", {})
    effective_mode = (
        mode
        or os.getenv("PUBLISH_MODE")
        or publish_cfg.get("mode")
        or config.get("universe", {}).get("live_mode")
        or "core_major"
    )
    enabled = parse_bool(os.getenv("PUBLISH_ENABLED"), publish_cfg.get("enabled", False))
    effective_dry_run = bool(dry_run or not enabled)
    return PublishSettings(
        enabled=enabled,
        dry_run=effective_dry_run,
        mode=str(effective_mode),
        project_id=project_id or os.getenv("CLOUD_PROJECT_ID") or os.getenv("GCP_PROJECT_ID") or publish_cfg.get("project_id"),
        cloud_bucket=cloud_bucket or os.getenv("CLOUD_BUCKET") or os.getenv("GCS_BUCKET") or publish_cfg.get("cloud_bucket"),
        cloud_root_prefix=str(publish_cfg.get("cloud_root_prefix", "crypto-live-pool-pipelines")).strip("/"),
        firestore_collection=(
            firestore_collection
            or os.getenv("FIRESTORE_COLLECTION")
            or publish_cfg.get("firestore_collection", "strategy")
        ),
        firestore_document=(
            firestore_document
            or os.getenv("FIRESTORE_DOCUMENT")
            or publish_cfg.get("firestore_document", "CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL")
        ),
        source_project=str(publish_cfg.get("source_project", config.get("project", {}).get("name", "crypto-live-pool-pipelines"))),
        upload_current_pointer=parse_bool(
            os.getenv("UPLOAD_CURRENT_POINTER"),
            publish_cfg.get("upload_current_pointer", True),
        ),
    )


def _require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required release artifact is missing: {path}")


def load_release_artifacts(output_dir: Path | str, mode: str) -> ReleaseArtifacts:
    output_path = Path(output_dir)
    paths = {name: output_path / name for name in REQUIRED_OUTPUT_FILES}
    for artifact_path in paths.values():
        _require_file(artifact_path)

    latest_universe = read_json(paths["latest_universe.json"])
    live_pool = read_json(paths["live_pool.json"])
    live_pool_legacy = read_json(paths["live_pool_legacy.json"])
    artifact_manifest = read_json(paths["artifact_manifest.json"])
    latest_ranking = pd.read_csv(paths["latest_ranking.csv"])

    as_of_values = {
        str(latest_universe.get("as_of_date")),
        str(live_pool.get("as_of_date")),
        str(live_pool_legacy.get("as_of_date")),
    }
    if not latest_ranking.empty and "as_of_date" in latest_ranking.columns:
        as_of_values.add(str(latest_ranking["as_of_date"].iloc[0]))
    as_of_values.discard("None")
    if len(as_of_values) != 1:
        raise ValueError(f"Release artifacts have inconsistent as_of_date values: {sorted(as_of_values)}")
    as_of_date = next(iter(as_of_values))

    if not isinstance(live_pool.get("symbols"), list) or not live_pool["symbols"]:
        raise ValueError("live_pool.json must contain a non-empty symbols list.")
    if not isinstance(live_pool_legacy.get("symbols"), dict) or not live_pool_legacy["symbols"]:
        raise ValueError("live_pool_legacy.json must contain a non-empty symbols mapping.")

    version = build_release_version(as_of_date, mode)
    return ReleaseArtifacts(
        as_of_date=as_of_date,
        version=version,
        output_dir=output_path,
        latest_universe_path=paths["latest_universe.json"],
        latest_ranking_path=paths["latest_ranking.csv"],
        live_pool_path=paths["live_pool.json"],
        live_pool_legacy_path=paths["live_pool_legacy.json"],
        artifact_manifest_path=paths["artifact_manifest.json"],
        latest_universe=latest_universe,
        latest_ranking=latest_ranking,
        live_pool=live_pool,
        live_pool_legacy=live_pool_legacy,
        artifact_manifest=artifact_manifest,
    )


def ensure_publish_preflight(
    settings: PublishSettings,
    output_dir: Path | str,
    *,
    expected_pool_size: int | None = None,
    reference_date: Any = None,
    max_age_days: int | None = None,
    require_freshness: bool = True,
) -> dict[str, Any]:
    validation = assert_release_outputs(
        output_dir,
        expected_mode=settings.mode,
        expected_source_project=settings.source_project,
        expected_pool_size=expected_pool_size,
        reference_date=reference_date,
        max_age_days=max_age_days,
        require_manifest=False,
        require_artifact_manifest=True,
        require_freshness=require_freshness,
    )
    if settings.dry_run:
        return validation
    if not settings.project_id:
        raise ValueError("Publish preflight failed: CLOUD_PROJECT_ID is required for a real publish.")
    if not settings.cloud_bucket:
        raise ValueError("Publish preflight failed: CLOUD_BUCKET is required for a real publish.")
    if not str(settings.firestore_collection).strip():
        raise ValueError("Publish preflight failed: Firestore collection must be configured.")
    if not str(settings.firestore_document).strip():
        raise ValueError("Publish preflight failed: Firestore document must be configured.")
    return validation


def build_storage_layout(settings: PublishSettings, artifacts: ReleaseArtifacts) -> dict[str, Any]:
    if not settings.cloud_bucket:
        bucket = "<unset-bucket>"
    else:
        bucket = settings.cloud_bucket

    release_prefix = f"{settings.cloud_root_prefix}/releases/{artifacts.version}"
    current_prefix = f"{settings.cloud_root_prefix}/current"
    filenames = {
        "latest_universe.json": artifacts.latest_universe_path,
        "latest_ranking.csv": artifacts.latest_ranking_path,
        "live_pool.json": artifacts.live_pool_path,
        "live_pool_legacy.json": artifacts.live_pool_legacy_path,
        "artifact_manifest.json": artifacts.artifact_manifest_path,
    }

    objects: dict[str, dict[str, str]] = {}
    for filename in filenames:
        release_object = f"{release_prefix}/{filename}"
        current_object = f"{current_prefix}/{filename}"
        objects[filename] = {
            "release_object": release_object,
            "current_object": current_object,
            "release_uri": f"gs://{bucket}/{release_object}",
            "current_uri": f"gs://{bucket}/{current_object}",
        }

    return {
        "release_prefix": release_prefix,
        "current_prefix": current_prefix,
        "storage_prefix_uri": f"gs://{bucket}/{release_prefix}",
        "current_prefix_uri": f"gs://{bucket}/{current_prefix}",
        "objects": objects,
    }


def build_firestore_payload(
    settings: PublishSettings,
    artifacts: ReleaseArtifacts,
    storage_layout: dict[str, Any],
) -> dict[str, Any]:
    symbol_map = dict(artifacts.live_pool_legacy["symbols"])
    symbols = list(symbol_map.keys())
    generated_at = pd.Timestamp.now(tz="UTC").isoformat()
    return {
        "as_of_date": artifacts.as_of_date,
        "mode": settings.mode,
        "version": artifacts.version,
        "pool_size": int(artifacts.live_pool.get("pool_size", len(symbols))),
        "symbols": symbols,
        "symbol_map": symbol_map,
        "storage_prefix": storage_layout["storage_prefix_uri"],
        "current_prefix": storage_layout["current_prefix_uri"],
        "live_pool_legacy_uri": storage_layout["objects"]["live_pool_legacy.json"]["current_uri"],
        "live_pool_uri": storage_layout["objects"]["live_pool.json"]["current_uri"],
        "latest_universe_uri": storage_layout["objects"]["latest_universe.json"]["current_uri"],
        "latest_ranking_uri": storage_layout["objects"]["latest_ranking.csv"]["current_uri"],
        "versioned_live_pool_legacy_uri": storage_layout["objects"]["live_pool_legacy.json"]["release_uri"],
        "artifact_manifest_uri": storage_layout["objects"]["artifact_manifest.json"]["current_uri"],
        "versioned_artifact_manifest_uri": storage_layout["objects"]["artifact_manifest.json"]["release_uri"],
        "artifact_contract_version": str(artifacts.artifact_manifest.get("contract_version", "")),
        "generated_at": generated_at,
        "source_project": settings.source_project,
    }


def build_release_manifest(
    settings: PublishSettings,
    artifacts: ReleaseArtifacts,
    storage_layout: dict[str, Any],
    firestore_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": artifacts.version,
        "mode": settings.mode,
        "dry_run": settings.dry_run,
        "publish_enabled": settings.enabled,
        "as_of_date": artifacts.as_of_date,
        "release_prefix": storage_layout["release_prefix"],
        "current_prefix": storage_layout["current_prefix"],
        "artifacts": {
            "latest_universe": storage_layout["objects"]["latest_universe.json"],
            "latest_ranking": storage_layout["objects"]["latest_ranking.csv"],
            "live_pool": storage_layout["objects"]["live_pool.json"],
            "live_pool_legacy": storage_layout["objects"]["live_pool_legacy.json"],
            "artifact_manifest": storage_layout["objects"]["artifact_manifest.json"],
        },
        "firestore": {
            "collection": settings.firestore_collection,
            "document": settings.firestore_document,
            "payload": firestore_payload,
        },
    }


def write_release_manifest(output_dir: Path | str, manifest: dict[str, Any]) -> Path:
    manifest_path = Path(output_dir) / "release_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def upload_release_artifacts(
    settings: PublishSettings,
    artifacts: ReleaseArtifacts,
    storage_layout: dict[str, Any],
) -> None:
    if settings.dry_run:
        return
    if not settings.project_id or not settings.cloud_bucket:
        raise ValueError("CLOUD_PROJECT_ID and CLOUD_BUCKET are required for a real publish.")

    try:
        from quant_platform_kit.cloud import get_object_store
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise ModuleNotFoundError(
            "quant-platform-kit is required for real publishing. Install requirements.txt first."
        ) from exc

    store = get_object_store()
    files = {
        "latest_universe.json": artifacts.latest_universe_path,
        "latest_ranking.csv": artifacts.latest_ranking_path,
        "live_pool.json": artifacts.live_pool_path,
        "live_pool_legacy.json": artifacts.live_pool_legacy_path,
        "artifact_manifest.json": artifacts.artifact_manifest_path,
    }
    for filename, local_path in files.items():
        object_info = storage_layout["objects"][filename]
        store.write_bytes(object_info["release_uri"], local_path.read_bytes())
        if settings.upload_current_pointer:
            store.write_bytes(object_info["current_uri"], local_path.read_bytes())


def publish_firestore_summary(settings: PublishSettings, firestore_payload: dict[str, Any]) -> None:
    if settings.dry_run:
        return
    if not settings.project_id:
        raise ValueError("CLOUD_PROJECT_ID is required for Firestore publishing.")

    try:
        from quant_platform_kit.cloud import get_document_store
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise ModuleNotFoundError(
            "quant-platform-kit is required for real publishing. Install requirements.txt first."
        ) from exc

    get_document_store().set(settings.firestore_collection, settings.firestore_document, firestore_payload)


def run_release_publish(
    config: dict[str, Any],
    *,
    mode: str | None = None,
    dry_run: bool = False,
    project_id: str | None = None,
    cloud_bucket: str | None = None,
    firestore_collection: str | None = None,
    firestore_document: str | None = None,
    max_age_days: int | None = 45,
    require_freshness: bool = True,
) -> dict[str, Any]:
    settings = resolve_publish_settings(
        config,
        mode=mode,
        dry_run=dry_run,
        project_id=project_id,
        cloud_bucket=cloud_bucket,
        firestore_collection=firestore_collection,
        firestore_document=firestore_document,
    )
    validation = ensure_publish_preflight(
        settings,
        config["paths"].output_dir,
        expected_pool_size=int(config["export"]["live_pool_size"]),
        max_age_days=max_age_days,
        require_freshness=require_freshness,
    )
    artifacts = load_release_artifacts(config["paths"].output_dir, settings.mode)
    storage_layout = build_storage_layout(settings, artifacts)
    firestore_payload = build_firestore_payload(settings, artifacts, storage_layout)
    manifest = build_release_manifest(settings, artifacts, storage_layout, firestore_payload)
    manifest_path = write_release_manifest(artifacts.output_dir, manifest)
    validation = assert_release_outputs(
        artifacts.output_dir,
        expected_mode=settings.mode,
        expected_source_project=settings.source_project,
        expected_pool_size=int(config["export"]["live_pool_size"]),
        max_age_days=max_age_days,
        require_manifest=True,
        require_artifact_manifest=True,
        require_freshness=require_freshness,
    )

    upload_release_artifacts(settings, artifacts, storage_layout)
    publish_firestore_summary(settings, firestore_payload)

    return {
        "settings": settings,
        "artifacts": artifacts,
        "storage_layout": storage_layout,
        "firestore_payload": firestore_payload,
        "manifest_path": manifest_path,
        "validation": validation,
    }
