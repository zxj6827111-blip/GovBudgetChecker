"""parse_run_uid 身份基础：只随解析实现/配置/依赖/原件/映射证据变化。

反例同时冻结两条边界：
- 诊断、展示、规则代码变化只能改变完整运行指纹；
- 旧内容哈希身份和旧四字段身份不能被新实现重新签发。
"""
from __future__ import annotations

import ast
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from src.schemas.parse_artifact import ParseArtifact
from src.services import input_processing_identity as identity
from src.services.input_processing_identity import (
    artifact_content_hash,
    full_run_fingerprint,
    processing_config_fingerprint,
    refresh_parse_identity,
)

#: 解析模块唯一允许的非解析导入：回执/诊断 schema 已拆分到独立模块。
_NON_PARSE_IMPORTS = {"src/schemas/check_receipt.py"}


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
        "parser_fingerprint": "pdfplumber-cells-v30-parse-identity-" + "1" * 16,
        "config_fingerprint": "caller-config:processing-v2:" + "2" * 64,
        "ocr_model_fingerprint": "ocr-model-1",
        "schema_version": identity.SCHEMA_VERSION,
    }
    values.update(overrides)
    return ParseArtifact(**values)


def _parser_fingerprint(manifest: dict) -> str:
    return "pdfplumber-cells-v30-parse-identity-" + manifest["sha256"][:16]


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


def _local_module_path(root: Path, dotted: str) -> str | None:
    relative = Path(*dotted.split("."))
    for candidate in (root / relative.with_suffix(".py"), root / relative / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def test_same_input_and_conditions_repeat_to_same_parse_identity():
    first, second = _artifact(), _artifact()
    first_uid = refresh_parse_identity(first)
    second_uid = refresh_parse_identity(second)
    assert first_uid == second_uid
    # 重算是幂等的：已签发身份不会因为再次进入同一条链而漂移。
    assert refresh_parse_identity(first) == first_uid


def test_diagnostic_and_display_fields_do_not_move_parse_identity():
    first = _artifact()
    refresh_parse_identity(first)
    second = _artifact()
    second.reading_assessment = {"version": "table-reading-v1", "pages": [{"probe": "changed"}]}
    second.label_visibility = {"recovered_total": 3, "conflict_total": 1}
    second.reconstruction_decisions = [{"reason": "display only"}]
    assert refresh_parse_identity(second) == first.parse_run_uid
    assert full_run_fingerprint(second) != full_run_fingerprint(first)


def test_diagnostic_code_change_keeps_parse_identity_but_moves_full_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _, diagnostic_file = _prepare_identity_root(tmp_path)
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ())

    before_parse = identity.parse_implementation_manifest()
    before_full = identity.processing_manifest()
    first = _artifact(
        parser_fingerprint=_parser_fingerprint(before_parse),
        config_fingerprint=processing_config_fingerprint("caller", before_parse),
    )
    first_uid = refresh_parse_identity(first)
    before_full_fingerprint = full_run_fingerprint(first)

    diagnostic_file.write_text("REPORT_V2")
    identity.runtime_code_manifest.cache_clear()

    after_parse = identity.parse_implementation_manifest()
    after_full = identity.processing_manifest()
    second = _artifact(
        parser_fingerprint=_parser_fingerprint(after_parse),
        config_fingerprint=processing_config_fingerprint("caller", after_parse),
    )
    assert after_parse["sha256"] == before_parse["sha256"]
    assert after_full["sha256"] != before_full["sha256"]
    assert refresh_parse_identity(second) == first_uid
    assert full_run_fingerprint(second) != before_full_fingerprint


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


def test_dependency_version_change_moves_parse_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _prepare_identity_root(tmp_path)
    versions = {"pdfplumber": "1.0.0"}
    monkeypatch.setattr(identity, "ROOT", tmp_path)
    monkeypatch.setattr(identity, "PARSE_IMPLEMENTATION_FILES", ("src/services/pdf_parser.py",))
    monkeypatch.setattr(identity, "PARSE_DEPENDENCIES", ("pdfplumber",))
    monkeypatch.setattr(identity, "_package_version", lambda name: versions.get(name))

    before = identity.parse_implementation_manifest()
    versions["pdfplumber"] = "2.0.0"
    identity.parse_code_manifest.cache_clear()
    after = identity.parse_implementation_manifest()
    assert after["sha256"] != before["sha256"]


def test_original_file_parser_and_ocr_model_are_identity_inputs():
    variants = [
        _artifact(),
        _artifact(source_file_hash="b" * 64),
        _artifact(parser_fingerprint="pdfplumber-cells-v30-parse-identity-" + "3" * 16),
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
    artifact.parser_fingerprint = "pdfplumber-cells-v30-parse-identity-" + "1" * 16
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
    new.parser_fingerprint = "pdfplumber-cells-v30-parse-identity-" + "9" * 16
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
    # 回执/诊断 schema 只进入完整清单，不进入解析实现清单。
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
        path = identity.ROOT / relative
        if not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
                names.append(node.module)
                names.extend(node.module + "." + alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names if alias.name.startswith("src"))
            for name in names:
                local = _local_module_path(identity.ROOT, name)
                if local and local not in manifest:
                    outside.add(local)
    assert outside <= _NON_PARSE_IMPORTS, sorted(outside - _NON_PARSE_IMPORTS)


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
