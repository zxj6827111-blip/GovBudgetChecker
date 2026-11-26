"""真实生成的非规则 PDF 经公开解析入口验证，防止恢复模块存在但入口未接入。"""

import pymupdf
import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.services.pdf_parser import PDFParser
from src.services.table_reading_assessment import assess_table_reading


def text(page, x, y, value, size=10):
    page.insert_text(
        (x, y), value, fontname="tiro" if value.isascii() else "china-s", fontsize=size
    )


def lines(page, xs, ys, *, vertical_bottom=None):
    for x in xs:
        page.draw_line((x, ys[0]), (x, vertical_bottom or ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))


@pytest.mark.parametrize("blocked_side", ["personnel", "public"])
def test_economic_rule_keeps_proven_findings_when_opposite_lane_is_unreadable(tmp_path, blocked_side):
    import pdfplumber

    from src.engine.pipeline import build_document, build_issues_payload
    from src.engine.rules_v33 import R33117_BasicExpenseClassification, R33243_Table6_BasicExpenseAdvancedCheck
    from src.schemas.review_report import build_review_report
    from src.services.evidence_guard import apply_evidence_completeness
    from src.services.pdf_page_extract import extract_tables_from_page, extract_visible_text_from_page
    from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations

    path = tmp_path / "economic-independent.pdf"
    personnel_blocked = blocked_side == "personnel"
    personnel = "30" if personnel_blocked else "301"
    public = "30" if not personnel_blocked else "302"
    rows = [
        ["经济分类科目编码", "科目名称", "决算数"] * 2,
        [personnel, "工资福利支出", "100.00", public, "商品和服务支出", "30.00"],
        [personnel + "01", "基本工资", "65.00", public + "01", "办公费", "12.00"],
        [personnel + "02", "津贴补贴", "40.00", public + "02", "印刷费", "20.00"],
        ["303", "对个人和家庭的补助", "20.00", "310", "资本性支出", "5.00"],
        ["30301", "离休费", "20.00", "31001", "房屋建筑物购建", "5.00"],
        ["人员经费合计", "", "120.00", "公用经费合计", "", "35.00"],
    ]
    xs = [36, 146, 276, 353, 463, 593, 670]
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=710, height=650)
        text(page, 36, 60, "一般公共预算财政拨款基本支出决算表")
        text(page, 590, 80, "单位：万元")
        lines(page, xs, [100 + index * 24 for index in range(len(rows) + 1)])
        for index, row in enumerate(rows):
            for x, value in zip(xs, row, strict=False):
                if value:
                    text(page, x + 4, 117 + index * 24, value)
        pdf.save(path)
    with pdfplumber.open(path) as pdf:
        texts = [extract_visible_text_from_page(page) for page in pdf.pages]
        tables = [extract_tables_from_page(page) for page in pdf.pages]
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(
        artifact, org_name="甲局", fiscal_year=2025, report_kind="final", doc_type="dept_final",
    )
    doc = build_document(str(path), texts, tables, path.stat().st_size)
    doc.parse_artifact, doc.fact_context = artifact, context
    result = build_issues_payload(doc, report_kind="final", rules=[
        R33117_BasicExpenseClassification(), R33243_Table6_BasicExpenseAdvancedCheck(),
    ])
    receipts = {(r["rule_id"], r["sub_check_id"]): r for r in result["fact_receipts"]}
    bad_name, good_name = ("人员经费合计", "公用经费合计") if personnel_blocked else ("公用经费合计", "人员经费合计")
    assert receipts["V33-117", f"economic:summary:{bad_name}"]["status"] == "insufficient_data"
    assert receipts["V33-117", f"economic:summary:{good_name}"]["status"] == "pass"
    pending = receipts["V33-243", f"economic:lane:{2 if personnel_blocked else 5}:identity"]
    assert pending["status"] == "insufficient_data"
    assert pending["missing_operands"][0]["column"].endswith(f"（{bad_name}）")
    code = "302" if personnel_blocked else "301"
    assert receipts["V33-243", f"economic:class:{code}"]["status"] == "fail"
    assert receipts["V33-243", f"economic:class:{'310' if personnel_blocked else '303'}"]["status"] == "pass"

    bind_rule_sources(result, page_texts=texts, source_file_hash=artifact.source_file_hash,
                      artifact=artifact, fact_context=context, consumption=result["structured_consumption"])
    verify_bound_rule_locations(result, source_pdf_path=str(path))
    apply_evidence_completeness(result, document_page_count=1, page_texts=texts,
                                source_file_hash=artifact.source_file_hash, source_pdf_path=str(path))
    report = build_review_report({"status": "review_required", "result": result}, resolve_pdf_bboxes=False)
    finding, = report["issues"]
    assert finding["rule_id"] == "V33-243" and f"经济分类{code}明细归集" in finding["message"]
    assert finding["verification_status"] == "verified"
    assert {ref["source_cell_id"].rsplit("c", 1)[1] for ref in finding["evidence"]} == {
        "5" if personnel_blocked else "2",
    }


