from pathlib import Path


def test_publish_lifecycle_inputs_workflow_uses_real_research_pipeline() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-lifecycle-inputs.yml"
    ).read_text(encoding="utf-8")

    assert 'DOWNLOAD_TOP_LIQUID: "90"' in workflow
    assert 'scripts/download_history.py --top-liquid "${DOWNLOAD_TOP_LIQUID}"' in workflow
    assert "scripts/export_lifecycle_preflight_inputs.py" in workflow
    assert "--universe-mode broad_liquid" in workflow
    assert "crypto-lifecycle-inputs-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "if-no-files-found: error" in workflow
