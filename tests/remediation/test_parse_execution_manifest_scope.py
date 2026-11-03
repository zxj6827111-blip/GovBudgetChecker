"""分析执行清单范围：规则辅助模块变化必须进入执行指纹，不改变解析身份。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.schemas.parse_artifact import ParseArtifact
from src.services import analysis_fact_snapshot as snapshots
from src.services import input_processing_identity as identity
from src.services.input_processing_identity import (
    artifact_content_hash,
    processing_config_fingerprint,
    refresh_parse_identity,
    semantic_artifact_hash,
)


def _artifact(**overrides) -> ParseArtifact:
    values = {
        "source_file_hash": "a" * 64,
        "parser_name": "pdfplumber",
        "parser_fingerprint": "pdfplumber-cells-v31-parse-identity-" + "1" * 16,
        "config_fingerprint": "caller-config:processing-v2:" + "2" * 64,
        "ocr_model_fingerprint": "ocr-model-1",
        "schema_version": identity.SCHEMA_VERSION,
    }
    values.update(overrides)
    return ParseArtifact(**values)


def _execution_fingerprint(artifact, execution_manifest: dict) -> str:
    return snapshots.compute_execution_fingerprint(
        execution_manifest=execution_manifest,
        source_file_hash=artifact.source_file_hash,
        parse_run_uid=artifact.parse_run_uid,
        artifact_content_hash_value=artifact_content_hash(artifact),
        semantic_artifact_hash_value=semantic_artifact_hash(artifact),
    )


def test_execution_manifest_covers_rule_helpers_and_rule_configs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    required = {
        "api/main.py": "",
        "api/runtime.py": "",
        "src/db/migrations.py": "",
        "src/engine/rules_v33.py": "from src.utils.narration import extract_amounts\n",
        "src/utils/narration.py": (
            "def extract_amounts(text):\n"
            "    value = 1.0\n"
            "    if '亿元' in text:\n"
            "        value *= 10000\n"
            "    return value\n"
        ),
        "rules/checklists/amount.yaml": "version: 1\n",
    }
    for relative, text in required.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    identity.runtime_code_manifest.cache_clear()
    identity.parse_code_manifest.cache_clear()

    first = identity.processing_manifest()
    parse_manifest = identity.parse_implementation_manifest()
    artifact = _artifact(
        parser_fingerprint="pdfplumber-cells-v31-parse-identity-" + parse_manifest["sha256"][:16],
        config_fingerprint=processing_config_fingerprint("caller", parse_manifest),
    )
    first_uid = refresh_parse_identity(artifact)
    first_execution = snapshots.freeze_execution_manifest()
    first_fingerprint = _execution_fingerprint(artifact, first_execution)
    assert "src/utils/narration.py" in first["code"]
    assert "rules/checklists/amount.yaml" in first["configuration_files"]

    (tmp_path / "src/utils/narration.py").write_text(
        (tmp_path / "src/utils/narration.py").read_text().replace("10000", "1000"))
    (tmp_path / "rules/checklists/amount.yaml").write_text("version: 2\n")
    identity.runtime_code_manifest.cache_clear()
    second = identity.processing_manifest()
    second_execution = snapshots.freeze_execution_manifest()
    second_fingerprint = _execution_fingerprint(artifact, second_execution)

    assert second["sha256"] != first["sha256"]
    assert second_fingerprint != first_fingerprint
    # 只改执行侧辅助模块/规则配置：解析实现、语义产物与 parse_run_uid 不变。
    assert identity.parse_implementation_manifest()["sha256"] == parse_manifest["sha256"]
    assert refresh_parse_identity(artifact) == first_uid


def test_snapshot_keeps_frozen_execution_manifest_for_historical_read(
    monkeypatch: pytest.MonkeyPatch,
):
    artifact = _artifact()
    refresh_parse_identity(artifact)
    execution_manifest = {
        "schema_version": snapshots.EXECUTION_MANIFEST_VERSION,
        "processing_manifest": {"sha256": "run-A"},
    }
    snapshot = snapshots.make_snapshot(
        job_uuid="job", revision_id="rev", artifact=artifact,
        raw_context=None, effective_context=None,
        execution_manifest=deepcopy(execution_manifest))
    with monkeypatch.context() as patch_ctx:
        patch_ctx.setattr(snapshots, "processing_manifest", lambda: (_ for _ in ()).throw(
            AssertionError("historical read consulted current implementation")))
        provenance = snapshots.snapshot_execution_provenance(snapshot)
        snapshots.validate_snapshot(snapshot)
    assert provenance["execution_manifest"] == execution_manifest
    assert provenance["execution_fingerprint"] == snapshot["execution_fingerprint"]