@pytest.mark.parametrize("valid_continuation", [True, False])
@pytest.mark.parametrize("table_code,title,amount_headers", [
    ("FIN_05_general_public_expenditure", "一般公共预算财政拨款支出决算表", ("合计", "基本支出", "项目支出")),
    ("FIN_02_income", "收入决算表", ("本年收入合计", "财政拨款收入", "其他收入")),
])
def test_functional_continuation_with_leading_label_tail(tmp_path, valid_continuation, table_code, title, amount_headers):
    """孤立名称尾行先核验原字符，再接回前页，不能吞入无关编号表。"""
    path = tmp_path / "label-tail.pdf"
    xs = [40, 120, 320, 410, 500, 590]
    with pymupdf.open() as pdf:
        first = pdf.new_page(width=650, height=650)
        text(first, 40, 490, title)
        text(first, 490, 505, "单位：万元")
        data = [
            ["科目编码", "科目名称", *amount_headers],
            ["208", "社会保障和就业支出", "100.00", "100.00", "0.00"],
            ["20805", "行政事业单位养老支出", "100.00", "100.00", "0.00"],
            ["2080505", "机关事业单位基本养老保险", "60.00", "60.00", "0.00"],
        ]
        lines(first, xs, [520, 545, 570, 595, 620])
        for i, row in enumerate(data):
            for x, value in zip(xs, row, strict=False):
                text(first, x + 4, 538 + i * 25, value)
        second = pdf.new_page(width=650, height=650)
        lines(second, xs, [80, 105, 130, 155])
        text(second, xs[1] + 4, 98, "缴费支出")
        row = (
            ["2080506", "机关事业单位职业年金缴费支出", "40.00", "40.00", "0.00"]
            if valid_continuation else ["2170101", "办公桌", "40.00", "40.00", "0.00"]
        )
        for x, value in zip(xs, row, strict=False):
            text(second, x + 4, 123, value)
        for x, value in zip(xs[1:], ["合计", "100.00", "100.00", "0.00"], strict=False):
            text(second, x + 4, 148, value)
        pdf.save(path)

    artifact = PDFParser(None).build_parse_artifact(str(path))
    fiscal = next(t for t in artifact.table_fragments if t.table_code == table_code)
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    if valid_continuation:
        assert max(c.page_number for c in fiscal.cells) == 2
        assert not any(row[1] == "缴费支出" for row in fiscal.rows)
        assert any(row[:2] == ["2080505", "机关事业单位基本养老保险缴费支出"] for row in fiscal.rows)
        amount = next(f for f in context.facts if f.subject_code == "2080506" and f.indicator == "total_actual")
        assert amount.extra_dims["evidence_refs"][0]["physical_page"] == 2
    else:
        assert max(c.page_number for c in fiscal.cells) == 1
        assert not any(f.subject_name == "办公桌" for f in context.facts)


@pytest.mark.parametrize("tail", ["置", "车"])
def test_basic_expense_continues_after_single_character_name_tail(tmp_path, tail):
    path = tmp_path / "economic-tail.pdf"
    xs = [40, 95, 240, 310, 365, 525, 595]
    with pymupdf.open() as pdf:
        for page_index, rows in enumerate((
            [
                ["科目编码", "科目名称", "决算数", "科目编码", "科目名称", "决算数"],
                ["301", "工资福利支出", "8.00", "310", "资本性支出", "25.00"],
                ["30101", "基本工资", "8.00", "31019", "其他交通工具购置", "5.00"],
                ["", "", "", "31021", "文物和陈列品购", ""],
            ],
            [
                ["", "", "", "", tail, ""],
                ["", "", "", "31022", "无形资产购置", "7.00"],
                ["", "", "", "31099", "其他资本性支出", "3.00"],
            ],
        )):
            page = pdf.new_page(width=625, height=650)
            top = 520 if page_index == 0 else 80
            if page_index == 0:
                text(page, 40, 490, "一般公共预算财政拨款基本支出决算表")
                text(page, 490, 505, "单位：万元")
            lines(page, xs, [top + i * 25 for i in range(len(rows) + 1)])
            for index, row in enumerate(rows):
                for x, value in zip(xs, row, strict=False):
                    if value:
                        text(page, x + 4, top + index * 25 + 18, value)
        pdf.save(path)

    artifact = PDFParser(None).build_parse_artifact(str(path))
    fiscal = next(t for t in artifact.table_fragments if t.table_code == "FIN_06_basic_expenditure")
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    if tail == "置":
        name = next(c for c in fiscal.cells if c.raw_text == "文物和陈列品购置")
        assert [ref["physical_page"] for ref in name.native_text_sources] == [1, 2]
        fact = next(f for f in context.facts if f.subject_code == "31022")
        assert fact.raw_value == "7.00"
        assert fact.extra_dims["evidence_refs"][0]["physical_page"] == 2
    else:
        assert max(c.page_number for c in fiscal.cells) == 1
        assert not any(f.subject_code == "31022" for f in context.facts)


