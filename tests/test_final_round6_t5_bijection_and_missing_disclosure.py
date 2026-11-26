"""Round 6 tests: T5 3-tier within-kuan bijection, leaf-only, dedup, and explicit omissions."""
from __future__ import annotations

from decimal import Decimal
import pytest

from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import _has_substantive_name_relation, _is_within_kuan_single_char_near
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33001_CoverYearUnit,
    R33220_Narrative3_T3,
    R33222_Narrative5_T5,
    R33227_Narrative5_T5_NameConsistency,
    R33234_NarrativePercentConsistency,
    extract_money_unit,
)
from src.schemas.parse_artifact import (
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
    SourceToken,
)
from src.services.pdf_parser import PDFParser

FILE_HASH = "b" * 64
FIN_05 = "FIN_05_general_public_expenditure"
FIN_03 = "FIN_03_expenditure"


def _build_t5_doc(table_rows, narrative_text, *, table_page=1, narr_page=2, extra_pages=()):
    """Build a native-grid certified Document with FIN_05 facts + ParseArtifact."""
    parser = PDFParser(None)
    table_lines = ["一般公共预算财政拨款支出决算表", "单位：万元", "功能分类科目编码 科目名称 合计 基本支出 项目支出"]
    for row in table_rows[2:]:
        table_lines.append(" ".join(str(x or "") for x in row))
    pages = ["\n".join(table_lines), narrative_text, *extra_pages]

    cells = []
    tokens = []
    facts = []
    for r, row in enumerate(table_rows):
        y0, y1 = r * 20.0 + 40.0, r * 20.0 + 58.0
        for c, val in enumerate(row):
            raw = "" if val is None else str(val)
            x0, x1 = c * 90.0 + 10.0, (c + 1) * 90.0 + 10.0
            bbox = [x0, y0, x1, y1]
            cells.append(
                ParsedCell(
                    row_idx=r,
                    col_idx=c,
                    raw_text=raw,
                    normalized_text=raw,
                    numeric_value=parser._parse_numeric(raw),
                    page_number=table_page,
                    bbox=tuple(bbox),
                    is_header=r < 2,
                    confidence=0.99,
                    unit_hint="万元",
                    extraction_method="lines",
                    native_blank_verified=not raw,
                )
            )
            if raw:
                tokens.append(
                    SourceToken(
                        token_uid=f"tok:{r}:{c}",
                        raw_text=raw,
                        physical_page=table_page,
                        bbox=bbox,
                        source_method="native_text",
                        source_ref={
                            "source_file_hash": FILE_HASH,
                            "source_block_id": f"blk:{r}:{c}",
                        },
                    )
                )
        row_key = str(row[0] or "").strip()
        if r >= 2 and (row_key.isdigit() or row_key == "合计"):
            code = "total" if row_key == "合计" else row_key
            name = "合计" if row_key == "合计" else str(row[1] or "")
            for c_idx, ind in ((2, "total_actual"), (3, "basic_actual"), (4, "project_actual")):
                amt_raw = str(row[c_idx] or "").strip()
                if not amt_raw:
                    continue
                facts.append(
                    FiscalFact(
                        fact_uid=f"{FIN_05}:{ind}:{code}",
                        indicator=ind,
                        raw_value=amt_raw,
                        unit="万元",
                        normalized_value=Decimal(amt_raw),
                        org_name="测试单位",
                        org_scope="单位",
                        fiscal_year=2025,
                        period="本年",
                        fund_scope="general_public_budget",
                        budget_stage="决算",
                        subject_code=code,
                        subject_name=name,
                        table_code=FIN_05,
                        instance_uid="inst:fin05",
                        extra_dims={
                            "evidence_refs": [
                                EvidenceRef(
                                    file_hash=FILE_HASH,
                                    physical_page=table_page,
                                    source_cell_id=f"frag_fin05_{table_page}:r{r}c{c_idx}",
                                    quote=amt_raw,
                                    bbox=[c_idx * 90.0 + 10.0, y0, (c_idx + 1) * 90.0 + 10.0, y1],
                                    location_level="exact_cell",
                                ).to_dict()
                            ]
                        },
                    )
                )

    frag = ParsedTable(
        FIN_05,
        "一般公共预算财政拨款支出决算表",
        table_page,
        0,
        (10.0, 40.0, 460.0, len(table_rows) * 20.0 + 40.0),
        2,
        0.99,
        "lines",
        "万元",
        table_rows,
        cells,
        f"frag_fin05_{table_page}",
    )
    lt = LogicalTableInstance(
        instance_uid="inst:fin05",
        table_code=FIN_05,
        title="一般公共预算财政拨款支出决算表",
        fragment_uids=[frag.fragment_uid],
        page_start=table_page,
        page_end=table_page,
    )
    artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_round6",
        source_tokens=tokens,
        table_fragments=[frag],
        logical_tables=[lt],
    )

    doc = build_document("round6_test.pdf", pages, [table_rows] + [[] for _ in range(len(pages) - 1)], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in pages]
    doc.dominant_year = 2025
    doc.report_kind = "final"
    doc.anchors = {"一般公共预算财政拨款支出决算表": [table_page]}
    doc.parse_artifact = artifact
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_round6",
        report_kind="final",
        fiscal_year=2025,
        org_name="测试单位",
        facts=facts,
    )
    return doc


