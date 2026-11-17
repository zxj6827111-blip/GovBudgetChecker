from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber
import pymupdf

from src.engine.fact_context import build_fact_context_from_artifact
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.analyze_dual import DualModeAnalyzer
from src.services.evidence_guard import apply_evidence_completeness
from src.services.input_processing_identity import bind_artifact_document_identity
from src.services.pdf_page_extract import (
    extract_tables_from_page,
    extract_visible_text_from_page,
)
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import (
    _literal_ref_sources,
    bind_rule_sources,
    verify_bound_rule_locations,
)
from src.utils.issue_bbox import PDFBBoxLocator


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _draw_grid_page(
    page: pymupdf.Page,
    *,
    title: str,
    rows: List[List[str]],
    top: float = 80.0,
    col_widths: List[float],
    merged_spans: Optional[List[Tuple[int, int, int]]] = None,
) -> None:
    page.insert_text((40, top - 24), title, fontsize=11, fontname="china-s")
    row_h = 20.0
    x_starts = [40.0]
    for w in col_widths:
        x_starts.append(x_starts[-1] + w)
    y_starts = [top + r * row_h for r in range(len(rows) + 1)]
    skip_vertical: set[Tuple[int, int]] = set()
    for r, c1, c2 in merged_spans or []:
        for c in range(c1 + 1, c2 + 1):
            skip_vertical.add((r, c))
    for y in y_starts:
        page.draw_line((x_starts[0], y), (x_starts[-1], y))
    for r in range(len(rows)):
        for c_idx in range(len(x_starts)):
            if (r, c_idx) not in skip_vertical:
                page.draw_line((x_starts[c_idx], y_starts[r]), (x_starts[c_idx], y_starts[r + 1]))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                fsize = 7 if col_widths[c] < 50 else 9
                page.insert_text(
                    (x_starts[c] + 2, y_starts[r] + 14),
                    text,
                    fontsize=fsize,
                    fontname="china-s",
                )


async def _run_pipeline(
    pdf_path: Path,
    *,
    org_name: str = "上海市普陀区测试单位",
    org_level: str = "unit",
    fiscal_year: int = 2025,
    doc_type: str = "unit_final",
    report_kind: str = "final",
) -> List[Dict]:
    sha = _sha256(pdf_path)
    page_texts: List[str] = []
    page_tables: List[list] = []
    parser = PDFParser(None)  # type: ignore[arg-type]
    with pdfplumber.open(pdf_path) as source:
        for p in source.pages:
            page_texts.append(extract_visible_text_from_page(p))
            page_tables.append(extract_tables_from_page(p))
        artifact = parser.build_parse_artifact_from_handle(
            source, file_hash=sha, pdf_path=str(pdf_path)
        )
    bind_artifact_document_identity(
        artifact,
        organization_id="org-test",
        organization_name=org_name,
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        doc_type=doc_type,
        check_subject_name=org_name,
    )
    fact_ctx = build_fact_context_from_artifact(
        artifact,
        org_name=org_name,
        org_level=org_level,
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        doc_type=doc_type,
        require_complete_identity=True,
    )
    artifact_dict = artifact.to_dict()
    fact_ctx_dict = fact_ctx.to_dict()
    job_ctx = JobContext(
        job_id=f"test-{pdf_path.stem}",
        pdf_path=str(pdf_path),
        page_texts=page_texts,
        page_tables=page_tables,
        pages=len(page_texts),
        filesize=pdf_path.stat().st_size,
        meta={
            "organization_name": org_name,
            "report_kind": report_kind,
            "doc_type": doc_type,
            "report_year": fiscal_year,
            "fiscal_year": fiscal_year,
            "rules_version": "v3_3",
        },
        fact_context=fact_ctx_dict,
        parse_artifact=artifact_dict,
    )
    cfg = AnalysisConfig(dual_mode=False, ai_enabled=False, rule_enabled=True, merge_enabled=False)
    dual_res = await DualModeAnalyzer().analyze(job_ctx, cfg)
    structured_consumption = list(dual_res.meta.get("structured_consumption") or [])
    result: Dict = {
        "rule_findings": [
            f.model_dump() if hasattr(f, "model_dump") else dict(f)
            for f in (dual_res.rule_findings or [])
        ],
        "ai_findings": [],
        "merged": {"conflicts": [], "agreements": [], "rule_only": [], "ai_only": []},
        "meta": {"report_kind": report_kind, "doc_type": doc_type},
    }
    bind_rule_sources(
        result,
        page_texts=page_texts,
        source_file_hash=sha,
        artifact=artifact,
        fact_context=fact_ctx,
        consumption=structured_consumption,
    )
    verify_bound_rule_locations(result, source_pdf_path=str(pdf_path))
    apply_evidence_completeness(
        result,
        document_page_count=len(page_texts),
        page_texts=page_texts,
        source_file_hash=sha,
        source_pdf_path=str(pdf_path),
    )
    return result["rule_findings"]


