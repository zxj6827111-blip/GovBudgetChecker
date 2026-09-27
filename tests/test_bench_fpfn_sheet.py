"""bench_fpfn_sheet 单元测试（WP4-I S1 工具 4）。

覆盖：FP/FN 行收集（含 truth_group 与归因空列）、CSV 落盘（utf-8-sig
便于 Excel 直开）、FP/FN 计数输出。
"""

from __future__ import annotations

import csv

from scripts.bench_fpfn_sheet import (
    CSV_FIELDS,
    collect_from_eval_reports,
    write_sheet,
)


def _sample_reports():
    return {
        "DOC-B1-001": {
            "false_positive_details": [
                {"rule": "V33-202", "page": 15, "message": "T4↔T5一般公共支出不一致：T4=24535.67"},
                {"rule": "CMM-004", "page": 3, "message": "连续句号"},
            ],
            "missed": [
                {
                    "truth_group": "T7",
                    "rule_id": "V33-227",
                    "page": 18,
                    "evidence": "说明5类级科目名称与表不一致",
                }
            ],
        },
        "DOC-B1-002": {
            "false_positive_details": [],
            "missed": [
                {"truth_group": "T2", "rule_id": "", "page": 7, "evidence": "收入决算表漏检"}
            ],
        },
    }


def test_collect_rows_shape_and_order():
    rows = collect_from_eval_reports(_sample_reports())
    assert len(rows) == 4  # 2 FP + 2 FN
    fp_rows = [r for r in rows if r["row_type"] == "FP"]
    fn_rows = [r for r in rows if r["row_type"] == "FN"]
    assert len(fp_rows) == 2 and len(fn_rows) == 2
    # FP 行：规则/页码/摘要来自 false_positive_details，归因列留空
    assert fp_rows[0]["doc_id"] == "DOC-B1-001"
    assert fp_rows[0]["rule"] == "V33-202"
    assert fp_rows[0]["page"] == 15
    assert "24535.67" in fp_rows[0]["detail"]
    assert fp_rows[0]["归因"] == "" and fp_rows[0]["备注"] == ""
    # FN 行：truth_group 必须带上（真值组归因的单位）
    assert fn_rows[0]["truth_group"] == "T7"
    assert fn_rows[0]["rule"] == "V33-227"
    assert fn_rows[1]["truth_group"] == "T2"


def test_write_sheet_csv_roundtrip(tmp_path):
    rows = collect_from_eval_reports(_sample_reports())
    out = tmp_path / "sheet" / "fpfn.csv"
    written = write_sheet(rows, out)
    assert written == out
    with out.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CSV_FIELDS
        back = list(reader)
    assert len(back) == 4
    assert {r["row_type"] for r in back} == {"FP", "FN"}
    # 导出契约：归因/备注列存在且留空（人工回填后由 eval_benchmark 重算消费）
    assert all(r["归因"] == "" and r["备注"] == "" for r in back)