def test_within_kuan_helpers_preserve_safety_boundaries():
    assert _is_within_kuan_single_char_near("执法监督", "执法监管")
    assert not _is_within_kuan_single_char_near("小学教育", "中学教育")
    assert not _is_within_kuan_single_char_near("初中教育", "高中教育")
    assert _has_substantive_name_relation("农业资源保护修复与利用", "农业生态资源保护")
    assert not _has_substantive_name_relation("专项奖励经费", "信息化运维服务")


def test_tier1_within_kuan_bijection_amount_match_unblocks_and_dedups():
    """档位 1：同款 M==1, N==1 且表文金额一致 → V33-222 通过（无阻塞）+ 合并后仅 1 条名称不一致问题。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "100.00", "70.00", "30.00"],
        ["21301", "农业农村", "100.00", "70.00", "30.00"],
        ["2130101", "行政运行", "70.00", "70.00", ""],
        ["2130135", "农业生态资源保护", "30.00", "", "30.00"],
        ["合计", "", "100.00", "70.00", "30.00"],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为100.00万元。\n"
        "1.农林水支出（类）农业农村（款）行政运行（项）。支出决算为70.00万元。\n"
        "2.农林水支出（类）农业农村（款）农业资源保护修复与利用（项）。支出决算为30.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    v222_issues = R33222_Narrative5_T5().apply(doc)
    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc)

    # 方案 A：V33-222 完成金额核对（0 金额问题、0 阻塞），V33-227 报出唯一名称不一致问题，去重后注入核对说明
    assert v222_issues == []
    assert len(v227_issues) == 1
    assert v227_issues[0].location["code"] == "2130135"

    merged = deduplicate_native_issues([*v222_issues, *v227_issues], doc=doc)
    assert len(merged) == 1
    assert merged[0].location.get("code") == "2130135"
    assert "金额一致" in merged[0].message
    assert merged[0].location.get("binding_tier") == "tier1_amount_match"


def test_tier2a_within_kuan_bijection_amount_mismatch_emits_both_issues_without_deferral():
    """档位 2a：同款 M==1, N==1 + 有关联/款闭环 + 表文金额不等 → 同步报名称错 + 金额错（high），不阻塞。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "100.00", "70.00", "30.00"],
        ["21301", "农业农村", "100.00", "70.00", "30.00"],
        ["2130101", "行政运行", "70.00", "70.00", ""],
        ["2130135", "农业生态资源保护", "30.00", "", "30.00"],
        ["合计", "", "100.00", "70.00", "30.00"],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为100.00万元。\n"
        "1.农林水支出（类）农业农村（款）行政运行（项）。支出决算为70.00万元。\n"
        "2.农林水支出（类）农业农村（款）农业资源保护修复与利用（项）。支出决算为25.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    v222_issues = R33222_Narrative5_T5().apply(doc)
    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc)

    assert len(v222_issues) == 1
    assert v222_issues[0].severity == "error" and "表文金额不一致" in v222_issues[0].message
    assert len(v227_issues) == 1
    assert v227_issues[0].location["code"] == "2130135"

    merged = deduplicate_native_issues([*v222_issues, *v227_issues], doc=doc)
    # 1 条表文金额不一致（error） + 1 条名称不一致（warn，携带表文金额亦不一致说明）
    assert len(merged) == 2
    assert any(i.severity == "error" and "表文金额不一致" in i.message for i in merged)
    assert any(i.location.get("code") == "2130135" and "表文金额亦不一致" in i.message for i in merged)