def _has_bbox(finding: Dict) -> bool:
    if finding.get("bbox"):
        return True
    loc = finding.get("location") or {}
    if loc.get("bbox"):
        return True
    return any(isinstance(r, dict) and r.get("bbox") for r in (loc.get("table_refs") or []))


def test_y_tolerance_45_keeps_micro_shifted_numbers_inline_and_bbox_locator_matches(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shifted_baseline.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    # 基线微偏 3.24pt（> 旧 y_tolerance=3.0，< 新 4.5），模拟真实决算数字掉行
    page.insert_text((40, 100), "公务用车运行维护支出", fontsize=11, fontname="china-s")
    page.insert_text((158, 103.24), "2.22", fontsize=11, fontname="china-s")
    page.insert_text((196, 100), "万元。", fontsize=11, fontname="china-s")
    pdf.save(path)
    pdf.close()

    with pdfplumber.open(path) as source:
        extracted = extract_visible_text_from_page(source.pages[0])
    assert "公务用车运行维护支出2.22万元。" in extracted.replace(" ", "")
    assert "2.22\n" not in extracted

    sha = _sha256(path)
    expected_span = extracted.strip()
    result = {
        "rule_findings": [
            {
                "id": "i1",
                "source": "rule",
                "rule_id": "TEST",
                "severity": "medium",
                "title": "t",
                "message": "m",
                "location": {
                    "pages": [1],
                    "table_refs": [
                        {"role": "正文表述", "page": 1, "span": expected_span}
                    ],
                },
            }
        ]
    }
    bind_rule_sources(result, page_texts=[extracted], source_file_hash=sha)
    finding = result["rule_findings"][0]
    assert finding["evidence"]
    locator = PDFBBoxLocator(str(path))
    span_bbox = locator._native_span_bbox(finding["evidence"][0], 1)
    locator.close()
    assert span_bbox is not None
    verify_bound_rule_locations(result, source_pdf_path=str(path))
    assert finding["verification_status"] == "verified"
    assert finding["bbox"] is not None
    assert finding["location"]["table_refs"][0]["bbox"] is not None


def test_cross_page_duplicate_fragment_expands_on_page_while_single_page_duplicate_stays_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "上海市普陀区测试单位2025年度决算.pdf"
    pdf = pymupdf.open()
    p1 = pdf.new_page()
    _draw_grid_page(
        p1,
        title="六、一般公共预算财政拨款基本支出决算表\n单位：万元",
        rows=[
            ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
            ["301", "工资福利支出", "50.00", "302", "商品和服务支出", "10.00"],
            ["30101", "基本工资", "50.00", "30201", "办公费", "10.00"],
            ["人员经费合计", "", "50.00", "公用经费合计", "", "10.00"],
        ],
        top=80.0,
        col_widths=[75, 100, 55, 75, 100, 55],
    )
    # 跨页句子在第 1 页末尾起头、第 2 页继续，且第 2 页末尾碎片在同页复现
    p1.insert_text(
        (40, 260),
        "六、一般公共预算财政拨款基本支出决算情况说明\n2025年度一般公共预算财政拨款基本支出60.00万元，其中：人员经费",
        fontsize=10,
        fontname="china-s",
    )
    p2 = pdf.new_page()
    p2.insert_text(
        (40, 80),
        "55.00万元，公用经费10.00万元。\n附注：55.00万元，公用经费10.00万元。",
        fontsize=10,
        fontname="china-s",
    )
    pdf.save(path)
    pdf.close()

    with pdfplumber.open(path) as source:
        pages = [extract_visible_text_from_page(p) for p in source.pages]
    sha = _sha256(path)

    # 单页非唯一短语继续严格拒认
    ambiguous_single = {"role": "金额", "page": 2, "span": "55.00万元"}
    assert _literal_ref_sources(ambiguous_single, pages, sha) == []

    # 真实全流程下 V33-223 跨页碎片会在第 2 页按行外扩至唯一上下文并通过几何验证
    findings = asyncio.run(_run_pipeline(path))
    v223 = [f for f in findings if f.get("rule_id") == "V33-223"]
    assert v223
    for f in v223:
        assert _has_bbox(f), f
        assert f.get("verification_status") == "verified", f
        p2_refs = [r for r in (f.get("location") or {}).get("table_refs", []) if r.get("page") == 2]
        assert p2_refs and all(r.get("bbox") for r in p2_refs), f


def test_native_table_text_compare_binds_unique_context_for_repeated_amounts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "上海市普陀区测试单位2025年度决算.pdf"
    pdf = pymupdf.open()
    p0 = pdf.new_page()
    p0.insert_text((40, 100), "上海市普陀区测试单位\n2025年度部门决算", fontsize=14, fontname="china-s")
    p1 = pdf.new_page()
    _draw_grid_page(
        p1,
        title="一般公共预算财政拨款“三公”经费支出决算表\n单位：万元",
        rows=[
            ["财政拨款“三公”经费"] + [""] * 11,
            [
                "合计",
                "",
                "因公出国（境）费",
                "",
                "公务用车购置及运行维护费",
                "",
                "",
                "",
                "",
                "",
                "公务接待费",
                "",
            ],
            [
                "",
                "",
                "",
                "",
                "小计",
                "",
                "公务用车购置费",
                "",
                "公务用车运行维护费",
                "",
                "",
                "",
            ],
            ["预算数", "决算数"] * 6,
            ["120.00", "120.00", "120.00", "120.00", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00"],
        ],
        top=80.0,
        col_widths=[44] * 12,
        merged_spans=[
            (0, 0, 11),
            (1, 0, 1),
            (1, 2, 3),
            (1, 4, 9),
            (1, 10, 11),
            (2, 0, 1),
            (2, 2, 3),
            (2, 4, 5),
            (2, 6, 7),
            (2, 8, 9),
            (2, 10, 11),
        ],
    )
    p1.insert_text(
        (40, 230),
        "\n".join(
            [
                "第三部分 决算情况说明",
                "七、财政拨款“三公”经费支出决算情况说明",
                "（一）“三公”经费财政拨款支出决算总体情况说明",
                "2025年度财政拨款“三公”经费支出决算为120.00万元。",
                "（二）具体情况说明",
                "因公出国（境）费支出决算为100.00万元。",
                "公务用车购置及运行维护费支出决算为0.00万元，其中：公务用车购置费支出为0.00万元，公务用车运行维护费支出为0.00万元。",
                "公务接待费支出决算为100.00万元。",
            ]
        ),
        fontsize=10,
        fontname="china-s",
    )
    pdf.save(path)
    pdf.close()

    findings = asyncio.run(_run_pipeline(path, org_level="department", doc_type="dept_final"))
    # 全流程中 CMM-001 与 V33-224 同属三公表文组（去重后由 V33-224 承载），且同样走 unique_context=True 绑定
    three_public = [f for f in findings if f.get("rule_id") in {"CMM-001", "V33-224"}]
    assert len(three_public) >= 2, [f.get("rule_id") for f in findings]
    for item in three_public:
        assert _has_bbox(item), item
        assert item.get("verification_status") == "verified", item
        spans = [
            str(r.get("span") or r.get("quote") or "")
            for r in (item.get("location") or {}).get("table_refs", [])
            if (r.get("span") or r.get("quote"))
        ] + [str(e.get("quote") or "") for e in (item.get("evidence") or [])]
        assert any(("因公出国" in s or "公务接待费" in s) and "100.00" in s for s in spans), spans

    # 同时直接验证 CMM-001 规则本身产出包含唯一上下文的 table_refs 并能通过 bind + verify_bound_rule_locations
    from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
    from src.engine.pipeline import build_document

    sha = _sha256(path)
    with pdfplumber.open(path) as source:
        pages = [extract_visible_text_from_page(p) for p in source.pages]
        tables = [extract_tables_from_page(p) for p in source.pages]
        artifact = PDFParser(None).build_parse_artifact_from_handle(  # type: ignore[arg-type]
            source, file_hash=sha, pdf_path=str(path)
        )
    bind_artifact_document_identity(
        artifact,
        organization_id="org-test",
        organization_name="上海市普陀区测试单位",
        fiscal_year=2025,
        report_kind="final",
        doc_type="dept_final",
        check_subject_name="上海市普陀区测试单位",
    )
    fact_ctx = build_fact_context_from_artifact(
        artifact,
        org_name="上海市普陀区测试单位",
        org_level="department",
        fiscal_year=2025,
        report_kind="final",
        doc_type="dept_final",
        require_complete_identity=True,
    )
    doc = build_document(str(path), pages, tables, 2025)
    doc.parse_artifact = artifact
    doc.fact_context = fact_ctx
    doc.fact_receipts = []
    cmm_raw = CMM001_ThreePublicNarrativeConsistency().apply(doc)
    assert len(cmm_raw) >= 2
    cmm_findings = [
        {
            "id": f"cmm-{idx}",
            "source": "rule",
            "rule_id": "CMM-001",
            "title": getattr(it, "title", ""),
            "message": getattr(it, "message", ""),
            "severity": getattr(it, "severity", "warn"),
            "location": dict(getattr(it, "location", None) or {}),
        }
        for idx, it in enumerate(cmm_raw)
    ]
    cmm_res = {"rule_findings": cmm_findings}
    cmm_consumption = list(getattr(doc, "structured_consumption", []) or [])
    bind_rule_sources(
        cmm_res,
        page_texts=pages,
        source_file_hash=sha,
        artifact=artifact,
        fact_context=fact_ctx,
        consumption=cmm_consumption,
    )
    verify_bound_rule_locations(cmm_res, source_pdf_path=str(path))
    for item in cmm_findings:
        assert _has_bbox(item), item
        assert item.get("verification_status") == "verified", item
        spans = [
            str(r.get("span") or r.get("quote") or "")
            for r in item["location"].get("table_refs", [])
            if (r.get("span") or r.get("quote"))
        ] + [str(e.get("quote") or "") for e in (item.get("evidence") or [])]
        assert any(("因公出国" in s or "公务接待费" in s) and "100.00" in s for s in spans), spans


def test_v33_rules_emit_native_operand_and_literal_refs_that_verify_bbox(
    tmp_path: Path,
) -> None:
    path = tmp_path / "上海市普陀区测试单位2025年度决算.pdf"
    pdf = pymupdf.open()

    # P1: 封面（年度写错成 2026 年与 P2 目录 2025 年度冲突，触发 V33-001）
    p1 = pdf.new_page()
    p1.insert_text((40, 120), "上海市普陀区测试单位\n2026年度决算", fontsize=16, fontname="china-s")

    # P2: 目录（标注 2025 年度决算）+ T1 收入支出决算总表（本年收入 100.00 与 T2 120.00 冲突，触发 V33-200）
    p2 = pdf.new_page()
    p2.insert_text((40, 40), "目 录\n第二部分 2025年度决算表", fontsize=10, fontname="china-s")
    _draw_grid_page(
        p2,
        title="一、收入支出决算总表\n单位：万元",
        rows=[
            ["收入", "", "支出", ""],
            ["项目", "决算数", "项目", "决算数"],
            ["本年收入合计", "100.00", "本年支出合计", "80.00"],
            ["收入总计", "100.00", "支出总计", "80.00"],
        ],
        top=110.0,
        col_widths=[120, 80, 120, 80],
        merged_spans=[(0, 0, 1), (0, 2, 3)],
    )

    # P3: T2 收入决算表（本年收入合计 120.00、财政拨款收入 120.00，刻意与 P2 和 P6 不一致）
    p3 = pdf.new_page()
    _draw_grid_page(
        p3,
        title="二、收入决算表\n单位：万元",
        rows=[
            ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
            ["合计", "", "120.00", "120.00"],
            ["201", "一般公共服务支出", "120.00", "120.00"],
        ],
        col_widths=[80, 140, 100, 100],
    )

    # P4: T3 支出决算表（基本支出刻意留空，项目支出 80.00；P6 说明写基本支出 10.00 触发 V33-104）
    p4 = pdf.new_page()
    _draw_grid_page(
        p4,
        title="三、支出决算表\n单位：万元",
        rows=[
            ["科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
            ["合计", "", "80.00", "", "80.00"],
            ["201", "一般公共服务支出", "80.00", "", "80.00"],
        ],
        col_widths=[75, 135, 85, 70, 70],
    )

    # P5: T6 一般公共预算财政拨款基本支出决算表（人员经费合计 50.00、公用经费合计 10.00）
    p5 = pdf.new_page()
    _draw_grid_page(
        p5,
        title="六、一般公共预算财政拨款基本支出决算表\n单位：万元",
        rows=[
            ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
            ["301", "工资福利支出", "50.00", "302", "商品和服务支出", "10.00"],
            ["30101", "基本工资", "50.00", "30201", "办公费", "10.00"],
            ["人员经费合计", "", "50.00", "公用经费合计", "", "10.00"],
        ],
        col_widths=[75, 100, 55, 75, 100, 55],
    )

    # P6: 决算情况说明 + 重复完成率短语（测试 V33-103、V33-104、V33-223、V33-TREND-COMPLETION-RATE）
    p6 = pdf.new_page()
    p6.insert_text(
        (40, 60),
        "\n".join(
            [
                "第三部分 决算情况说明",
                "二、收入决算情况说明",
                "本年收入合计99.00万元，其中：财政拨款收入99.00万元，占100.00%。",
                "三、支出决算情况说明",
                "本年支出合计80.00万元，其中：基本支出10.00万元；项目支出70.00万元。",
                "六、一般公共预算财政拨款基本支出决算情况说明",
                "2025年度一般公共预算财政拨款基本支出60.00万元，其中：人员经费55.00万元，公用经费10.00万元。",
                "五、一般公共预算财政拨款支出决算情况说明",
                "（三）支出决算具体情况",
                "1. 一般公共服务支出（类）行政运行（款）2010301（项）。年初预算为100.00万元，调整预算为100.00万元，决算为50.00万元，完成调整预算的100.00%。",
                "2. 社会保障和就业支出（类）行政运行（款）2080501（项）。年初预算为200.00万元，调整预算为200.00万元，决算为200.00万元，完成调整预算的100.00%。",
            ]
        ),
        fontsize=9,
        fontname="china-s",
    )
    pdf.save(path)
    pdf.close()

    findings = asyncio.run(_run_pipeline(path))
    by_rule = {f.get("rule_id") for f in findings}
    expected_rules = {
        "V33-001",
        "V33-103",
        "V33-104",
        "V33-200",
        "V33-223",
        "V33-TREND-COMPLETION-RATE",
    }
    assert expected_rules <= by_rule, f"missing rules: {expected_rules - by_rule}; got {by_rule}"
    for f in findings:
        if f.get("rule_id") not in expected_rules:
            continue
        assert _has_bbox(f), f"missing bbox: {f.get('rule_id')} {f.get('title')} loc={f.get('location')}"
        assert f.get("verification_status") == "verified", f"unverified: {f.get('rule_id')} loc={f.get('location')}"
        loc = f.get("location") or {}
        for ref in loc.get("table_refs") or []:
            assert "条目首句金额" not in str(ref.get("span") or "")