@pytest.mark.parametrize("aligned", [True, False])
def test_grant_table_merges_only_uniquely_aligned_footer_rows(tmp_path, aligned):
    path = tmp_path / "merged-grant.pdf"
    xs = [30, 175, 230, 385, 435, 500, 565, 635]
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=675, height=650)
        text(page, 30, 390, "财政拨款收入支出决算总表")
        text(page, 575, 415, "单位：万元")
        ys = [430, 450, 480, 505, 530]
        for x in (xs[0], xs[2], xs[-1]):
            page.draw_line((x, ys[0]), (x, ys[-1] if x == xs[2] else 585))
        for x in (xs[1], *xs[3:-1]):
            page.draw_line((x, ys[1]), (x, ys[-1]))
        for y in [*ys, 585]:
            page.draw_line((xs[0], y), (xs[-1], y))
        text(page, 70, 444, "收入")
        text(page, 270, 444, "支出")
        for col, val in enumerate(("项目", "决算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算")):
            text(page, xs[col] + 3, 469, val, size=8)
        body = [
            ("一、一般公共预算财政拨款", "10.00", "十四、资源勘探工业信息等支出", "1.00", "1.00", "0.00", "0.00"),
            ("二、政府性基金预算财政拨款", "0.00", "十五、商业服务业等支出", "2.00", "2.00", "0.00", "0.00"),
        ]
        for row, y in zip(body, (498, 523), strict=True):
            for col, val in enumerate(row):
                text(page, xs[col] + 3, y, val, size=9)
        for y, label, total in ((553, "十六、金融支出", "3.00"), (574, "十七、援助其他地区支出", "4.00")):
            text(page, xs[2] + 3, y, label)
            for col, val in enumerate((total, total, "0.00", "0.00"), start=3):
                x = xs[col] + 3 if aligned or col != 4 else xs[col] - 3
                text(page, x, y, val)
        pdf.save(path)

    artifact = PDFParser(None).build_parse_artifact(str(path))
    fiscal = next(t for t in artifact.table_fragments if t.table_code == "FIN_04_fiscal_grant_total")
    restored = [c for c in fiscal.cells if any(
        src.get("recovery_kind") == "merged_ledger_tail"
        for src in (c.native_text_sources or [])
    )]
    assert bool(restored) is aligned
    if aligned:
        assert fiscal.rows[-1][2:] == ["十七、援助其他地区支出", "4.00", "4.00", "0.00", "0.00"]


