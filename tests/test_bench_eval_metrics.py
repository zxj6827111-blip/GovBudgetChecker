"""eval_benchmark 聚合指标反例（WP4-I S1 工具 3）。

aggregate() 是纯函数：直接构造逐份评测报告（evaluate() 的返回形状），
验证 §4.2/§4.3 指标口径——TP/FP/FN 合计、FPR=1−P、FP 密度、规则级
precision、零触发清单、文种识别准确率（含错判反例）、六态合计、
义务组完成率聚合与分层下钻。
"""

from __future__ import annotations

import copy

from scripts.eval_benchmark import aggregate


def _eval_report(tp_rules, fp_rules, *, fn=1, hint_groups_hit=1, hint_groups_total=2):
    """构造 evaluate() 返回形状的最小报告。"""
    rule_counts = {}
    for rule in tp_rules:
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
    for rule in fp_rules:
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
    return {
        "tp": len(tp_rules),
        "fp": len(fp_rules),
        "fn": fn,
        "hint_groups_hit": hint_groups_hit,
        "hint_groups_total": hint_groups_total,
        "rule_counts": rule_counts,
        "matched": [{"matched_rule": rule} for rule in tp_rules],
        "hint_hits": [],
        "false_positive_details": [{"rule": rule, "page": 1, "message": "m"} for rule in fp_rules],
        "acceptable_violations": [],
    }


def _entry(doc_id, *, manifest, resolved, summary, ledger_groups, eval_report=None,
           rule_counts=None):
    """逐份重放条目。

    ``rule_counts`` 是**无标注依赖**的触发观测（真实重放里等于该份 findings
    的规则计数，已标注与未标注材料都有值）；不显式给时按 eval_report 的
    规则计数回填，与真实产物形状一致。
    """
    if rule_counts is None:
        rule_counts = dict((eval_report or {}).get("rule_counts") or {})
    entry = {
        "doc_id": doc_id,
        "manifest": manifest,
        "replay_meta": {
            "report_kind_resolved": resolved,
            "rule_execution_summary": summary,
            "obligation_ledger": {"by_group": ledger_groups},
            "rule_counts": rule_counts,
        },
    }
    if eval_report is not None:
        entry["eval_report"] = eval_report
        entry["golden_path"] = ""
    return entry


REGISTRY = {
    "final": ["V33-101", "V33-102", "V33-201", "V33-227"],
    "budget": ["BUD-105"],
    "common": ["CMM-001", "CMM-002"],
}