def test_tier2b_zero_overlap_and_unclosed_kuan_amount_mismatch_falls_back_to_review():
    """档位 2b 反例：零公共实义字 + 款级不闭环 + 表文金额不等 → 疑似表漏列+说明多列，必须退回待人工复核。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "120.00", "70.00", "50.00"],
        ["21301", "农业农村", "120.00", "70.00", "50.00"],
        ["2130101", "行政运行", "70.00", "70.00", ""],
        ["2130199", "信息化运维服务", "30.00", "", "30.00"],  # 70+30 != 120 → 款级未闭环
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为120.00万元。\n"
        "1.农林水支出（类）农业农村（款）行政运行（项）。支出决算为70.00万元。\n"
        "2.农林水支出（类）农业农村（款）农田水利建设补贴（项）。支出决算为50.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    with pytest.raises(RuleDeferred) as exc_info:
        R33222_Narrative5_T5().apply(doc)
    # 绝不报表文金额不一致（不能强绑）
    assert not any("表文金额不一致" in i.message for i in exc_info.value.partial_issues)
    assert any("退回人工复核" in r for r in exc_info.value.unresolved_reasons)


def test_tier3_multi_unmatched_items_must_stay_review_required_even_if_amounts_match():
    """档位 3 反例：同款 M>=2 且 N>=2 多项非近名错名，即使金额碰巧相等也严格保持待人工复核。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "150.00", "70.00", "80.00"],
        ["21301", "农业农村", "150.00", "70.00", "80.00"],
        ["2130101", "行政运行", "70.00", "70.00", ""],
        ["2130135", "病虫害防控补助", "50.00", "", "50.00"],
        ["2130199", "乡村绿化养护", "30.00", "", "30.00"],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为150.00万元。\n"
        "1.农林水支出（类）农业农村（款）行政运行（项）。支出决算为70.00万元。\n"
        "2.农林水支出（类）农业农村（款）农产品质量抽检（项）。支出决算为50.00万元。\n"
        "3.农林水支出（类）农业农村（款）农机购置补助（项）。支出决算为30.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    with pytest.raises(RuleDeferred) as exc_info:
        R33222_Narrative5_T5().apply(doc)
    assert any("多对多/一对多未匹配科目" in r for r in exc_info.value.unresolved_reasons)


