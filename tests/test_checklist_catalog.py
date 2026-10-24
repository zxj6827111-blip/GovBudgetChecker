"""检查点清单目录（rules/checklists/）的映射完整性。

清单（2026-10-22 起全系统默认检查指标）每一条都必须映射到存在的检查义务与已注册规则；
external 条目说明为何不属 PDF 内容规则。清单条目增删或规则改名会让这里失败。
"""

from __future__ import annotations

import pytest

from src.engine.check_obligations import OBLIGATION_CATALOG, obligation_ids_for_rule
from src.engine.checklist_catalog import (
    CHECKLIST_DECISIONS,
    CHECKLIST_FILES,
    checklist_coverage,
    checklist_items_for_rule,
    load_checklist,
    validate_checklist,
)
from src.engine.rule_registry import all_rule_ids

#: 清单原文 54 条（预算、决算各 A~F 六个维度）。
EXPECTED_ITEMS = 54


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_checklist_self_check_passes(kind: str) -> None:
    assert validate_checklist(kind) == []


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_checklist_covers_every_source_item(kind: str) -> None:
    document = load_checklist(kind)
    ids = [item["id"] for item in document["items"]]
    assert len(ids) == EXPECTED_ITEMS
    assert document["scope"] == "global", "清单作为全系统检查指标，不得按地区单列"
    for prefix, count in (("A", 12), ("B", 10), ("C", 16), ("D", 8), ("E", 6), ("F", 2)):
        assert sum(1 for cid in ids if cid.startswith(prefix)) == count, prefix


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_external_items_are_only_intake_and_publication_timing(kind: str) -> None:
    external = {item["id"] for item in load_checklist(kind)["items"] if item["status"] == "external"}
    assert external == {"E-03", "F-01", "F-02"}


def _decisions(kind: str) -> dict[str, str]:
    return {
        item["id"]: item["decision"]
        for item in load_checklist(kind)["items"]
        if item.get("decision")
    }


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_every_non_implemented_item_carries_a_decision(kind: str) -> None:
    """2026-10-22 决议：清单里不留“还没想好”的条目，每条未完全覆盖的都有明确处置。"""
    for item in load_checklist(kind)["items"]:
        if item["status"] == "implemented":
            assert not item.get("decision"), item["id"]
        else:
            assert item.get("decision") in CHECKLIST_DECISIONS, item["id"]


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_publication_timing_and_format_are_not_checked_by_rules(kind: str) -> None:
    """公开时限在下载环节核查、文件格式在上传入口限定，规则库不检查（决议 2026-10-22）。"""
    items = {item["id"]: item for item in load_checklist(kind)["items"]}
    for cid in ("E-03", "F-01", "F-02"):
        assert items[cid]["status"] == "external" and items[cid]["decision"] == "closed"
        assert not items[cid].get("rules") and not items[cid].get("obligations")
    for cid in ("F-01", "F-02"):
        assert "下载环节" in items[cid]["note"]


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_only_subject_dictionary_is_deferred(kind: str) -> None:
    """科目字典待取得权威科目表后再建，是唯一的暂缓项。"""
    deferred = {cid for cid, decision in _decisions(kind).items() if decision == "deferred"}
    assert deferred == {"B-04"}


@pytest.mark.parametrize("kind", sorted(CHECKLIST_FILES))
def test_typo_and_sentence_items_are_ai_only(kind: str) -> None:
    """错别字、病句：词表只作提示，进一步判断只交大模型 AI。"""
    ai_only = {cid for cid, decision in _decisions(kind).items() if decision == "ai_only"}
    expected = {"D-01", "D-03", "D-08"} if kind == "budget" else {"D-01", "D-08"}
    assert ai_only == expected


def test_every_checklist_rule_is_tied_to_an_obligation() -> None:
    for kind in CHECKLIST_FILES:
        for item in load_checklist(kind)["items"]:
            for rule_id in item.get("rules") or []:
                if rule_id in {"BUD-103", "V33-110", "V33-115", "V33-120"}:
                    continue  # 既有规则，账本经同条目的其他义务覆盖
                assert obligation_ids_for_rule(rule_id), (kind, item["id"], rule_id)


#: 2026-10-22 为清单新增的规则编号。
NEW_CHECKLIST_RULES = (
    *(f"CMM-{n:03d}" for n in range(8, 27)),
    *(f"BUD-{n}" for n in range(116, 124)),
    "V33-251", "V33-252",
)


def test_every_new_checklist_rule_is_registered_and_listed() -> None:
    registered = set(all_rule_ids())
    for rule_id in NEW_CHECKLIST_RULES:
        assert rule_id in registered, rule_id
        assert checklist_items_for_rule(rule_id), rule_id
        assert obligation_ids_for_rule(rule_id), rule_id


def test_coverage_counts_add_up() -> None:
    for kind in CHECKLIST_FILES:
        coverage = checklist_coverage(kind)
        assert sum(coverage["by_status"].values()) == coverage["total"] == EXPECTED_ITEMS
        non_implemented = coverage["total"] - coverage["by_status"]["implemented"]
        assert sum(coverage["by_decision"].values()) == non_implemented


def test_new_obligations_are_registered_for_both_kinds() -> None:
    by_id = {item.obligation_id: item for item in OBLIGATION_CATALOG}
    for obligation_id in ("OBL-STRUCT-SECTIONS", "OBL-STRUCT-TOC-ALIGN", "OBL-TEXT-AMOUNT-FORMAT",
                          "OBL-TEXT-AMOUNT-UNIT", "OBL-TEXT-YEAR", "OBL-STRUCT-ORG-NAME"):
        assert set(by_id[obligation_id].report_kinds) == {"budget", "final"}