def test_repeated_invalid_amounts_bind_each_original_cell(tmp_path):
    from src.engine.checklist_text_rules import CMM010_AmountFormat
    from src.engine.pipeline import build_document, build_issues_payload
    from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations

    path = tmp_path / "repeated-invalid-amounts.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=650, height=650)
        text(page, 40, 60, "支出决算表")
        text(page, 500, 85, "单位：万元")
        xs = [40, 110, 300, 410, 510, 600]
        lines(page, xs, [100, 125, 150, 175, 200])
        rows = [
            ["科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
            ["220", "自然资源海洋气象等支出", "5.00", "2.359.46", "0.00"],
            ["22001", "自然资源事务", "5.00", "2.359.46", "0.00"],
            ["2200150", "事业运行", "5.00", "2.359.46", "0.00"],
        ]
        for i, row in enumerate(rows):
            for x, value in zip(xs, row, strict=False):
                text(page, x + 4, 118 + 25 * i, value)
        page_texts = [page.get_text()]
        pdf.save(path)
    artifact = PDFParser(None).build_parse_artifact(str(path))
    doc = build_document(str(path), page_texts, [[]], path.stat().st_size)
    doc.parse_artifact = artifact
    payload = build_issues_payload(doc, report_kind="final", rules=[CMM010_AmountFormat()])
    findings = payload["issues"]["all"]
    result = {"rule_findings": findings}
    bind_rule_sources(result, page_texts=page_texts, source_file_hash=artifact.source_file_hash, artifact=artifact)
    verify_bound_rule_locations(result, source_pdf_path=str(path))
    assert [f["location"]["row_code"] for f in findings] == ["220", "22001", "2200150"]
    assert all(f["location_level"] == "exact_cell" for f in findings)
    assert len({tuple(f["bbox"]) for f in findings}) == 3
    with pymupdf.open(path) as pdf:
        hits = pdf[0].search_for("2.359.46")
        for finding, hit in zip(findings, hits, strict=True):
            assert pymupdf.Rect(finding["bbox"]).contains(hit)


@pytest.fixture(params=["decimal_wrap", "merged_rows", "missing_money_grid", "open_fiscal_tail"])
def irregular_pdf(tmp_path, request):
    kind = request.param
    path = tmp_path / f"{kind}.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=1000, height=650)
        text(page, 40, 80, "单位：万元")
        if kind == "decimal_wrap":
            text(page, 40, 60, "收入支出决算总表")
            xs, ys = [40, 150, 260, 400, 510], [100, 120, 140, 180, 220]
            for y in ys:
                page.draw_line((40, y), (510, y))
            for x in xs:
                page.draw_line((x, 100 if x in (40, 260, 510) else 120), (x, 220))
            text(page, 44, 115, "收入")
            text(page, 264, 115, "支出")
            for i, v in enumerate(["项目", "决算数", "项目", "决算数"]):
                text(page, xs[i] + 4, 135, v)
            for i, v in enumerate(["财政拨款收入", "10.00", "一般公共服务支出", "10.00"]):
                text(page, xs[i] + 4, 165, v)
            text(page, 154, 195, "123.4")
            text(page, 174, 210, "5")
            text(page, 44, 204, "事业收入")
            text(page, 264, 204, "教育支出")
            text(page, 404, 204, "50.00")
            expected = [(None, "事业收入", "income_actual", "123.45")]
        elif kind in {"merged_rows", "missing_money_grid"}:
            title = "支出决算表" if kind == "merged_rows" else "一般公共预算财政拨款支出决算表"
            text(page, 40, 60, title)
            xs = [40, 110, 300, 400, 500, 610]
            ys = [100, 140, 180, 220, 300] if kind == "merged_rows" else [100, 140, 180, 220]
            lines(page, xs, ys, vertical_bottom=300 if kind == "merged_rows" else 260)
            if kind == "missing_money_grid":
                page.draw_line((40, 260), (300, 260))
            for i, v in enumerate(
                [
                    "科目编码",
                    "科目名称",
                    "本年支出合计" if kind == "merged_rows" else "合计",
                    "基本支出",
                    "项目支出",
                ]
            ):
                text(page, xs[i] + 4, 125, v)
            for y, row in [
                (165, ["201", "一般公共服务支出", "300.00", "200.00", "100.00"]),
                (205, ["20101", "人大事务", "300.00", "200.00", "100.00"]),
            ]:
                for i, v in enumerate(row):
                    text(page, xs[i] + 4, y, v)
            if kind == "merged_rows":
                for y, row in [
                    (245, ["2010101", "行政运行", "10.00", "10.00", ""]),
                    (280, ["2010102", "一般行政管理事务", "20.00", "20.00", ""]),
                ]:
                    for i, v in enumerate(row):
                        if v:
                            text(page, xs[i] + 4, y, v)
                expected = [
                    ("2010101", "行政运行", "total_actual", "10.00"),
                    ("2010102", "一般行政管理事务", "total_actual", "20.00"),
                ]
            else:
                text(page, 150, 245, "合计")
                for x, v in [(304, "650.58"), (404, "374.00"), (504, "276.58")]:
                    text(page, x, 245, v)
                expected = [("total", "合计", "total_actual", "650.58")]
        else:
            text(page, 40, 60, "财政拨款收入支出决算总表")
            xs, ys = [40, 150, 230, 420, 510, 650, 800, 960], [100, 120, 140, 180, 220]
            for y in ys:
                page.draw_line((40, y), (960, y))
            for x in xs:
                page.draw_line((x, 100 if x in (40, 230, 960) else 120), (x, 250))
            text(page, 44, 115, "收入")
            text(page, 234, 115, "支出决算数")
            headers = [
                "项目",
                "决算数",
                "项目",
                "合计",
                "一般公共预算财政拨款",
                "政府性基金预算财政拨款",
                "国有资本经营预算财政拨款",
            ]
            for i, v in enumerate(headers):
                text(page, xs[i] + 3, 135, v, 7)
            for y, label in [
                (165, "一、一般公共服务支出"),
                (205, "二、外交支出"),
                (240, "三、教育支出"),
            ]:
                text(page, 234, y, label)
                for x in xs[3:-1]:
                    text(page, x + 4, y, "0.00")
            expected = [(None, "三、教育支出", "expenditure_actual", "0.00")]
        doc.save(path)
    return path, expected


def test_public_pdf_pipeline_recovers_irregular_native_table(irregular_pdf):
    path, expected = irregular_pdf
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert context
    for code, name, measure, amount in expected:
        matched = [
            f
            for f in context.definite_facts()
            if f.subject_code == code
            and "".join((f.subject_name or "").split()) == name
            and f.indicator == measure
        ]
        assert len(matched) == 1 and matched[0].raw_value == amount
    assert not assess_table_reading(artifact)["gaps"]