def test_iterative_kuan_near_name_peel_0081f36f_pattern():
    """0081f36f 同款：1 字差“执法监督↔执法监管”迭代消解后，余下唯一“农业资源保护修复与利用↔农业生态资源保护”闭环通过。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "200.00", "100.00", "100.00"],
        ["21301", "农业农村", "200.00", "100.00", "100.00"],
        ["2130101", "行政运行", "40.00", "40.00", ""],
        ["2130110", "执法监管", "60.00", "60.00", ""],
        ["2130135", "农业生态资源保护", "100.00", "", "100.00"],
        ["合计", "", "200.00", "100.00", "100.00"],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为200.00万元。\n"
        "1.农林水支出（类）农业农村（款）行政运行（项）。支出决算为40.00万元。\n"
        "2.农林水支出（类）农业农村（款）执法监督（项）。支出决算为60.00万元。\n"
        "3.农林水支出（类）农业农村（款）农业资源保护修复与利用（项）。支出决算为100.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    v222_issues = R33222_Narrative5_T5().apply(doc)
    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc)

    assert v222_issues == []
    assert {i.location["code"] for i in v227_issues} == {"2130110", "2130135"}
    merged = deduplicate_native_issues([*v222_issues, *v227_issues], doc=doc)
    assert len(merged) == 2
    assert {i.location["code"] for i in merged} == {"2130110", "2130135"}
    assert any(i.location.get("code") == "2130135" and "金额一致" in i.message for i in merged)

    # 再验非官方变体的纯单字近名“基层监督↔基层监管”同样经 Pass 2 迭代消解后解锁剩余旧名
    t5_rows2 = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["213", "农林水支出", "200.00", "100.00", "100.00"],
        ["21301", "农业农村", "200.00", "100.00", "100.00"],
        ["2130101", "行政运行", "40.00", "40.00", ""],
        ["2130110", "基层监管", "60.00", "60.00", ""],
        ["2130135", "农业生态资源保护", "100.00", "", "100.00"],
        ["合计", "", "200.00", "100.00", "100.00"],
    ]
    narr2 = narr.replace("执法监督", "基层监督")
    doc2 = _build_t5_doc(t5_rows2, narr2)
    v222_issues2 = R33222_Narrative5_T5().apply(doc2)
    v227_issues2 = R33227_Narrative5_T5_NameConsistency().apply(doc2)
    assert v222_issues2 == []
    assert {i.location["code"] for i in v227_issues2} == {"2130110", "2130135"}
    merged2 = deduplicate_native_issues([*v222_issues2, *v227_issues2], doc=doc2)
    assert len(merged2) == 2
    assert {i.location["code"] for i in merged2} == {"2130110", "2130135"}
    assert all("金额一致" in i.message for i in merged2)


def test_leaf_only_06558ce4_pattern_resolves_both_v222_and_v227():
    """06558ce4 项级表（无类/款行）：2080502 事业单位退休↔事业单位离退休（同有 2101102 事业单位医疗）→ 双规则联动闭环。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["", "", "", "", ""],
        ["2080502", "事业单位离退休", "3.02", "3.02", ""],
        ["2080505", "机关事业单位基本养老保险缴费支出", "60.55", "60.55", ""],
        ["2101102", "事业单位医疗", "39.74", "39.74", ""],
        ["合计", "", "103.31", "103.31", ""],
    ]
    narr = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "2025年度一般公共预算财政拨款支出决算为103.31万元。\n"
        "社会保障和就业支出（类）63.57万元。\n"
        "1、社会保障和就业支出（类）行政事业单位退休（款）事业单位退休（项）。支出决算为3.02万元。\n"
        "2、社会保障和就业支出（类）行政事业单位离退休（款）机关事业单位基本养老保险缴费支出（项）。支出决算为60.55万元。\n"
        "3、医疗卫生与计划生育支出（类）行政事业单位医疗（款）事业单位医疗（项）。支出决算为39.74万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = _build_t5_doc(t5_rows, narr)
    v222_issues = R33222_Narrative5_T5().apply(doc)
    assert v222_issues == []

    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc)
    assert {i.location.get("code") for i in v227_issues} == {"2080502"}
    assert v227_issues[0].location["actual_name"] == "事业单位退休"

    merged = deduplicate_native_issues([*v222_issues, *v227_issues], doc=doc)
    assert len(merged) == 1
    assert merged[0].location["code"] == "2080502"
    assert "金额一致" in merged[0].message


