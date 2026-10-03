"""RC 批次一整改回归（复核报告 2026-10-03 §四/§五）。

对应 RC-01（规则迁移与取数）、RC-02（表身份）、RC-08（缓存指纹）、
RC-10（校正闭环）、RC-12（版本与导出防串版）。全部合成输入，修复前
失败，修复后作为正式回归。

每条测试的 docstring 标注 RC 编号与"再次验收条件"原文要点。
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.engine.budget_rules import BUD107_TextTableConsistency
from src.engine.fact_context import (
    FactContext,
    FactOverlay,
    apply_fact_overlay,
    apply_fact_overlays_with_remap,
    build_fact_context_from_artifact,
    collect_unsupported_changes,
    parse_overlays_from_records,
    resolve_operand,
)
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import build_document
from src.schemas.parse_artifact import (
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.pdf_parser import PDFParser, _stable_fragment_uid


# ---------------------------------------------------------------------------
# 工具（与 test_ac_remediation 同构，独立复制避免跨文件耦合）
# ---------------------------------------------------------------------------


def _make_fragment(
    code: str,
    rows: List[List[str]],
    *,
    title: str = "测试表",
    page: int = 1,
    unit_hint: str = "万元",
    confidence: float = 0.95,
    table_index: int = 0,
) -> ParsedTable:
    cells = [
        ParsedCell(
            row_idx=r,
            col_idx=c,
            raw_text=rows[r][c],
            normalized_text=rows[r][c],
            numeric_value=(
                float(rows[r][c].replace(",", ""))
                if rows[r][c].replace(".", "").replace(",", "").replace("-", "").isdigit()
                else None
            ),
            page_number=page,
            bbox=(10.0 * r, 10.0 * c, 10.0 * r + 5, 10.0 * c + 5),
            is_header=(r == 0),
            confidence=confidence,
            unit_hint=unit_hint,
            extraction_method="lines",
        )
        for r in range(len(rows))
        for c in range(len(rows[r]))
    ]
    return ParsedTable(
        table_code=code,
        title=title,
        page_number=page,
        table_index=table_index,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=confidence,
        extraction_method="lines",
        unit_hint=unit_hint,
        rows=rows,
        cells=cells,
        # 与生产解析器同源：内容派生的稳定 UID（RC-10）
        fragment_uid=_stable_fragment_uid(
            "t", page, table_index, code, title, rows=rows
        ),
    )


def _artifact_with(
    fragments: List[ParsedTable],
    instances: List[LogicalTableInstance],
    *,
    file_hash: str = "abc123",
) -> ParseArtifact:
    return ParseArtifact(
        source_file_hash=file_hash,
        page_count=max((f.page_number for f in fragments), default=1),
        table_fragments=fragments,
        logical_tables=instances,
    )


def _instance_for(fragment: ParsedTable) -> LogicalTableInstance:
    return LogicalTableInstance(
        instance_uid=fragment.fragment_uid,
        table_code=fragment.table_code,
        title=fragment.title,
        fragment_uids=[fragment.fragment_uid],
        page_start=fragment.page_number,
        page_end=fragment.page_number,
        column_mappings=[],
    )


def _run_one(doc: Any, rule: Any) -> Dict[str, Any]:
    issues, outcomes = run_rules_with_outcomes(
        doc, report_kind="budget", rules=[rule]
    )
    return {
        "outcomes": [o.to_dict() for o in outcomes],
        "issues": list(issues),
        "receipts": list(getattr(doc, "fact_receipts", []) or []),
    }


def _fact(uid: str, indicator: str, value: Decimal, subject: str = "合计",
          status: str = "unverified", confidence: float = 0.9,
          table_code: str = "FIN_01_income_expenditure_total") -> FiscalFact:
    return FiscalFact(
        fact_uid=uid,
        indicator=indicator,
        raw_value=str(value),
        unit="万元",
        normalized_value=value,
        subject_name=subject,
        table_code=table_code,
        recognition_status=status,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# RC-01：操作数选择——verified 优先、总计角色唯一化、歧义拒绝
# ---------------------------------------------------------------------------


def test_rc01_verified_fact_outranks_confident_unverified() -> None:
    """同指标同时存在 verified=100 与 unverified=999：排序不得反超。

    复核反例：旧排序 (status != "verified", confidence) reverse=True 使
    高置信 unverified 压过 verified。
    """
    context = FactContext(
        facts=[
            _fact("t1", "income_actual", Decimal("100"), status="verified", confidence=0.8),
            _fact("t2", "income_actual", Decimal("999"), subject="事业收入", confidence=1.0),
        ]
    )
    resolution = resolve_operand(context, "income_actual")
    assert resolution.status == "ok"
    assert resolution.value == Decimal("100")
    assert resolution.fact.fact_uid == "t1"

    # 同为总计角色但值不同 → 必须判歧义，不得按置信度静默二选一
    ambiguous = FactContext(
        facts=[
            _fact("a", "income_actual", Decimal("100")),
            _fact("b", "income_actual", Decimal("999")),
        ]
    )
    assert resolve_operand(ambiguous, "income_actual").status == "blocked"


def test_rc01_total_requires_row_role_not_first_fact() -> None:
    """明细 50 在合计 100 之前时，不得把 50 当总计（角色唯一化）。"""
    fragment = _make_fragment(
        "FIN_02_income",
        [
            ["项目", "合计"],
            ["事业收入", "50.00"],
            ["合计", "100.00"],
        ],
    )
    context = build_fact_context_from_artifact(
        _artifact_with([fragment], [_instance_for(fragment)]),
        org_name="测试局",
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    resolution = resolve_operand(
        context,
        "total_actual",
        table_code="FIN_02_income",
        subject_tokens=("合计", "总计"),
    )
    assert resolution.status == "ok"
    assert resolution.value == Decimal("100.0")
    assert resolution.fact.subject_name == "合计"


def test_rc01_ambiguous_total_blocks_rule_not_silent_pick() -> None:
    """两个不同值的总计候选 → blocked；引用它的关系转未完成。"""
    context = FactContext(
        facts=[
            _fact("a", "income_actual", Decimal("100")),
            _fact("b", "income_actual", Decimal("999")),
        ]
    )
    resolution = resolve_operand(context, "income_actual")
    assert resolution.status == "blocked"
    assert resolution.reason and "人工消歧" in resolution.reason


def test_rc01_bud107_blocked_fact_must_not_fall_back_to_legacy_pass() -> None:
    """BUD-107：事实被识别风险压住 → 整关系转未完成，不得回退旧表值
    返回 pass（复核反例：歧义/低置信事实被拒后仍用旧表值）。"""
    fragment = _make_fragment(
        "FIN_01_income_expenditure_total",
        [
            ["项目", "本年收入", "本年支出"],
            ["合计", "100.00", "100.00"],
        ],
    )
    context = build_fact_context_from_artifact(
        _artifact_with([fragment], [_instance_for(fragment)]),
        org_name="测试局",
        report_kind="budget",
        doc_type="budget",
    )
    assert context is not None
    # 人为把收入事实压成 low_confidence——旧表里 100 仍在，不得静默沿用
    blocked_facts = [
        (
            fact
            if fact.indicator != "income_actual"
            else dataclasses_replace_fact(fact, status="low_confidence")
        )
        for fact in context.facts
    ]
    blocked_context = FactContext(facts=blocked_facts)
    doc = build_document(
        "budget.pdf",
        ["2026年部门预算收支总表\n单位：万元\n收入预算90.00万元，支出预算100.00万元。"],
        [],
        0,
    )
    doc.fact_context = blocked_context
    output = _run_one(doc, BUD107_TextTableConsistency())
    assert output["outcomes"][0]["status"] == "insufficient_data", output["outcomes"]
    assert any(
        "low_confidence" in str(reason) or "不可作操作数" in str(reason)
        for reason in output["outcomes"][0].get("unresolved_reasons") or []
    ), output["outcomes"]
    income_receipts = [
        r for r in output["receipts"] if r.get("sub_check_id") == "T1:income_budget:text_vs_table"
    ]
    assert income_receipts, "收入关系必须有回执"
    assert income_receipts[0]["status"] == "insufficient_data", income_receipts


def dataclasses_replace_fact(fact: FiscalFact, *, status: str) -> FiscalFact:
    import dataclasses

    return dataclasses.replace(fact, recognition_status=status)


def test_rc01_bud107_fact_only_path_without_legacy_table() -> None:
    """BUD-107：没有旧二维表、只有事实时同样完成表文比对（闭环正例）。"""
    doc = build_document(
        "budget.pdf",
        ["2026年部门预算收支总表\n单位：万元\n收入预算90.00万元，支出预算90.00万元。"],
        [],
        0,
    )
    doc.fact_context = FactContext(
        facts=[
            _fact("i1", "income_actual", Decimal("100")),
            _fact("e1", "expenditure_actual", Decimal("90")),
        ]
    )
    output = _run_one(doc, BUD107_TextTableConsistency())
    issues = output["issues"]
    income_findings = [
        i for i in issues if "收入" in str(getattr(i, "message", "")) and "不一致" in str(getattr(i, "message", ""))
    ]
    assert income_findings, f"事实100 vs 文本90 必须报差异：{issues}"
    income_receipt = next(
        r for r in output["receipts"]
        if r.get("sub_check_id") == "T1:income_budget:text_vs_table"
    )
    assert income_receipt["status"] == "fail"
    assert income_receipt["operand_fact_ids"] == ["i1"], income_receipt

    # RC-10 闭环：校正 100 → 90 后，差异必须消失
    corrected = apply_fact_overlay(
        FactContext(facts=deepcopy(doc.fact_context.facts)),
        [FactOverlay(fact_uid="i1", normalized_value=Decimal("90"))],
    )
    doc2 = build_document("budget.pdf", doc.page_texts, [], 0)
    doc2.fact_context = corrected
    output2 = _run_one(doc2, BUD107_TextTableConsistency())
    assert not [
        i for i in output2["issues"]
        if "收入" in str(getattr(i, "message", "")) and "不一致" in str(getattr(i, "message", ""))
    ], output2["issues"]


# ---------------------------------------------------------------------------
# RC-02：表身份——主体/单位冲突不合并，续页合并保留来源格
# ---------------------------------------------------------------------------


def test_rc02_same_year_different_org_tables_not_merged() -> None:
    """甲局/乙局同名同年表是两张表，不得合并（复核探针实证串主体）。"""
    parser = PDFParser(None)
    jia = _make_fragment(
        "FIN_02_income",
        [["项目", "合计"], ["合计", "100.00"]],
        title="甲局2024年度收入决算表",
        page=1,
        unit_hint="单位:万元",
    )
    yi = _make_fragment(
        "FIN_02_income",
        [["项目", "合计"], ["合计", "200.00"]],
        title="乙局2024年度收入决算表",
        page=2,
        unit_hint="单位:万元",
    )
    merged = parser._coalesce_tables([jia, yi])
    assert len(merged) == 2, "同年异主体不得合并"


def test_rc02_unit_conflict_not_merged() -> None:
    """同标题但万元/元口径冲突的两段不得合并（数量级风险）。"""
    parser = PDFParser(None)
    wan = _make_fragment(
        "FIN_02_income",
        [["项目", "合计"], ["合计", "100.00"]],
        title="2024年度收入决算表",
        page=1,
        unit_hint="单位:万元",
    )
    yuan = _make_fragment(
        "FIN_02_income",
        [["项目", "合计"], ["合计", "1000000.00"]],
        title="2024年度收入决算表",
        page=2,
        unit_hint="单位:元",
    )
    merged = parser._coalesce_tables([wan, yuan])
    assert len(merged) == 2, "单位口径冲突不得合并"


def test_rc02_true_continuation_merges_with_stable_uid_and_origin() -> None:
    """真续页仍合并：合并视图 UID 由成员派生且跨解析稳定；追加格保留
    来源物理片段与原始行号（RC-02 来源格回溯）。"""
    parser = PDFParser(None)
    head = _make_fragment(
        "FIN_02_income",
        [["项目", "合计"], ["合计", "100.00"]],
        title="2024年度收入决算表",
        page=1,
    )
    tail = _make_fragment(
        "FIN_02_income",
        [["教育", "50.00"]],
        title="",
        page=2,
    )
    first = parser._coalesce_tables([head, tail])
    second = parser._coalesce_tables([deepcopy(head), deepcopy(tail)])
    assert len(first) == 1 and len(second) == 1
    assert first[0].fragment_uid == second[0].fragment_uid, "合并 UID 必须跨解析稳定"
    assert first[0].fragment_uid == _stable_fragment_uid(
        "merged", head.fragment_uid, tail.fragment_uid
    )
    appended = [c for c in first[0].cells if c.origin_fragment_uid == tail.fragment_uid]
    assert appended and all(c.origin_row_idx == 0 for c in appended), (
        "续页单元格必须保留来源片段与原始行号"
    )


def test_rc02_parser_fragment_uids_deterministic_across_reparses() -> None:
    """同一内容重复解析 → 同一片段 UID（RC-10 校正目标稳定的根）。"""
    parser = PDFParser(None)
    rows = [["项目", "本年收入", "本年支出"], ["合计", "328661.81", "328661.81"]]
    a = _make_fragment("FIN_01_income_expenditure_total", rows, title="收支总表")
    b = _make_fragment("FIN_01_income_expenditure_total", rows, title="收支总表")
    assert a.fragment_uid == b.fragment_uid
    ctx_a = build_fact_context_from_artifact(
        _artifact_with([a], [_instance_for(a)])
    )
    ctx_b = build_fact_context_from_artifact(
        _artifact_with([b], [_instance_for(b)])
    )
    uids_a = {f.fact_uid for f in ctx_a.facts}
    uids_b = {f.fact_uid for f in ctx_b.facts}
    assert uids_a == uids_b, "重复解析后事实 UID 必须一致"


# ---------------------------------------------------------------------------
# RC-08：结构化入库缓存分支——指纹一致，不旧 run 配新事实
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rc08_cache_lookup_uses_passed_artifact_fingerprints(monkeypatch) -> None:
    """缓存键必须取调用方 artifact 自身的 config/OCR 指纹（复核：固定参数
    指纹 + OCR=None 会把新配置事实挂到旧 run）。"""
    import src.services.structured_ingest_runner as runner

    captured: Dict[str, Any] = {}

    class _Abort(Exception):
        pass

    async def _fake_ready() -> bool:
        return True

    async def _fake_lookup(conn, **kwargs):
        captured.update(kwargs)
        raise _Abort("cache-lookup-captured")

    async def _fake_document_version(conn, **kwargs):
        return {"org_unit_id": 1, "document_id": 2, "document_version_id": 3}

    async def _fake_material_slot(conn, **kwargs):
        return {"status": "skipped"}

    class _StubDatabase:
        @staticmethod
        async def acquire():
            return object()

        @staticmethod
        async def release(conn):
            return None

    monkeypatch.setattr(runner, "ensure_structured_ingest_ready", _fake_ready)
    monkeypatch.setattr(runner, "find_cached_parse_run", _fake_lookup)
    monkeypatch.setattr(runner, "_ensure_document_version", _fake_document_version)
    monkeypatch.setattr(runner, "_allocate_material_slot", _fake_material_slot)
    monkeypatch.setattr(runner, "DatabaseConnection", _StubDatabase)

    artifact = ParseArtifact(
        source_file_hash="hash",
        config_fingerprint="cfg-new",
        ocr_model_fingerprint="ocr-new",
        table_fragments=[],
        logical_tables=[],
    )
    result = await runner.run_structured_ingest(
        "job-x",
        Path("unused.pdf"),
        {"organization_name": "测试局", "checksum": "f" * 64},
        parse_artifact=artifact,
        parse_config_fingerprint="cfg-old-param",
    )
    assert captured, "缓存查询必须发生"
    assert captured["config_fingerprint"] == "cfg-new", captured
    assert captured["ocr_model_fingerprint"] == "ocr-new", captured
    assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# RC-10：覆盖层——unit 支持、跨解析重映射、不支持字段记账
# ---------------------------------------------------------------------------


def test_rc10_overlay_merges_raw_value_and_unit_change() -> None:
    """同一事实的 raw_value+unit 两条变更合并为一个覆盖；按校正单位换算。"""
    records = [
        {
            "actor": "tester",
            "reason": "口径修正",
            "changes": [
                {"target_type": "fact", "target_id": "f1", "field": "raw_value", "value": "900000"},
                {"target_type": "fact", "target_id": "f1", "field": "unit", "value": "元"},
            ],
        }
    ]
    overlays = parse_overlays_from_records(records)
    assert len(overlays) == 1
    assert overlays[0].raw_value == "900000" and overlays[0].unit == "元"
    context = FactContext(facts=[_fact("f1", "income_actual", Decimal("100"))])
    new_context = apply_fact_overlay(context, overlays)
    assert new_context.fact_by_uid("f1").normalized_value == Decimal("90")


def test_rc10_identity_remap_recovers_target_across_parses() -> None:
    """UID 失效时按事实身份键唯一命中重映射；歧义/缺失转未完成，不猜。"""
    old_fact = _fact("old-uid", "income_actual", Decimal("100"))
    new_context = FactContext(facts=[_fact("new-uid", "income_actual", Decimal("100"))])
    overlays = [FactOverlay(fact_uid="old-uid", raw_value="90")]

    new_context, report = apply_fact_overlays_with_remap(
        new_context, overlays, previous_facts=[old_fact]
    )
    assert report["status"] == "applied" and report["remapped"] == 1
    assert new_context.fact_by_uid("new-uid").normalized_value == Decimal("90")

    # 歧义身份：两个同身份候选 → unresolved，主链路必须转待复核
    ambiguous_context = FactContext(
        facts=[
            _fact("n1", "income_actual", Decimal("100")),
            _fact("n2", "income_actual", Decimal("100")),
        ]
    )
    _, report2 = apply_fact_overlays_with_remap(
        ambiguous_context, [FactOverlay(fact_uid="old-uid", raw_value="90")],
        previous_facts=[old_fact],
    )
    assert report2["status"] == "failed" and report2["unresolved"]


def test_rc10_unsupported_identity_fields_are_recorded_not_dropped() -> None:
    """身份字段（year/org/scope/indicator）校正必须显式记账为未生效。"""
    records = [
        {
            "changes": [
                {"target_type": "fact", "target_id": "f1", "field": "year", "value": 2023},
                {"target_type": "fact", "target_id": "f1", "field": "manual_location",
                 "value": {"physical_page": 1, "bbox": [0, 0, 1, 1]}},
            ]
        }
    ]
    unsupported = collect_unsupported_changes(records)
    assert [item["field"] for item in unsupported] == ["year", "manual_location"]
    assert unsupported[0]["blocking"] is True
    assert unsupported[1]["blocking"] is False


# ---------------------------------------------------------------------------
# RC-12：版本绑定与导出产物防串版
# ---------------------------------------------------------------------------


def test_rc12_revision_precedes_review_report(tmp_path: Path) -> None:
    """revision 先于统一报告构建：报告的 analysis_revision 与版本快照同源。"""
    from src.schemas.review_report import build_review_report
    from api import runtime as api_runtime

    job_dir = tmp_path / "job-rc12"
    job_dir.mkdir()
    payload = {
        "job_id": job_dir.name,
        "analysis_revision": "rev-fixed-id",
        "status": "done",
        "result": {},
        "filename": "a.pdf",
    }
    (job_dir / "fact_context.json").write_text(
        '{"facts": [{"fact_uid": "f1"}]}', encoding="utf-8"
    )
    report = build_review_report(payload)
    assert report["analysis_revision"] == "rev-fixed-id"
    pointer = api_runtime.write_analysis_revision_snapshot(job_dir, payload)
    assert pointer is not None and pointer["revision_id"] == "rev-fixed-id"
    archived = job_dir / "analysis_versions" / "rev-fixed-id" / "fact_context.json"
    assert archived.is_file(), "事实快照必须随版本归档（RC-10 重映射依赖）"


def test_rc12_annotated_pdf_path_is_content_addressed() -> None:
    """同版本同问题集 → 同一不可变产物；任一不同 → 不同文件（防串版）。"""
    from api.routes.reports import _annotated_output_path

    payload_rev_a = {"analysis_revision": "rev-a"}
    payload_rev_b = {"analysis_revision": "rev-b"}
    issues_1 = [{"id": "i1", "page": 1, "bbox": [0, 0, 10, 10]}]
    issues_2 = [{"id": "i2", "page": 2, "bbox": [1, 1, 11, 11]}]
    path_a1 = _annotated_output_path("job-x", payload_rev_a, issues_1)
    path_a1_again = _annotated_output_path("job-x", payload_rev_a, deepcopy(issues_1))
    path_b1 = _annotated_output_path("job-x", payload_rev_b, issues_1)
    path_a2 = _annotated_output_path("job-x", payload_rev_a, issues_2)
    assert path_a1 == path_a1_again
    assert path_a1 != path_b1
    assert path_a1 != path_a2
    assert "job-x" in str(path_a1) and str(path_a1).endswith(".pdf")
