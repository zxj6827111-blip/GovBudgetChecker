"""benchmark 与生产路径的 finding 等价性守护（WP4-I 度量可信度的前提）。

S4 的精度指标全部取自 ``run_benchmark``，而用户在生产里看到的是
``build_issues_payload`` 的 ``issues`` 分桶。两者只有 finding 集合**一致**，
基准测出来的精度才代表生产行为——一旦有人在 ``build_issues_payload`` 里
加去重/过滤/精选，基准数字就会静默地与生产脱钩（基准变好或变坏都与用户
无关），且没有任何现有测试会察觉。

实测等价面（本测试固化）：
- finding 条数一致、规则多重集一致（``build_issues_payload`` 只做 dict 化
  与 severity 归一，不过滤不去重）；
- severity 经 ``_norm_sev`` 归一为 error/warn/info 三桶。

已知且**故意保留**的差异（不在本测试断言范围内）：
- MR-3 的文档级可读性闸门在 ``api/main.py`` 里于 ``build_issues_payload``
  **之后**施加，因此 benchmark 路径不含闸门——scan-watch 材料在基准里会
  显示闸门前的条目（scan-watch 不进主指标，见 delta 文档 §七）。
"""

from __future__ import annotations

import json

from collections import Counter
from pathlib import Path

from src.engine import pipeline as engine_pipeline
from src.engine.pipeline import build_document, build_issues_payload, run_rules_with_outcomes

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_page_data.json"


def _load_doc():
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    # 深拷贝表格：引擎会就地表对象，共享会污染 fixture 的嵌套结构
    return build_document(
        path="DOC-20260905-001.pdf",
        page_texts=fixture["page_texts"],
        page_tables=json.loads(json.dumps(fixture["page_tables"])),
        filesize=0,
    )


def test_benchmark_and_production_yield_same_findings() -> None:
    raw, _outcomes = run_rules_with_outcomes(_load_doc(), use_ai_assist=False)
    payload = build_issues_payload(_load_doc(), use_ai_assist=False)
    items = payload["issues"]["all"]

    raw_rules = Counter(str(getattr(issue, "rule", "")) for issue in raw)
    payload_rules = Counter(item["rule"] for item in items)
    assert payload_rules == raw_rules, (
        "build_issues_payload 与 run_rules_with_outcomes 的 finding 集合不一致——"
        "基准（run_benchmark 走上游函数）与生产（走 payload）已经脱钩，"
        "S4 精度指标不再代表用户所见。"
    )
    assert len(items) == len(raw)


def test_payload_severity_is_normalized_into_three_buckets() -> None:
    raw, _outcomes = run_rules_with_outcomes(_load_doc(), use_ai_assist=False)
    payload = build_issues_payload(_load_doc(), use_ai_assist=False)
    items = payload["issues"]["all"]

    for item in items:
        assert item["severity"] in {"error", "warn", "info"}
    # 桶内容与 all 完全一致（分桶不是二次筛选）
    bucket_total = sum(len(payload["issues"][name]) for name in ("error", "warn", "info"))
    assert bucket_total == len(items)

    # 归一映射逐条对得上（raw severity → 三桶），且分桶按归一后的值落位
    expected = Counter(
        engine_pipeline._norm_sev(str(getattr(issue, "severity", ""))) for issue in raw
    )
    assert Counter(item["severity"] for item in items) == expected


def test_rule_execution_summary_survives_payload_conversion() -> None:
    """未执行事实不得在 payload 转换中丢失（fail-closed 依赖它）。

    契约（src/engine/rule_outcome.summarize_rule_outcomes）：executed =
    pass+fail+not_applicable；unresolved_total = insufficient+parse+execution
    且等于 unresolved_rules 的条数。
    """
    payload = build_issues_payload(_load_doc(), use_ai_assist=False)
    summary = payload["rule_execution_summary"]
    assert summary["executed"] == (
        summary["pass"] + summary["fail"] + summary["not_applicable"]
    )
    assert summary["unresolved_total"] == (
        summary["insufficient_data"] + summary["parse_error"] + summary["execution_error"]
    )
    assert summary["unresolved_total"] == len(summary["unresolved_rules"])
    assert summary["total_rules"] >= summary["executed"]
    # golden 样张的既有事实：确实存在未执行规则（否则这条断言无意义）
    assert summary["unresolved_total"] > 0