def test_task2_single_table_missing_unit_emits_issue_and_keeps_downstream_deferred():
    """任务二(1) 0e53f6cb 口径：单表表头漏印金额单位 → R33001 报规范性问题，同时 V33-220 仍因缺单位转待核。"""
    p1 = "单位名称：测试单位\n2025年度决算公开"
    p2 = "收入支出决算总表\n单位：万元\n本年收入 204.09\n本年支出 204.09"
    # P3 支出决算表刻意漏标“单位：万元”
    p3 = "支出决算表\n项目 本年支出合计 基本支出 项目支出\n合计 204.09 154.74 49.35\n201 一般公共服务支出 204.09 154.74 49.35"
    p4 = "三、支出决算情况说明\n本年支出合计204.09万元，其中：基本支出154.74万元，项目支出49.35万元。\n四、财政拨款情况"
    pages = [p1, p2, p3, p4]

    t3_rows = [
        ["项目", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "204.09", "154.74", "49.35"],
    ]
    frag3 = ParsedTable(
        FIN_03,
        "支出决算表",
        3,
        0,
        (10.0, 40.0, 300.0, 100.0),
        1,
        0.99,
        "lines",
        "",
        t3_rows,
        [],
        "frag_fin03_3",
    )
    lt3 = LogicalTableInstance(
        instance_uid="inst:fin03",
        table_code=FIN_03,
        title="支出决算表",
        fragment_uids=["frag_fin03_3"],
        page_start=3,
        page_end=3,
    )
    fact3 = FiscalFact(
        fact_uid=f"{FIN_03}:total_actual:total",
        indicator="total_actual",
        raw_value="204.09",
        unit=None,
        normalized_value=None,
        org_name="测试单位",
        org_scope="单位",
        fiscal_year=2025,
        period="本年",
        fund_scope="all",
        budget_stage="决算",
        subject_code="total",
        subject_name="合计",
        table_code=FIN_03,
        instance_uid="inst:fin03",
    )
    doc = build_document("fake_0e53.pdf", pages, [[], [], t3_rows, []], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in pages]
    doc.dominant_year = 2025
    doc.report_kind = "final"
    doc.anchors = {"收入支出决算总表": [2], "支出决算表": [3]}
    doc.parse_artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_0e53",
        table_fragments=[frag3],
        logical_tables=[lt3],
    )
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_0e53",
        report_kind="final",
        fiscal_year=2025,
        org_name="测试单位",
        facts=[fact3],
    )

    meta_issues = R33001_CoverYearUnit().apply(doc)
    missing_unit_issues = [i for i in meta_issues if "表头缺失金额单位标注" in i.message]
    assert len(missing_unit_issues) == 1
    assert missing_unit_issues[0].severity == "warn"
    assert missing_unit_issues[0].location["page"] == 3
    assert missing_unit_issues[0].location["table_refs"][0]["span"]

    with pytest.raises(RuleDeferred):
        R33220_Narrative3_T3().apply(doc)


