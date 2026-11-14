"""C1（2026-11-14 复验）：编码位数异常发现必须绑定编码原格证据，不得崩溃。

真实事故：救助任务 e8030ffd…/rev-20261114T085536 V33-222 execution_error
``KeyError: 'page'``——source_refs 的证据契约键是 ``physical_page``，发射处
却直接取不存在的 ``page``；且证据错把金额格（evidence_refs，r..c2）当"异常
编码原格"，引文用"编码＋名称"拼贴冒充原格原文。

修复口径（本文件用真实 ParseArtifact 原生网格贯穿 规则→发现→证据准入）：
1. 规则执行无 execution_error，异常编码发现进入最终 issues；
2. 证据取 ``row_identity_sources`` 编码原格：原格 ID、原字节、物理页、bbox、
   文件哈希与解析批次一并携带，引文 = 该格自己的原文；
3. 缺编码原格证据时不静默吞发现——如实降级（code_cell_evidence=unavailable）
   并回退页码定位，由证据门按未核验处理。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import check_function_disclosures
from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33222_Narrative5_T5
from src.schemas.parse_artifact import FiscalFact, ParseArtifact, ParsedCell, ParsedTable
from src.services.evidence_guard import evaluate_finding_evidence
from src.services.rule_source_binding import _current_cell_evidence

ORG = "上海市某某救助管理站"
YEAR = 2025
FILE_HASH = "a" * 64
PARSE_RUN = "run-rescue"
GRID_UID = "rescue-fin05"
AMOUNT = "772.36"

#: 说明五：条目带完整类款项标签；表内项级行编码多印一位（20820002，规范 7 位）。
NARRATIVE_PAGE = (
    "上海市某某救助管理站\n2025年度单位决算\n"
    "五、一般公共预算财政拨款支出决算情况说明\n"
    f"一般公共预算财政拨款支出共计{AMOUNT}万元。\n"
    "1、社会保障和就业支出（类）流浪乞讨人员救助（款）流浪乞讨人员救助支出（项）。"
    f"支出决算为 {AMOUNT} 万元。\n"
)

#: 表页文本层：与原格逐字一致，供证据门在所称页面核验引文。
TABLE_PAGE = (
    "一般公共预算财政拨款支出决算表\n单位：万元\n"
    f"208 社会保障和就业支出 {AMOUNT}\n"
    f"20820 流浪乞讨人员救助 {AMOUNT}\n"
    f"20820002 流浪乞讨人员救助支出 {AMOUNT}\n"
)

PAGES = [NARRATIVE_PAGE, TABLE_PAGE]


def _fact(code, name, raw, *, table="FIN_05_general_public_expenditure",
          extra=None, plain=False):
    from src.schemas.parse_artifact import EvidenceRef

    dims = {} if plain else {
        "evidence_refs": [EvidenceRef(
            file_hash=FILE_HASH, physical_page=2,
            source_cell_id=f"{GRID_UID}:r2c2", quote=raw,
            bbox=(20.0, 20.0, 29.0, 29.0),
            location_level="exact_cell").to_dict()],
        "row_identity_sources": [{
            "file_hash": FILE_HASH, "physical_page": 2,
            "source_cell_id": f"{GRID_UID}:r2c0",
            "grid_raw_text": code, "recovered_text": code,
            "native_text_sources": [{"physical_page": 2,
                                     "bbox": [0.0, 20.0, 9.0, 29.0]}],
        }],
    }
    if extra:
        dims.update(extra)
    return FiscalFact(
        fact_uid=f"fact-{code}", indicator="total_actual", raw_value=raw,
        unit="万元", normalized_value=Decimal(raw.replace(",", "")),
        org_name=ORG, org_scope="单位", fiscal_year=YEAR, period="本年",
        fund_scope="general_public_budget", budget_stage="决算",
        subject_code=code, subject_name=name, table_code=table,
        instance_uid="instance-1", extra_dims=dims,
    )


def _artifact():
    """FIN_05 原生网格：类/款/项三行；项级行编码 8 位（原件多印一位）。"""
    rows = [
        ["208", "社会保障和就业支出", AMOUNT],
        ["20820", "流浪乞讨人员救助", AMOUNT],
        ["20820002", "流浪乞讨人员救助支出", AMOUNT],
    ]
    cells = []
    for row_idx, row in enumerate(rows):
        for col, text in enumerate(row):
            cells.append(ParsedCell(
                row_idx=row_idx, col_idx=col, raw_text=text, normalized_text=text,
                numeric_value=None, page_number=2,
                bbox=(10.0 * col, 10.0 * row_idx, 10.0 * col + 9.0, 10.0 * row_idx + 9.0),
                is_header=False, confidence=1.0, unit_hint="万元",
                extraction_method="lines"))
    table = ParsedTable(
        table_code="FIN_05_general_public_expenditure", title="一般公共预算财政拨款支出决算表",
        page_number=2, table_index=0, bbox=(0.0, 0.0, 100.0, 100.0),
        header_row_count=0, confidence=1.0, extraction_method="lines",
        unit_hint="万元", rows=rows, cells=cells, fragment_uid=GRID_UID)
    return ParseArtifact(source_file_hash=FILE_HASH, parse_run_uid=PARSE_RUN,
                         physical_fragments=[table])


def _doc(*, with_row_sources=True):
    doc = build_document("rescue.pdf", PAGES, [[], []], 0)
    anomaly_extra = {"code_length_anomaly": True}
    if not with_row_sources:
        # 负例构造：剥掉编码原格来源，其余不变。
        anomaly_extra = dict(anomaly_extra)
    facts = [
        _fact("total", "合计", AMOUNT, plain=True),
        _fact("208", "社会保障和就业支出", AMOUNT, plain=True),
        _fact("20820", "流浪乞讨人员救助", AMOUNT, plain=True),
        _fact("20820002", "流浪乞讨人员救助支出", AMOUNT, extra=anomaly_extra),
    ]
    if not with_row_sources:
        facts[3].extra_dims.pop("row_identity_sources")
    doc.fact_context = FactContext(facts, FILE_HASH, PARSE_RUN,
                                   org_name=ORG, fiscal_year=YEAR, report_kind="final")
    doc.parse_artifact = _artifact()
    doc.fact_receipts = []
    doc.report_kind = "final"
    return doc, facts


def _anomaly_issues(issues):
    return [issue for issue in issues
            if getattr(issue, "location", {}) and
            issue.location.get("name_kind") == "code_length_anomaly"]


def test_anomaly_finding_carries_certified_code_cell_evidence():
    doc, _ = _doc()
    issues = check_function_disclosures(R33222_Narrative5_T5(), doc)
    anomalies = _anomaly_issues(issues)
    assert anomalies, "编码异常发现必须单独报告，不得吞进金额核对"
    location = anomalies[0].location
    assert location["code_cell_evidence"] == "certified"
    assert location["file_hash"] == FILE_HASH
    assert location["parse_run_uid"] == PARSE_RUN
    refs = location["table_refs"]
    assert refs, "必须携带异常编码原格引用"
    ref = refs[0]
    assert ref["role"] == "异常编码原格"
    assert ref["source_cell_id"] == f"{GRID_UID}:r2c0", "证据必须指向编码原格而非金额格"
    assert ref["page"] == 2 and ref["physical_page"] == 2
    assert ref["span"] == "20820002", "引文必须是编码格自己的原文"
    assert ref["raw_code"] == "20820002"
    assert ref.get("bbox"), "原格 bbox 必须随证据携带"
    # fact_ids 与说明侧页码
    assert anomalies[0].fact_ids == ["fact-20820002"]
    assert anomalies[0].evidence_text == "20820002"


def test_anomaly_finding_survives_evidence_admission():
    """发现 → 真实序列化/证据装订 → 证据门：引文可回原件且逐字命中。"""
    from src.engine.pipeline import _issue_to_dict
    from src.services.rule_source_binding import bind_rule_sources

    doc, facts = _doc()
    issues = check_function_disclosures(R33222_Narrative5_T5(), doc)
    anomalies = _anomaly_issues(issues)
    assert anomalies
    issue = anomalies[0]
    # 规则侧先自证：异常事实的证据引用可通过原格认证器（与装订同一口径）。
    anomaly_fact = next(f for f in facts if f.subject_code == "20820002")
    verified = _current_cell_evidence([anomaly_fact], doc.parse_artifact,
                                      doc.page_texts, FILE_HASH)
    assert verified, "事实证据引用必须绑定当前原件"
    # 真实管道：legacy 序列化 → 原件装订 → 证据门。
    finding = _issue_to_dict(issue, 1, doc)
    result = {"rule_findings": [finding]}
    bind_rule_sources(result, page_texts=doc.page_texts,
                      source_file_hash=FILE_HASH, artifact=doc.parse_artifact,
                      fact_context=doc.fact_context)
    finding = result["rule_findings"][0]
    complete, missing, verification = evaluate_finding_evidence(
        finding, document_page_count=len(doc.page_texts),
        page_texts=doc.page_texts, source_file_hash=FILE_HASH)
    assert complete, f"证据门未通过: missing={missing} verification={verification}"
    assert verification["status"] == "verified"


def test_missing_code_cell_sources_degrades_honestly_without_crash():
    """无编码原格来源：不崩溃、不静默吞发现，如实登记证据缺口。"""
    doc, _ = _doc(with_row_sources=False)
    issues = check_function_disclosures(R33222_Narrative5_T5(), doc)
    anomalies = _anomaly_issues(issues)
    assert anomalies, "缺原格证据也必须保留发现本身，不得静默跳过"
    location = anomalies[0].location
    assert location["code_cell_evidence"] == "unavailable"
    roles = {ref.get("role") for ref in location["table_refs"]}
    assert any("编码原格证据缺失" in str(role) for role in roles), \
        "回退引用必须如实标注金额格角色，不得冒充编码原格"


def test_wrong_hash_or_foreign_cell_not_certified():
    """编码原格引用换文件哈希/指向不存在格：不得声称 certified。"""
    doc, facts = _doc()
    anomaly_fact = next(f for f in facts if f.subject_code == "20820002")
    anomaly_fact.extra_dims["row_identity_sources"][0]["file_hash"] = "b" * 64
    issues = check_function_disclosures(R33222_Narrative5_T5(), doc)
    anomalies = _anomaly_issues(issues)
    assert anomalies
    assert anomalies[0].location["code_cell_evidence"] == "unavailable"
