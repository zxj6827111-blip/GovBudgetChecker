"""MR-P 批次（2026-09-30 市级探针裁决整改）验收测试。

事实源：docs/B1_PROBE14_FPFN_ADJUDICATION_20260930.md（commit 3062bcd）——
14 份上海市级决算 87 条高严重度 findings 人工裁决：TP 10 / 舍入过判 9 /
FP 68，漏报实证 2 类。本文件是 MR-P1~P6 六个批次的机检钉线：

- 每批次一组**合成正例/反例**单元测试（修复的逻辑行为本身）；
- **TP 保留钉线**：把裁决的 10 条 TP 写成机检断言
  （键 = 规则 × 材料 × 关键数字，容差沿用 classify_amount_diff）——
  防止「修没真阳性」。语料 PDF 不入 git（bench fail-closed 纪律），
  PDF 缺失的干净 checkout 上显式 skip，探针重放（run_benchmark --doc）
  是 TP 的主验收面；
- FP 清零的逐批验收走 docs/baselines/bench_probe14_metrics_*.json 快照对比。

红线（MR-P 方案 §〇）：不删义务凑绿；FP 不得改装成 unresolved 充数；
10 条 TP 一条不许修丢。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    R33200_InterTable_T1_T2,
    R33201_InterTable_T1_T3,
    R33TrendCompletionRate,
    _two_sided_side_value,
    build_document,
)

# ---------------------------------------------------------------------------
# 工具：合成文档 / 语料文档装载（PDF 缺失 → skip，不静默放空）
# ---------------------------------------------------------------------------


def _doc_from_pages(
    *pages: str, path: str = "contract-mrp.pdf"
) -> Any:
    """合成 final 文档：页 1 提供年度锚，其后每参量一页。"""
    return build_document(
        path=path,
        page_texts=["某某局 2025 年度部门决算公开", *pages],
        page_tables=[[]],
        filesize=0,
    )


def _load_corpus_doc(doc_id: str) -> Any:
    """装载已登记语料 PDF（本机已登记的 14 份探针观察面）。

    语料 PDF 不入 git：干净 checkout 上 skip（探针重放是主验收面，
    不在无源环境伪装通过）。
    """
    pdf = ROOT / "corpus" / doc_id / "sample.pdf"
    if not pdf.exists():
        pytest.skip(f"语料 PDF 不在本机（干净 checkout）：{doc_id}")
    from scripts.replay_golden_corpus import load_page_tables, load_page_texts

    return build_document(
        str(pdf),
        load_page_texts(pdf),
        load_page_tables(pdf),
        1000,
    )


def _doc_with_table(
    table_name: str, rows: List[List[str]], extra_pages: Optional[Dict[int, List[List[List[str]]]]] = None
) -> Any:
    """构造可锚定的单表文档：页 2 文本含表名（锚点），页表数据挂对应页。"""
    page_tables: List[List[List[List[str]]]] = [[], [rows]]
    if extra_pages:
        for pg, tbls in extra_pages.items():
            while len(page_tables) < pg:
                page_tables.append([])
            page_tables[pg - 1] = tbls
    return build_document(
        path="contract-mrp-table.pdf",
        page_texts=["某某局 2025 年度部门决算公开", table_name],
        page_tables=page_tables,
        filesize=0,
    )


# ---------------------------------------------------------------------------
# MR-P1：V33-200/201 总表收支列身份 fail-closed
# ---------------------------------------------------------------------------

#: 双栏总表标准形态（上海市版式）：表头两行 + 收支行 + 支出行
_TWO_SIDED_T1 = [
    ["收入", "", "支出", ""],
    ["项目", "决算数", "项目", "决算数"],
    ["一、一般公共预算财政拨款收入", "2,171,700.66", "一、一般公共服务支出", "1,982.47"],
    ["本年收入合计", "2,418,329.38", "本年支出合计", "2,443,579.41"],
    ["年初结转和结余", "48,688.30", "年末结转和结余", "23,409.06"],
    ["总计", "2,468,832.94", "总计", "2,468,832.94"],
]


def test_p1_two_sided_value_takes_label_side():
    """双栏行按标签所在半行取数：收入侧 2,418,329.38、支出侧 2,443,579.41
    （探针 009 p8 实证数）。旧 max(vals) 在支出>收入时把支出侧当收入。"""
    rows = _TWO_SIDED_T1
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 2418329.38
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 2443579.41


def test_p1_two_sided_value_single_sided_row_unaffected():
    """单侧布局（标签+单值）行为不变：取标签后第一个数值。"""
    rows = [
        ["项目", "决算数"],
        ["本年收入合计", "100.00"],
    ]
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 100.0


def test_p1_two_sided_value_fails_closed_when_label_side_empty():
    """标签所在半行无数值 → None（fail-closed），不得取另一侧的数凑数。"""
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "", "本年支出合计", "200.00"],
    ]
    assert _two_sided_side_value(rows, "本年收入合计", "income") is None
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 200.0


def test_p1_two_sided_value_header_side_conflict_fails_closed():
    """表头侧身份与标签所在半行冲突 → None（列身份校验，禁止混猜）。"""
    # 表头声明收入在左、支出在右；标签「本年支出合计」却出现在左半行
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年支出合计", "2,443,579.41", "", ""],
    ]
    assert _two_sided_side_value(rows, "本年支出合计", "expense") is None


def test_p1_v33_200_reports_real_income_mismatch():
    """正例保留：T1 收入合计与 T2 真实不一致仍报 error（不因修复变哑）。

    T2 收入表合计行取 2,300,000.00 ≠ T1 收入侧 2,418,329.38。修复前该形态
    也会报（max 恰好取对侧更大数），修复后按列身份取数仍必须报——
    防止「fail-closed 把真差异也吞掉」。"""
    t1 = [row[:] for row in _TWO_SIDED_T1]
    t2 = [
        ["项目", "本年收入合计", "财政拨款收入", "其他收入"],
        ["功能分类科目编码", "科目名称", "", ""],
        ["合计", "", "2,300,000.00", "2,300,000.00"],
    ]
    doc = build_document(
        path="p1-real-mismatch.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "一、收入支出决算总表",
            "二、收入决算表",
        ],
        page_tables=[[], [t1], [t2]],
        filesize=0,
    )
    issues = R33200_InterTable_T1_T2().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "T1=2418329.38" in issues[0].message
    assert "T2=2300000.00" in issues[0].message


def test_p1_v33_201_reports_real_expense_mismatch():
    """正例保留：T1 支出侧与 T3 真实不一致仍报 error。"""
    t1 = [row[:] for row in _TWO_SIDED_T1]
    t3 = [
        ["项目", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", ""],
        ["合计", "", "2,000,000.00", "2,000,000.00"],
    ]
    doc = build_document(
        path="p1-real-expense-mismatch.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "一、收入支出决算总表",
            "三、支出决算表",
        ],
        page_tables=[[], [t1], [t3]],
        filesize=0,
    )
    issues = R33201_InterTable_T1_T3().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "T1=2443579.41" in issues[0].message


def test_p1_v33_201_side_value_on_probe_shape():
    """019 形态：支出侧取 130,442.55（旧实现取收入侧 195,989.75 报假不一致）。"""
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "195,989.75", "本年支出合计", "130,442.55"],
        ["年初结转和结余", "45,830.18", "年末结转和结余", "110,757.63"],
        ["总计", "241,860.77", "总计", "241,860.77"],
    ]
    assert _two_sided_side_value(rows, "本年支出合计", "expense") == 130442.55
    assert _two_sided_side_value(rows, "本年收入合计", "income") == 195989.75


# ---------------------------------------------------------------------------
# TP 保留钉线：10 条真阳性（键 = 规则 × 材料 × 关键数字）
# ---------------------------------------------------------------------------