def test_task2_multipage_table_missing_unit_on_start_page_only():
    """多页表起始页缺单位而续页有单位（0e53f6cb P8/P9 形态）仍报缺单位；起始页已有单位时不误报。"""
    p1 = "单位名称：测试单位\n2025年度决算公开"
    p2 = "收入支出决算总表\n单位：万元\n本年收入 204.09\n本年支出 204.09"
    p3 = "支出决算表\n项目 本年支出合计 基本支出 项目支出\n合计 204.09 154.74 49.35"
    p4 = "单位：万元\n210 卫生健康支出 3.86 3.86"
    pages = [p1, p2, p3, p4]

    pf_p3 = ParsedTable(FIN_03, "支出决算表", 3, 0, (10.0, 40.0, 300.0, 100.0), 1, 0.99, "lines", "", [["合计", "204.09"]], [], "pf_p3")
    pf_p4 = ParsedTable("UNKNOWN_P4", "支出决算表", 4, 0, (10.0, 40.0, 300.0, 100.0), 1, 0.99, "lines", "万元", [["210", "3.86"]], [], "pf_p4")
    # 合并后的 table_fragment 继承了“万元”，但物理起始页 pf_p3 无单位
    tf_merged = ParsedTable(FIN_03, "支出决算表", 3, 0, (10.0, 40.0, 300.0, 100.0), 1, 0.99, "lines", "万元", [["合计", "204.09"], ["210", "3.86"]], [], "tf_fin03")
    lt = LogicalTableInstance("inst:fin03", FIN_03, "支出决算表", ["tf_fin03"], 3, 4)

    doc = build_document("fake_multi.pdf", pages, [[], [], [], []], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in pages]
    doc.dominant_year = 2025
    doc.parse_artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_multi",
        physical_fragments=[pf_p3, pf_p4],
        table_fragments=[tf_merged],
        logical_tables=[lt],
    )
    issues = R33001_CoverYearUnit().apply(doc)
    miss = [i for i in issues if "表头缺失金额单位标注" in i.message]
    assert len(miss) == 1 and miss[0].location["page"] == 3

    # 负控：若起始页已有单位而续页没有，不误报
    pages_ok = [p1, p2, "支出决算表\n单位：万元\n合计 204.09 154.74 49.35", "210 卫生健康支出 3.86 3.86"]
    doc_ok = build_document("fake_ok.pdf", pages_ok, [[], [], [], []], 1024)
    doc_ok.units_per_page = [extract_money_unit(p) for p in pages_ok]
    doc_ok.dominant_year = 2025
    doc_ok.parse_artifact = ParseArtifact(
        source_file_hash=FILE_HASH,
        parse_run_uid="run_ok",
        physical_fragments=[ParsedTable(FIN_03, "支出决算表", 3, 0, (10.0, 40.0, 300.0, 100.0), 1, 0.99, "lines", "万元", [["合计", "204.09"]], [], "pf_ok")],
        table_fragments=[tf_merged],
        logical_tables=[lt],
    )
    ok_issues = R33001_CoverYearUnit().apply(doc_ok)
    assert not any("表头缺失金额单位标注" in i.message for i in ok_issues)


def test_task2_procurement_missing_subitem_amount_emits_issue_and_keeps_deferred():
    """任务二(2) 035c9842 口径：工程采购漏写金额 → V33-234 明确提示漏填，同时保留下游阻塞不猜 0。"""
    p1 = "单位名称：测试单位\n2023年度决算公开\n单位：万元"
    p2 = (
        "五、其他重要事项情况说明\n"
        "（二）政府采购支出情况\n"
        "测试单位2023年度政府采购金额（以合同签订为准）为20.35万元，"
        "其中：货物采购金额16.38万元、工程采购 服务采购金额3.97万元。\n"
        "六、国有资产占用情况说明"
    )
    doc = build_document("fake_035c.pdf", [p1, p2], [[], []], 1024)
    doc.units_per_page = [extract_money_unit(p) for p in [p1, p2]]
    doc.dominant_year = 2023
    doc.report_kind = "final"
    doc.fact_context = FactContext(
        version_token="v1",
        file_hash=FILE_HASH,
        parse_run_uid="run_035c",
        report_kind="final",
        fiscal_year=2023,
        org_name="测试单位",
        facts=[],
    )

    with pytest.raises(RuleDeferred) as exc_info:
        R33234_NarrativePercentConsistency().apply(doc)

    miss_issues = [i for i in exc_info.value.partial_issues if "工程采购" in i.message and "漏写" in i.message]
    assert len(miss_issues) == 1
    assert miss_issues[0].severity == "warn"
    assert miss_issues[0].location["page"] == 2
    assert miss_issues[0].location["table_refs"]
    assert any("未唯一列出货物、工程、服务三项" in r for r in exc_info.value.unresolved_reasons)