def test_aggregate_core_metrics():
    entries = [
        _entry(
            "DOC-B1-001",
            manifest={"doc_id": "DOC-B1-001", "report_kind_true": "final",
                      "subset": "final-main", "region": "anchor"},
            resolved="final",
            summary={"pass": 40, "fail": 3, "not_applicable": 5,
                     "insufficient_data": 6, "parse_error": 0, "execution_error": 0},
            ledger_groups=[
                {"group_id": "TABLE_CROSS", "applicable": 4, "completed": 2, "unresolved": 2},
                {"group_id": "SAN_GONG", "applicable": 3, "completed": 3, "unresolved": 0},
            ],
            eval_report=_eval_report(["V33-101", "V33-102"], ["BUD-105"]),
        ),
        _entry(
            "DOC-B1-002",
            manifest={"doc_id": "DOC-B1-002", "report_kind_true": "final",
                      "subset": "clean-contrast", "region": "anchor"},
            resolved="final",
            summary={"pass": 50, "fail": 0, "not_applicable": 4,
                     "insufficient_data": 2, "parse_error": 0, "execution_error": 0},
            ledger_groups=[
                {"group_id": "TABLE_CROSS", "applicable": 4, "completed": 4, "unresolved": 0},
            ],
            eval_report=_eval_report(["V33-201"], [], hint_groups_hit=0, hint_groups_total=1),
        ),
        # 未标注材料：进 docs_total，不进任何指标分母
        _entry(
            "DOC-B1-003",
            manifest={"doc_id": "DOC-B1-003", "report_kind_true": "budget",
                      "subset": "budget-main", "region": "probe"},
            resolved="budget",
            summary={"pass": 10, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[],
        ),
    ]

    report = aggregate(entries, REGISTRY)
    totals = report["totals"]

    assert report["docs_total"] == 3
    assert report["docs_evaluated"] == 2
    assert report["docs_unannotated"] == ["DOC-B1-003"]

    # TP=3, FP=1, FN=2（每份已评测材料各 1 个 FN）
    assert totals["tp"] == 3 and totals["fp"] == 1 and totals["fn"] == 2
    assert totals["precision"] == round(3 / 4, 4)
    assert totals["recall"] == round(3 / 5, 4)
    assert totals["fpr_operational"] == round(1 - 3 / 4, 4)  # FPR = 1 − P
    assert totals["fp_density"] == 0.5  # 1 条 FP / 2 份已评测材料
    assert totals["hint_groups_total"] == 3

    # 规则级 precision：BUD-105 只有 FP → 0.0；V33-201 只有 TP → 1.0
    assert report["rule_level"]["BUD-105"]["precision"] == 0.0
    assert report["rule_level"]["V33-201"]["precision"] == 1.0
    assert report["rule_level"]["V33-101"]["tp"] == 1

    # 零触发：final 域的 V33-227 从未触发；budget 域唯一规则 BUD-105 已作为
    # FP 触发（触发≠正确），不再进零触发清单
    assert "V33-227" in report["zero_trigger_rules"]["final"]
    assert "V33-101" not in report["zero_trigger_rules"]["final"]
    assert report["zero_trigger_rules"]["budget"] == []

    # 文种识别：3 份全对 → 1.0
    assert report["kind_accuracy"]["rate"] == 1.0

    # 六态合计（含未标注材料的重放摘要）
    assert report["status_distribution"]["insufficient_data"] == 8
    assert report["status_distribution"]["execution_error"] == 0

    # 义务组聚合：TABLE_CROSS (4+4, 2+4) → 完成率 6/8
    table_cross = report["obligation_ledger"]["by_group"]["TABLE_CROSS"]
    assert table_cross["completion_rate"] == round(6 / 8, 4)
    assert report["obligation_ledger"]["completion_rate"] == round(
        (2 + 3 + 4) / (4 + 3 + 4), 4
    )

    # 分层下钻：subset 各自独立计 P/R
    by_subset = report["stratified"]["by_subset"]
    assert by_subset["final-main"]["tp"] == 2
    assert by_subset["clean-contrast"]["tp"] == 1


def test_aggregate_kind_mismatch_is_counted():
    """文种错判直接换掉整套专项规则——必须逐份点名。"""
    entries = [
        _entry(
            "DOC-B1-009",
            manifest={"doc_id": "DOC-B1-009", "report_kind_true": "budget",
                      "subset": "budget-main", "region": "anchor"},
            resolved="final",  # 错判
            summary={"pass": 1, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[],
            eval_report=_eval_report(["V33-101"], []),
        ),
    ]
    report = aggregate(entries, REGISTRY)
    assert report["kind_accuracy"]["rate"] == 0.0
    assert report["kind_accuracy"]["mismatches"] == [
        {"doc_id": "DOC-B1-009", "resolved": "final", "true": "budget"}
    ]


def test_aggregate_empty_denominators_return_none():
    """0 分母不是 100%：precision/recall/完成率在无数据时必须为 None。"""
    entries = [
        _entry(
            "DOC-B1-000",
            manifest={"doc_id": "DOC-B1-000", "report_kind_true": "",
                      "subset": "", "region": ""},
            resolved="",
            summary={"pass": 0, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[],
            eval_report=_eval_report([], [], fn=0),
        ),
    ]
    report = aggregate(entries, REGISTRY)
    assert report["totals"]["precision"] is None
    assert report["totals"]["recall"] is None
    assert report["totals"]["fpr_operational"] is None
    assert report["obligation_ledger"]["completion_rate"] is None
    # 该材料的文种真值为空 → 不进文种分母；无适用材料 → 不产出零触发键
    assert report["kind_accuracy"]["total"] == 0
    assert "final" not in report["zero_trigger_rules"]


def test_aggregate_does_not_mutate_entries():
    entries = [
        _entry(
            "DOC-B1-001",
            manifest={"doc_id": "DOC-B1-001", "report_kind_true": "final",
                      "subset": "final-main", "region": "anchor"},
            resolved="final",
            summary={"pass": 1, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[{"group_id": "SAN_GONG", "applicable": 2, "completed": 1, "unresolved": 1}],
            eval_report=_eval_report(["V33-101"], []),
        )
    ]
    snapshot = copy.deepcopy(entries)
    aggregate(entries, REGISTRY)
    assert entries == snapshot


def test_zero_trigger_scope_covers_unannotated_replay():
    """零触发清单必须看**全部重放材料**，不能只看已标注面。

    反例来由（真缺陷）：S1 首版用 rule_level（只由已标注材料构建）算零触发，
    7 份材料只标注了 1 份时，另外 6 份上的触发被全部记成"零触发"
    （final 域 52 条），与本节"重放 findings 里从未出现"的语义相反。
    """
    entries = [
        # 已标注：只触发 V33-101
        _entry(
            "DOC-B1-001",
            manifest={"doc_id": "DOC-B1-001", "report_kind_true": "final",
                      "subset": "final-main", "region": "anchor"},
            resolved="final",
            summary={"pass": 1, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[],
            eval_report=_eval_report(["V33-101"], []),
        ),
        # 未标注：重放里触发了 V33-201（无 eval_report，只有触发观测）
        _entry(
            "DOC-B1-002",
            manifest={"doc_id": "DOC-B1-002", "report_kind_true": "final",
                      "subset": "final-main", "region": "anchor"},
            resolved="final",
            summary={"pass": 1, "fail": 0, "not_applicable": 0,
                     "insufficient_data": 0, "parse_error": 0, "execution_error": 0},
            ledger_groups=[],
            rule_counts={"V33-201": 2},
        ),
    ]
    report = aggregate(entries, REGISTRY)

    # V33-201 只在未标注材料上触发——不得进零触发清单
    assert "V33-201" not in report["zero_trigger_rules"]["final"]
    # V33-102 / V33-227 全量重放里从未触发——仍在清单里
    assert "V33-102" in report["zero_trigger_rules"]["final"]
    assert "V33-227" in report["zero_trigger_rules"]["final"]
    # 触发观测是标签无关面：全量计数如实导出
    assert report["rule_trigger_counts"]["V33-201"] == 2
    assert report["rule_trigger_counts"]["V33-101"] == 1
    assert report["zero_trigger_scope"] == "全部重放材料（无标注依赖）"
