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
