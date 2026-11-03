"""parse_run_uid 身份基础：三层身份与验收反例回归。

三层职责：
- 输入/缓存查找键：原件 + 解析实现/配置/依赖；
- 语义产物身份：原文、单元格、几何/映射、主体身份与资格证据；
- 分析运行指纹：本次实际执行清单 + 产物来源，随分析快照冻结。
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.schemas.parse_artifact import FiscalFact, ParseArtifact
from src.services import analysis_fact_snapshot as snapshots
from src.services import input_processing_identity as identity
from src.services.input_processing_identity import (
    artifact_content_hash,
    artifacts_share_semantic_identity,
    processing_config_fingerprint,
    refresh_parse_identity,
    semantic_artifact_hash,
)

#: 解析模块允许的非解析导入；每项都在生产模块里显式说明边界。
_NON_PARSE_IMPORTS = identity.NON_PARSE_LOCAL_IMPORTS


@pytest.fixture(autouse=True)
def _clear_identity_caches():
    identity.runtime_code_manifest.cache_clear()
    identity.parse_code_manifest.cache_clear()
    yield
    identity.runtime_code_manifest.cache_clear()
    identity.parse_code_manifest.cache_clear()


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


def _parser_fingerprint(manifest: dict) -> str:
    return "pdfplumber-cells-v31-parse-identity-" + manifest["sha256"][:16]


def _reviewed_native_artifact() -> ParseArtifact:
    """验收方反例使用的原生表产物，补齐真实解析身份字段。"""
    from tests.remediation.test_table_reading_recovery import native_artifact

    artifact = native_artifact()
    manifest = identity.parse_implementation_manifest()
    artifact.parser_name = "pdfplumber"
    artifact.parser_fingerprint = _parser_fingerprint(manifest)
    artifact.config_fingerprint = processing_config_fingerprint("synthetic-config", manifest)
    artifact.processing_manifest = manifest
    refresh_parse_identity(artifact)
    return artifact


def _change_cell(artifact: ParseArtifact, value: str = "999.00") -> None:
    cell = next(c for c in artifact.table_fragments[0].cells if c.row_idx == 1 and c.col_idx == 2)
    cell.raw_text = cell.normalized_text = value
    cell.numeric_value = float(value)
    cell.normalized_value_wan = Decimal(value)
    artifact.table_fragments[0].rows[1][2] = value


def _prepare_identity_root(tmp_path: Path) -> tuple[Path, Path]:
    parse_file = tmp_path / "src/services/pdf_parser.py"
    diagnostic_file = tmp_path / "src/engine/reporting.py"
    files = (
        (parse_file, "PARSE_V1"),
        (diagnostic_file, "REPORT_V1"),
        (tmp_path / "src/schemas/check_receipt.py", "RECEIPT_V1"),
        (tmp_path / "api/main.py", ""),
        (tmp_path / "api/runtime.py", ""),
        (tmp_path / "src/db/migrations.py", ""),
    )
    for path, text in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return parse_file, diagnostic_file


def test_same_input_and_conditions_repeat_to_same_parse_identity():
    first, second = _artifact(), _artifact()
    first_uid = refresh_parse_identity(first)
    second_uid = refresh_parse_identity(second)
    assert first_uid == second_uid
    # 重算是幂等的：已签发身份不会因为再次进入同一条链而漂移。
    assert refresh_parse_identity(first) == first_uid


def test_audit_and_display_fields_do_not_move_parse_identity():
    first = _artifact()
    refresh_parse_identity(first)
    second = _artifact()
    second.label_visibility = {"recovered_total": 3, "conflict_total": 1}
    second.reconstruction_decisions = [{"reason": "display only"}]
    assert refresh_parse_identity(second) == first.parse_run_uid
    assert artifacts_share_semantic_identity(first, second)
    # artifact 完整内容哈希仍覆盖审计字段，二者职责不同。
    assert artifact_content_hash(second) != artifact_content_hash(first)


def test_cell_content_change_moves_identity_and_rejects_old_context():
    from src.services.analysis_fact_snapshot import context_is_source_bound

    old = _reviewed_native_artifact()
    old_uid = old.parse_run_uid
    changed = deepcopy(old)
    _change_cell(changed)
    changed_uid = refresh_parse_identity(changed)

    assert changed_uid != old_uid
    assert artifact_content_hash(changed) != artifact_content_hash(old)
    assert semantic_artifact_hash(changed) != semantic_artifact_hash(old)
    assert context_is_source_bound(changed, {
        "file_hash": old.source_file_hash,
        "parse_run_uid": old_uid,
        "facts": [{"raw_value": "100.00"}],
    }) is False


def test_reading_assessment_gap_moves_identity_and_qualification():
    from src.services.table_reading_assessment import assess_table_reading, constrain_reading_facts

    old = _reviewed_native_artifact()
    changed = deepcopy(old)
    changed.reading_assessment["pages"][0]["amounts"].append(
        {"raw_text": "999.00", "bbox": [210, 150, 260, 170]}
    )
    changed_uid = refresh_parse_identity(changed)

    assert changed_uid != old.parse_run_uid
    assert assess_table_reading(changed)["status"] == "partial"
    assert "native_amount_not_extracted" in {
        gap["reason"] for gap in assess_table_reading(changed)["gaps"]
    }
    fact = FiscalFact(
        fact_uid="synthetic-fact", indicator="total_actual", raw_value="100.00", unit="万元",
        normalized_value=Decimal("100.00"), instance_uid=old.logical_tables[0].instance_uid,
        table_code=old.logical_tables[0].table_code, recognition_status="unverified")
    assert constrain_reading_facts(old, [fact])[0].recognition_status == "unverified"
    assert constrain_reading_facts(changed, [fact])[0].recognition_status == "low_confidence"


def test_diagnostic_code_change_keeps_parse_identity_and_changes_execution_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _, diagnostic_file = _prepare_identity_root(tmp_path)
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ())

    before_parse = identity.parse_implementation_manifest()
    before_execution = snapshots.freeze_execution_manifest()
    first = _artifact(
        parser_fingerprint=_parser_fingerprint(before_parse),
        config_fingerprint=processing_config_fingerprint("caller", before_parse),
    )
    first_uid = refresh_parse_identity(first)
    before_fingerprint = snapshots.compute_execution_fingerprint(
        execution_manifest=before_execution,
        source_file_hash=first.source_file_hash,
        parse_run_uid=first_uid,
        artifact_content_hash_value=artifact_content_hash(first),
        semantic_artifact_hash_value=semantic_artifact_hash(first),
    )

    diagnostic_file.write_text("REPORT_V2")
    identity.runtime_code_manifest.cache_clear()

    after_parse = identity.parse_implementation_manifest()
    after_execution = snapshots.freeze_execution_manifest()
    second = _artifact(
        parser_fingerprint=_parser_fingerprint(after_parse),
        config_fingerprint=processing_config_fingerprint("caller", after_parse),
    )
    assert after_parse["sha256"] == before_parse["sha256"]
    assert refresh_parse_identity(second) == first_uid
    assert after_execution != before_execution
    after_fingerprint = snapshots.compute_execution_fingerprint(
        execution_manifest=after_execution,
        source_file_hash=second.source_file_hash,
        parse_run_uid=second.parse_run_uid,
        artifact_content_hash_value=artifact_content_hash(second),
        semantic_artifact_hash_value=semantic_artifact_hash(second),
    )
    assert after_fingerprint != before_fingerprint


def test_parse_implementation_change_moves_parse_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    parse_file, _ = _prepare_identity_root(tmp_path)
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ())

    before = identity.parse_implementation_manifest()
    first = _artifact(
        parser_fingerprint=_parser_fingerprint(before),
        config_fingerprint=processing_config_fingerprint("caller", before),
    )
    first_uid = refresh_parse_identity(first)

    parse_file.write_text("PARSE_V2")
    identity.parse_code_manifest.cache_clear()
    after = identity.parse_implementation_manifest()
    second = _artifact(
        parser_fingerprint=_parser_fingerprint(after),
        config_fingerprint=processing_config_fingerprint("caller", after),
    )
    assert after["sha256"] != before["sha256"]
    assert refresh_parse_identity(second) != first_uid
    assert processing_config_fingerprint("caller", after) != processing_config_fingerprint("caller", before)


def test_parse_dependency_inventory_covers_layout_and_matching():
    assert {"rapidfuzz", "pdfminer.six"} <= set(identity.PARSE_DEPENDENCIES)


@pytest.mark.parametrize("dependency", ["rapidfuzz", "pdfminer.six"])
def test_dependency_version_change_moves_parse_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dependency: str
):
    _prepare_identity_root(tmp_path)
    versions = {dependency: "1.0.0"}
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", (dependency,))
    monkeypatch.setattr(identity, "_package_version", lambda name: versions.get(name))

    before = identity.parse_implementation_manifest()
    versions[dependency] = "2.0.0"
    identity.parse_code_manifest.cache_clear()
    after = identity.parse_implementation_manifest()
    assert after["sha256"] != before["sha256"]


def test_original_file_parser_and_ocr_model_are_identity_inputs():
    variants = [
        _artifact(),
        _artifact(source_file_hash="b" * 64),
        _artifact(parser_fingerprint="pdfplumber-cells-v31-parse-identity-" + "3" * 16),
        _artifact(ocr_model_fingerprint="ocr-model-2"),
        _artifact(schema_version="gbc.parse-artifact.v4"),
        _artifact(parser_name="other-parser"),
    ]
    identities = {refresh_parse_identity(item) for item in variants}
    assert len(identities) == len(variants)


def test_column_mapping_evidence_moves_parse_identity():
    from src.engine.fact_context import apply_table_column_mapping_overlays
    from tests.remediation.test_rc_batch1 import _artifact_with, _instance_for, _make_fragment

    fragment = _make_fragment(
        "FIN_01_income_expenditure_total",
        [["项目", "本年收入", "本年支出"], ["合计", "100.00", "90.00"]],
    )
    artifact = _artifact_with([fragment], [_instance_for(fragment)], file_hash="a" * 64)
    artifact.parser_fingerprint = "pdfplumber-cells-v31-parse-identity-" + "1" * 16
    artifact.schema_version = identity.SCHEMA_VERSION
    before = refresh_parse_identity(artifact)
    records = [{
        "record_id": "human-mapping",
        "changes": [{
            "target_type": "table_instance",
            "target_id": artifact.logical_tables[0].instance_uid,
            "field": "column_mapping",
            "value": {"canonical_measure": "income_actual", "source_col_idx": 2},
        }],
    }]
    assert apply_table_column_mapping_overlays(artifact, records)["applied"] == 1
    mapped = artifact.parse_run_uid
    assert mapped != before
    # 同样的映射重复应用不改变身份；这是缓存可复用的前提。
    apply_table_column_mapping_overlays(artifact, records)
    assert artifact.parse_run_uid == mapped


def test_manual_cell_evidence_moves_parse_identity():
    from src.services.manual_cell_overlay import apply_table_cell_overlays
    from tests.remediation.test_manual_cell_fill import artifact as manual_artifact
    from tests.remediation.test_manual_cell_fill import fill

    built = manual_artifact([["221", "住房保障支出", "", ""]])
    before = refresh_parse_identity(built)
    report = apply_table_cell_overlays(built, [fill(1, 2, "200")])
    assert report["applied"] == 1
    assert built.parse_run_uid != before


def test_raw_interpretation_evidence_moves_parse_identity():
    from src.services.analysis_fact_snapshot import bind_raw_interpretation

    class _RawContext:
        def __init__(self, artifact):
            self._data = {
                "file_hash": artifact.source_file_hash,
                "parse_run_uid": "raw-run",
                "facts": [],
            }

        def to_dict(self):
            return dict(self._data)

    artifact = _artifact()
    before = refresh_parse_identity(artifact)
    bind_raw_interpretation(artifact, _RawContext(artifact), {"applied": 1, "targets": []})
    assert artifact.parse_run_uid != before
    assert artifact.processing_manifest["raw_interpretation"]["parse_run_uid"] == "raw-run"


def test_legacy_identity_schemes_are_not_reissued():
    artifact = _artifact()
    refresh_parse_identity(artifact)
    old_content_hash_uid = artifact_content_hash(artifact)[:32]
    old_four_field_uid = hashlib.sha256("|".join((
        artifact.source_file_hash,
        artifact.parser_fingerprint,
        artifact.config_fingerprint,
        artifact.ocr_model_fingerprint or "",
    )).encode()).hexdigest()[:32]
    assert artifact.parse_run_uid != old_content_hash_uid
    assert artifact.parse_run_uid != old_four_field_uid


def test_old_immutable_run_cannot_be_overwritten_by_new_identity():
    from src.services.parse_artifact_store import _restore_snapshot, artifact_payload_hash

    old = _artifact()
    old.parse_run_uid = "legacy-content-hash-run"
    payload = old.to_dict()
    run = dict(payload, artifact_snapshot=payload,
               artifact_snapshot_hash=artifact_payload_hash(payload))
    restored = _restore_snapshot(run)
    assert restored.to_dict() == payload
    assert restored.parse_run_uid == "legacy-content-hash-run"

    new = deepcopy(old)
    new.parser_fingerprint = "pdfplumber-cells-v31-parse-identity-" + "9" * 16
    assert refresh_parse_identity(new) != old.parse_run_uid
    # 旧 run 的快照不能被新身份改写：即使重算哈希，身份字段也必须与行记录一致。
    tampered = dict(payload, parse_run_uid=new.parse_run_uid)
    tampered_run = dict(run, artifact_snapshot=tampered,
                        artifact_snapshot_hash=artifact_payload_hash(tampered))
    with pytest.raises(ValueError, match="identity mismatch"):
        _restore_snapshot(tampered_run)


def test_receipt_schema_change_does_not_move_parse_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _prepare_identity_root(tmp_path)
    receipt_file = tmp_path / "src/schemas/check_receipt.py"
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ())

    before_parse = identity.parse_implementation_manifest()
    before_full = identity.processing_manifest()
    receipt_file.write_text("RECEIPT_V2")
    identity.runtime_code_manifest.cache_clear()
    after_parse = identity.parse_implementation_manifest()
    after_full = identity.processing_manifest()
    # 回执/诊断 schema 只进入完整执行清单，不进入解析实现清单。
    assert after_parse["sha256"] == before_parse["sha256"]
    assert after_full["sha256"] != before_full["sha256"]


def test_receipt_schema_remains_importable_from_parse_artifact():
    from src.schemas.check_receipt import CheckReceipt as Receipt
    from src.schemas.parse_artifact import CheckReceipt as CompatReceipt
    from src.schemas.parse_artifact import SUB_CHECK_STATUSES as compat_statuses

    assert CompatReceipt is Receipt
    assert "insufficient_data" in compat_statuses
    assert Receipt(rule_id="C-001", sub_check_id="total", status="pass").to_dict()["status"] == "pass"


def test_parse_manifest_covers_parse_module_import_edges():
    manifest = set(identity.PARSE_IMPLEMENTATION_FILES)
    outside = set()
    for relative in manifest:
        outside |= identity.local_import_dependencies(relative, identity.ROOT)
    unexpected = (outside - manifest) - set(_NON_PARSE_IMPORTS)
    assert not unexpected, sorted(unexpected)


def test_local_import_dependencies_include_target_package_and_parents(tmp_path: Path):
    service = tmp_path / "ocr_service"
    package = service / "unregistered_parser"
    package.mkdir(parents=True)
    (service / "app.py").write_text("from .unregistered_parser import transform\n")
    (service / "__init__.py").write_text("")
    (package / "__init__.py").write_text("def transform(value): return value * 10\n")
    assert identity.local_import_dependencies("ocr_service/app.py", tmp_path) == {
        "ocr_service/__init__.py",
        "ocr_service/unregistered_parser/__init__.py",
    }

    parser = tmp_path / "src/services/pdf_parser.py"
    parser.parent.mkdir(parents=True)
    parser.write_text("")
    (tmp_path / "src/__init__.py").write_text("")
    (tmp_path / "src/services/__init__.py").write_text("")
    dependencies = identity.local_import_dependencies("src/services/pdf_parser.py", tmp_path)
    assert {"src/__init__.py", "src/services/__init__.py"} <= dependencies


def test_local_import_prefers_module_file_over_same_name_package(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/__init__.py").write_text("")
    (tmp_path / "src/foo.py").write_text("VALUE = 'module'\n")
    package = tmp_path / "src/foo"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 'package'\n")
    (tmp_path / "src/consumer.py").write_text("from src.foo import VALUE\n")
    dependencies = identity.local_import_dependencies("src/consumer.py", tmp_path)
    assert "src/foo.py" in dependencies
    assert "src/foo/__init__.py" not in dependencies


@pytest.mark.parametrize("source", [
    "from ocr_service.unregistered_parser import transform\n",
    "from ocr_service import unregistered_parser\n",
    "from .unregistered_parser import transform\n",
    "from . import unregistered_parser\n",
    "import ocr_service.unregistered_parser\n",
    "def later():\n    from .unregistered_parser import transform\n",
])
def test_import_guard_rejects_unregistered_package_parser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
):
    service = tmp_path / "ocr_service"
    package = service / "unregistered_parser"
    package.mkdir(parents=True)
    (service / "app.py").write_text(source)
    (package / "__init__.py").write_text("def transform(value): return value * 10\n")
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("ocr_service/app.py",))
    with pytest.raises(AssertionError):
        test_parse_manifest_covers_parse_module_import_edges()


def test_parent_package_init_change_moves_parse_implementation_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    for relative in ("src/__init__.py", "src/services/__init__.py", "src/services/pdf_parser.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", (
        "src/__init__.py", "src/services/__init__.py", "src/services/pdf_parser.py"))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ())
    identity.parse_code_manifest.cache_clear()
    before = identity.parse_implementation_manifest()
    (tmp_path / "src/services/__init__.py").write_text("changed parent package")
    identity.parse_code_manifest.cache_clear()
    after = identity.parse_implementation_manifest()
    assert after["sha256"] != before["sha256"]
    assert after["code"]["src/services/__init__.py"] != before["code"]["src/services/__init__.py"]


def test_execution_manifest_covers_rule_helper_modules(
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
        parser_fingerprint=_parser_fingerprint(parse_manifest),
        config_fingerprint=processing_config_fingerprint("caller", parse_manifest),
    )
    first_uid = refresh_parse_identity(artifact)
    first_execution = snapshots.freeze_execution_manifest()
    first_fingerprint = snapshots.compute_execution_fingerprint(
        execution_manifest=first_execution,
        source_file_hash=artifact.source_file_hash,
        parse_run_uid=first_uid,
        artifact_content_hash_value=artifact_content_hash(artifact),
        semantic_artifact_hash_value=semantic_artifact_hash(artifact),
    )
    assert "src/utils/narration.py" in first["code"]
    assert "rules/checklists/amount.yaml" in first["configuration_files"]

    (tmp_path / "src/utils/narration.py").write_text(
        (tmp_path / "src/utils/narration.py").read_text().replace("10000", "1000"))
    (tmp_path / "rules/checklists/amount.yaml").write_text("version: 2\n")
    identity.runtime_code_manifest.cache_clear()
    second = identity.processing_manifest()
    second_execution = snapshots.freeze_execution_manifest()
    second_fingerprint = snapshots.compute_execution_fingerprint(
        execution_manifest=second_execution,
        source_file_hash=artifact.source_file_hash,
        parse_run_uid=first_uid,
        artifact_content_hash_value=artifact_content_hash(artifact),
        semantic_artifact_hash_value=semantic_artifact_hash(artifact),
    )
    assert second["sha256"] != first["sha256"]
    assert second_fingerprint != first_fingerprint
    # 只改执行侧辅助模块：解析实现、语义产物与 parse_run_uid 均不变。
    assert identity.parse_implementation_manifest()["sha256"] == parse_manifest["sha256"]
    assert refresh_parse_identity(artifact) == first_uid


@pytest.mark.parametrize("source", [
    "from ocr_service.unregistered_parser import transform\n",
    "from ocr_service import unregistered_parser\n",
    "from .unregistered_parser import transform\n",
    "from . import unregistered_parser\n",
    "import ocr_service.unregistered_parser\n",
    "def later():\n    from ocr_service.unregistered_parser import transform\n",
])
def test_import_guard_rejects_unregistered_local_parser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
):
    service = tmp_path / "ocr_service"
    service.mkdir()
    (service / "app.py").write_text(source)
    (service / "unregistered_parser.py").write_text("def transform(value): return value\n")
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("ocr_service/app.py",))
    with pytest.raises(AssertionError):
        test_parse_manifest_covers_parse_module_import_edges()


def test_execution_fingerprint_is_frozen_in_snapshot(monkeypatch: pytest.MonkeyPatch):
    artifact = _artifact()
    refresh_parse_identity(artifact)
    execution_manifest = {
        "schema_version": snapshots.EXECUTION_MANIFEST_VERSION,
        "processing_manifest": {"sha256": "run-A"},
    }
    snapshot = snapshots.make_snapshot(
        job_uuid="job", revision_id="rev", artifact=artifact,
        raw_context=None, effective_context=None, execution_manifest=execution_manifest)
    assert snapshot["schema_version"] == snapshots.SNAPSHOT_VERSION
    assert snapshot["execution_manifest"] == execution_manifest
    assert snapshot["execution_fingerprint"] == snapshots.compute_execution_fingerprint(
        execution_manifest=execution_manifest,
        source_file_hash=artifact.source_file_hash,
        parse_run_uid=artifact.parse_run_uid,
        artifact_content_hash_value=artifact_content_hash(artifact),
        semantic_artifact_hash_value=semantic_artifact_hash(artifact),
    )
    # 历史读取只使用快照内保存的清单，不能回头调用当前代码清单。
    with monkeypatch.context() as patch_ctx:
        patch_ctx.setattr(snapshots, "processing_manifest", lambda: (_ for _ in ()).throw(
            AssertionError("historical read consulted current implementation")))
        assert snapshots.snapshot_execution_provenance(snapshot)["execution_fingerprint"] == \
            snapshot["execution_fingerprint"]
        snapshots.validate_snapshot(snapshot)

    tampered = deepcopy(snapshot)
    tampered["execution_manifest"]["processing_manifest"]["sha256"] = "run-B"
    tampered["snapshot_hash"] = snapshots.content_hash({
        key: value for key, value in tampered.items() if key != "snapshot_hash"})
    with pytest.raises(ValueError, match="execution fingerprint"):
        snapshots.validate_snapshot(tampered)


def test_legacy_snapshot_without_execution_provenance_still_reads():
    artifact = _artifact()
    refresh_parse_identity(artifact)
    snapshot = snapshots.make_snapshot(
        job_uuid="job", revision_id="rev", artifact=artifact,
        raw_context=None, effective_context=None,
        execution_manifest={"schema_version": snapshots.EXECUTION_MANIFEST_VERSION,
                            "processing_manifest": {"sha256": "run-A"}})
    legacy = {
        key: value for key, value in snapshot.items()
        if key not in {"execution_manifest", "execution_fingerprint", "semantic_artifact_hash"}
    }
    legacy["schema_version"] = "gbc.analysis-fact-snapshot.v1"
    legacy["snapshot_hash"] = snapshots.content_hash({
        key: value for key, value in legacy.items() if key != "snapshot_hash"})
    assert snapshots.validate_snapshot(legacy) is legacy
    assert snapshots.snapshot_execution_provenance(legacy) is None


class _Transaction:
    async def start(self): pass
    async def commit(self): pass
    async def rollback(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_exc): return False


class _Connection:
    def transaction(self): return _Transaction()
    async def fetch(self, *_args, **_kwargs): return []
    async def execute(self, *_args, **_kwargs): return None
    async def fetchrow(self, *_args, **_kwargs): return None


@pytest.mark.asyncio
async def test_persist_accepts_audit_only_snapshot_but_rejects_semantic_change():
    from src.services.parse_artifact_store import (
        artifact_payload_hash,
        persist_parse_artifact,
    )

    cached = _reviewed_native_artifact()
    supplied = deepcopy(cached)
    supplied.processing_manifest = {**supplied.processing_manifest, "audit_marker": "run-B"}
    assert refresh_parse_identity(supplied) == cached.parse_run_uid
    cached_payload = cached.to_dict()
    run = {
        "id": 7,
        "artifact_snapshot_hash": artifact_payload_hash(cached_payload),
        "artifact_snapshot": cached_payload,
    }

    class _PersistConnection(_Connection):
        async def fetchrow(self, *_args, **_kwargs):
            return run

    assert await persist_parse_artifact(
        _PersistConnection(), supplied, document_version_id=1) == 7

    corrupted = deepcopy(cached)
    _change_cell(corrupted)
    corrupted.parse_run_uid = cached.parse_run_uid  # 模拟同一 UID 被错误复用
    with pytest.raises(ValueError, match="different semantic artifact"):
        await persist_parse_artifact(
            _PersistConnection(), corrupted, document_version_id=1)


@pytest.mark.asyncio
async def test_repeated_ingest_ignores_audit_manifest_but_rejects_semantic_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from src.services import structured_ingest_runner as runner

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    source = tmp_path / "synthetic_cache_input.pdf"
    source.write_bytes(b"%PDF-1.4\n% synthetic identity boundary input\n%%EOF")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    cached = _reviewed_native_artifact()
    cached.source_file_hash = checksum
    refresh_parse_identity(cached)
    supplied = deepcopy(cached)
    # 只改审计/诊断清单，不改语义内容：不得产生缓存冲突。
    supplied.processing_manifest = {
        **supplied.processing_manifest, "audit_marker": "run-B"}
    assert refresh_parse_identity(supplied) == cached.parse_run_uid
    assert artifacts_share_semantic_identity(cached, supplied)

    async def persist_facts(_conn, *, facts, **_kwargs):
        return len(facts)

    mocks = {
        "ensure_structured_ingest_ready": AsyncMock(return_value=True),
        "_ensure_document_version": AsyncMock(return_value={
            "document_id": 1, "document_version_id": 1}),
        "find_cached_parse_run": AsyncMock(return_value=7),
        "load_parse_artifact": AsyncMock(return_value=cached),
        "persist_parse_artifact": AsyncMock(),
        "persist_facts_and_evidence": AsyncMock(side_effect=persist_facts),
        "verify_persisted_facts": AsyncMock(return_value={
            "verified": True, "facts_count": 1, "facts_hash": "synthetic"}),
        "persist_snapshot": AsyncMock(return_value={"snapshot_hash": "synthetic"}),
    }
    for name, value in mocks.items():
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(runner.DatabaseConnection, "acquire", AsyncMock(return_value=_Connection()))
    monkeypatch.setattr(runner.DatabaseConnection, "release", AsyncMock())
    metadata = {
        "checksum": checksum, "organization_name": "测试局", "fiscal_year": 2024,
        "report_year": 2024, "doc_type": "dept_final", "report_kind": "final",
    }
    result = await runner.run_structured_ingest(
        "identity-review", source, metadata, parse_artifact=supplied,
        analysis_revision="rev-identity", candidate_only=True,
        execution_manifest={"schema_version": snapshots.EXECUTION_MANIFEST_VERSION,
                            "processing_manifest": {"sha256": "run-B"}})
    assert "cache_key_conflict" not in str(result.get("error") or ""), result
    assert result["status"] == "done", result
    assert mocks["persist_parse_artifact"].await_count == 0

    changed = deepcopy(cached)
    _change_cell(changed)
    refresh_parse_identity(changed)
    result = await runner.run_structured_ingest(
        "identity-review", source, metadata, parse_artifact=changed,
        analysis_revision="rev-identity", candidate_only=True)
    assert result["status"] == "error"
    assert "cache_key_conflict" in str(result.get("error") or "")
    assert mocks["persist_parse_artifact"].await_count == 0


def test_two_real_parser_runs_on_the_same_pdf_share_run_identity(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "repeat.pdf"
    document = fitz.open()
    page = document.new_page()
    y = 72
    for line in ("收入支出决算总表", "单位：万元", "项目 合计", "合计 123.45"):
        page.insert_text((72, y), line, fontsize=12, fontname="china-s")
        y += 18
    document.save(str(pdf_path))
    document.close()

    from src.services.pdf_parser import PDFParser

    first = PDFParser(None).build_parse_artifact(str(pdf_path))
    second = PDFParser(None).build_parse_artifact(str(pdf_path))
    assert first.source_file_hash == second.source_file_hash
    assert first.config_fingerprint == second.config_fingerprint
    assert first.parse_run_uid == second.parse_run_uid
    # 解析快照只保存解析来源清单，不写入完整运行代码清单。
    assert first.processing_manifest["sha256"] == identity.parse_implementation_manifest()["sha256"]
    assert set(first.processing_manifest["code"]) <= set(identity.PARSE_IMPLEMENTATION_FILES)
