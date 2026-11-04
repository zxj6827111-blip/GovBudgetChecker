#!/usr/bin/env python3
"""114 目标统一重评：按当前运行逐项、逐出现点核对。

- FP 目标：在 S1 冻结基线中找到原条目，再到当前运行里按规则+页码+原文
  复核是否仍以正式问题存在；原序号位移不算新问题。
- MISS 目标：用金标单位的原文出现点（有阶段契约缺陷词的按契约收窄）逐一
  核对当前 F 层且证据合格的条目；全部出现点覆盖才算 satisfied。
- 只报告当前状态与缺口，不用阶段契约放行，也不按目标编号写例外。

输出：
    target_ledger.csv        114 行逐项状态
    target_ledger.json       含覆盖明细与分类
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
EVIDENCE = Path("/Volumes/workSSD/software development/gbc-worktrees/joint-execution-20261103-evidence")
TOOLS = Path("/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103/scripts")
DEFAULT_RUN = EVIDENCE / "s4b/gold/final"
BASELINE_RUN = EVIDENCE / "s1_integration/gold/run1"
TARGET_MATRIX = REPO / "docs/joint_execution_20261103/target_matrix.csv"
HISTORICAL_STATUS = EVIDENCE / "target_status.csv"
STRICT_STATUS = EVIDENCE / "rework/target_status_strict.csv"
CONTRACT_FILES = (
    EVIDENCE / "s5/S5_contract.json",
    EVIDENCE / "s4/S4_contract.json",
    EVIDENCE / "s4b/contract_n05_all.json",
    EVIDENCE / "s4b/contract_residual.json",
)
OCCURRENCE_CONTRACT = Path(__file__).with_name("occurrence_contract_20261104.json")
STRICT_ROLES = Path(__file__).with_name("strict_roles_20261104.json")
KNOWN_PENDING = {
    "FP|GS-2022-03|V33-202-16",
    "FP|GS-2024-02|V33-202-9",
    "MISS|GS-2022-06|C|GS-N05|1",
    "MISS|GS-2023-05|C|GS-N05|2",
    "MISS|GS-2024-08|C|GS-N05|2",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_gz(path: Path):
    import gzip

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def enrich_evidence(items: List[Dict[str, Any]], raw_index) -> None:
    """冻结匹配器条目行不带证据字段，从原始 status 补齐。"""
    eval_lib = load_module("eval_lib", TOOLS / "gold_baseline_eval_fp_batch1.py")
    for item in items:
        raw = eval_lib.raw_item(raw_index, item) or {}
        item["_raw"] = raw
        if not str(item.get("evidence_status") or "").strip():
            item["evidence_status"] = raw.get("evidence_status")
        if not item.get("evidence_verification"):
            item["evidence_verification"] = raw.get("evidence_verification")
        if not item.get("location") and isinstance(raw.get("location"), dict):
            item["location"] = raw["location"]
        if not item.get("evidence") and isinstance(raw.get("evidence"), list):
            item["evidence"] = raw["evidence"]


def evidence_ok(item: Dict[str, Any]) -> bool:
    if str(item.get("evidence_status") or "").strip() != "complete":
        return False
    raw = item.get("evidence_verification") or {}
    return not (isinstance(raw, dict) and str(raw.get("status") or "") == "failed")


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _canonical_amount(value: Any) -> str:
    """金额数值规范化：100.00 / 100.000 / 1e2 都是 100。"""
    text = str(value).replace(",", "").replace("，", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    return format(number.normalize(), "f")


def _identity(item: Dict[str, Any]) -> Dict[str, Any]:
    """实体身份：关系、事实/单元格、操作数、证据引文。

    相似度只用于检索候选；这里用可核验的身份字段判断是否仍是同一实体。
    金额同时保留原文与规范化值：显示精度变化（100.00/100.000）不能证明
    实体不同，但“纳入12项”这类小整数计数也不应当金额身份。
    """
    raw = item.get("_raw") or {}
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    relations, cells = set(), set()
    for source in (raw, location):
        for key in ("check_relation_id", "relation_id"):
            value = source.get(key)
            if value:
                relations.add(str(value))
    for evidence in item.get("evidence") or raw.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        for key in ("relation_id", "operand_id", "fact_uid", "source_cell_id"):
            if evidence.get(key):
                cells.add(f"{key}:{evidence[key]}")
    raw_amounts = set(_amount_tokens(item.get("message")))
    return {
        "relations": relations,
        "cells": cells,
        "raw_amounts": raw_amounts,
        "amounts": {_canonical_amount(value) for value in raw_amounts},
        "quotes": {norm(q) for q in item.get("quotes") or [] if norm(q)},
        "pages": set(cmp_pages(item)),
    }


def cmp_pages(item: Dict[str, Any]) -> List[int]:
    pages = set()
    for value in item.get("pages") or []:
        if isinstance(value, int):
            pages.add(value)
    for ref in (item.get("location") or {}).get("table_refs") or []:
        if isinstance(ref, dict) and isinstance(ref.get("page"), int):
            pages.add(ref["page"])
    for evidence in item.get("evidence") or []:
        if isinstance(evidence, dict) and isinstance(evidence.get("page"), int):
            pages.add(evidence["page"])
    return sorted(pages)


def _significant_amounts(values) -> set:
    """金额量级 token；排除“纳入12项”“1处”这类小整数计数。

    返回值已规范化：100.00 与 100.000 都是 100，显示精度变化不能证明
    实体不同；小整数计数（12/1）仍被排除。
    """
    result = set()
    for value in values:
        text = str(value)
        try:
            number = Decimal(text.replace(",", "").replace("，", ""))
        except (InvalidOperation, ValueError):
            continue
        if "." in text or abs(number) >= 1000 or abs(number) < 1:
            result.add(_canonical_amount(text))
    return result


def _same_entity(baseline: Dict[str, Any], candidate: Dict[str, Any]):
    """True=同一实体；False=可证明不同；None=身份不足，保留未完成。"""
    left, right = _identity(baseline), _identity(candidate)
    if left["relations"] and right["relations"]:
        return bool(left["relations"] & right["relations"])
    if left["cells"] and right["cells"]:
        return bool(left["cells"] & right["cells"])
    baseline_amounts = _significant_amounts(left["raw_amounts"])
    if baseline_amounts and right["amounts"]:
        overlap = baseline_amounts & right["amounts"]
        if not overlap:
            return False
        largest = max(baseline_amounts, key=lambda value: abs(Decimal(value)))
        return largest in overlap or len(overlap) * 2 >= len(baseline_amounts)
    if left["quotes"] and right["quotes"]:
        return bool(left["quotes"] & right["quotes"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=str(DEFAULT_RUN))
    parser.add_argument("--baseline-run", default=str(BASELINE_RUN))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    cmp = load_module("cmp", TOOLS / "batch2_gold_compare.py")
    eval_lib = load_module("eval_lib2", TOOLS / "gold_baseline_eval_fp_batch1.py")
    gm = load_module("gm", REPO / "scripts/gold_baseline_match.py")
    docs = gm.load_gold()

    occurrence_contract = json.loads(OCCURRENCE_CONTRACT.read_text(encoding="utf-8")) \
        if OCCURRENCE_CONTRACT.exists() else {"check_rules": {}, "control_adjudications": []}
    check_rules = occurrence_contract.get("check_rules") or {}
    controls = occurrence_contract.get("control_adjudications") or []
    strict_roles = {}
    if STRICT_ROLES.exists():
        strict_roles = (json.loads(STRICT_ROLES.read_text(encoding="utf-8"))
                        .get("targets") or {})
    needles: Dict[str, List[str]] = {}
    for path in CONTRACT_FILES:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (payload.get("target_message_contains") or {}).items():
                needles.setdefault(key, list(value))
    # 收窄词可能随小批收敛；优先使用最窄（元素最多）的一份。
    for path in CONTRACT_FILES:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (payload.get("target_message_contains") or {}).items():
                if len(value) > len(needles.get(key) or []):
                    needles[key] = list(value)

    run = Path(args.run).resolve()
    baseline = Path(args.baseline_run).resolve()
    current_items, _ = gm.load_system(run, docs)
    baseline_items, _ = gm.load_system(baseline, docs)
    enrich_evidence(current_items, eval_lib.load_raw_index(run))
    enrich_evidence(baseline_items, eval_lib.load_raw_index(baseline))

    baseline_by_key = {f"{i['doc']}|{i.get('raw_id')}": i for i in baseline_items}
    current_by_id = {i["id"]: i for i in current_items}
    # 基线内容索引：判断当前条目是否“新出现”。
    baseline_texts = defaultdict(list)
    for item in baseline_items:
        baseline_texts[(item["doc"], item.get("rule"))].append(item)

    historical = {r["target_id"]: r for r in csv.DictReader(
        HISTORICAL_STATUS.open(encoding="utf-8-sig"))}
    strict = {r["target_id"]: r for r in csv.DictReader(
        STRICT_STATUS.open(encoding="utf-8-sig"))}
    matrix = list(csv.DictReader(TARGET_MATRIX.open(encoding="utf-8-sig")))

    def is_new(item: Dict[str, Any]) -> bool:
        """当前条目在基线中没有同规则同正文的对应项。"""
        key = norm(item.get("message"))
        for old in baseline_texts.get((item["doc"], item.get("rule")), []):
            if norm(old.get("message")) == key:
                return False
        return True

    def covers(item: Dict[str, Any], anchor: str) -> bool:
        return cmp._anchor_covered(item, anchor, eval_lib)

    rows: List[Dict[str, Any]] = []
    for target in matrix:
        tid = target["target_id"]
        sample = target["sample_id"]
        ttype = target["target_type"]
        rule_or_check = target.get("rule_or_check") or ""
        entry: Dict[str, Any] = {
            "target_id": tid,
            "stage": target.get("owner_stage"),
            "type": ttype,
            "sample_id": sample,
            "rule_or_check": rule_or_check,
            "gold_units": target.get("gold_units"),
            "evidence_pages": target.get("evidence_pages"),
            "historical_status": (historical.get(tid) or {}).get("status"),
            "strict_status": (strict.get(tid) or {}).get("status"),
            "known_pending": tid in KNOWN_PENDING,
        }
        if ttype == "FP":
            source_id = target.get("source_id") or ""
            old = baseline_by_key.get(f"{sample}|{source_id}")
            if old is None:
                entry.update(current_state="baseline_identity_missing",
                             detail="冻结基线中找不到该原条目")
            else:
                matched = []
                for item in current_items:
                    if item["doc"] != sample or item.get("rule") != old.get("rule"):
                        continue
                    if not (set(cmp._pages(item)) & set(cmp._pages(old))):
                        continue
                    anchors = [str(old.get("message") or "")] + list(old.get("quotes") or [])
                    anchors = [a for a in anchors if norm(a)]
                    covered = any(covers(item, anchor) for anchor in anchors)
                    identity = _same_entity(old, item)
                    if not covered and identity is not True:
                        # 文本已变且身份也不能确认：不作为原 FP 的残留候选。
                        continue
                    matched.append({
                        "id": item["id"],
                        "tier": item.get("tier"),
                        "similarity": round(_message_similarity(old.get("message"),
                                                               item.get("message")), 3),
                        "message": str(item.get("message") or "")[:300],
                        "amounts": _amount_tokens(item.get("message")),
                        "pages": cmp._pages(item),
                        "covered": covered,
                        "same_entity": identity,
                        "relation": sorted(_identity(item)["relations"])[:4],
                    })
                matched.sort(key=lambda row: (-row["similarity"], str(row["id"])))
                best = matched[0] if matched else None
                residual_f = [row for row in matched
                              if row.get("tier") == "F" and row.get("same_entity") is True]
                ambiguous_f = [row for row in matched
                               if row.get("tier") == "F" and row.get("same_entity") is None]
                downgraded = [row for row in matched
                              if row.get("tier") in {"R", "P", "D", "N"}
                              and row.get("same_entity") is True]
                entry.update(
                    current_state="still_present" if matched else "removed",
                    current_items=[row["id"] for row in matched[:10]],
                    matching_entities=matched[:10],
                    residual_f_entities=residual_f,
                    ambiguous_f_entities=ambiguous_f,
                    downgraded_entities=downgraded,
                    baseline_rule=old.get("rule"),
                    baseline_pages=cmp._pages(old),
                    baseline_message=str(old.get("message") or "")[:300],
                    baseline_amounts=_amount_tokens(old.get("message")),
                    baseline_relation=sorted(_identity(old)["relations"])[:4],
                    best_current_match=best,
                )
        else:
            unit = cmp._find_unit(docs, sample, target.get("gold_units") or "")
            scoped = needles.get(tid) or []
            check_key = str(target.get("rule_or_check") or "")
            allowed_rules = check_rules.get(check_key) or []
            role_entry = strict_roles.get(tid)
            if unit is None:
                entries = []
            elif role_entry:
                entries = [{"quote": item["quote"], "page": item.get("page"),
                            "role": item.get("role") or "defect_occurrence"}
                           for item in role_entry.get("anchors") or []]
            else:
                entries = cmp._defect_anchor_entries(unit, scoped)
                for item in entries:
                    item["role"] = "defect_occurrence"
            raw_entries = cmp._defect_anchor_entries(unit) if unit else []
            f_items = [i for i in current_items
                       if i["doc"] == sample and i.get("tier") == "F" and evidence_ok(i)
                       and (not allowed_rules or cmp._rule_matches(i, allowed_rules))]
            p_items = [i for i in current_items
                       if i["doc"] == sample and i.get("tier") == "P"
                       and (not allowed_rules or cmp._rule_matches(i, allowed_rules))]
            name_only = check_key in {
                "GS-N05", "GS-S01", "GS-S03", "GS-S04", "free:R21",
                "free:R22", "free:R24", "free:R28", "free:R9-半角冒号",
            }
            anchor_rows, remaining = [], []
            excluded = []
            for anchor in entries:
                if _is_control(controls, sample, check_key, anchor):
                    excluded.append(anchor)
                    continue
                f_hits = [i["id"] for i in f_items
                          if cmp._anchor_covered(i, anchor["quote"], eval_lib, anchor.get("page"),
                                                 amount_required=not name_only)]
                p_hits = [i["id"] for i in p_items
                          if cmp._anchor_covered(i, anchor["quote"], eval_lib, anchor.get("page"),
                                                 amount_required=not name_only)]
                row = {"quote": anchor["quote"], "page": anchor.get("page"),
                       "role": anchor.get("role") or "defect_occurrence",
                       "covered": bool(f_hits), "f_items": f_hits[:6], "p_items": p_hits[:6]}
                anchor_rows.append(row)
                remaining.append(row)
            required = [row for row in remaining if row.get("role") != "correct_contrast"]
            correct_contrast = [row for row in remaining if row.get("role") == "correct_contrast"]
            covering = [item for row in required for item in row["f_items"]]
            covering_p = [row for row in required if row["p_items"] and not row["covered"]]
            covered = [row for row in required if row["covered"]]
            missing = [row for row in required if not row["covered"]]
            if unit is None:
                entry.update(current_state="gold_unit_missing",
                             anchors_total=0, anchors_covered=0)
            else:
                entry.update(
                    anchors_total=len(required),
                    anchors_covered=len(covered),
                    anchors_raw_total=len(raw_entries),
                    anchor_entries=anchor_rows,
                    required_anchor_roles=sorted({row["role"] for row in required}),
                    correct_contrast_entries=correct_contrast,
                    excluded_control_entries=excluded,
                    scoped_needles=scoped,
                    check_rules=allowed_rules,
                    missing_anchors=[row["quote"] for row in missing],
                    covering_f_items=sorted(set(covering))[:12],
                    new_f_items=sorted({i for i in set(covering)
                                        if is_new(current_by_id[i])})[:12],
                    covering_p_items=len(covering_p),
                )
                if required and len(covered) == len(required):
                    state = "satisfied"
                elif covered:
                    state = "partial"
                elif covering_p:
                    state = "candidate_only"
                else:
                    state = "missing"
                entry["current_state"] = state
                defect_missing = any(row["role"] == "defect_occurrence" for row in missing)
                operand_missing = any(row["role"] == "required_operand" for row in missing)
                if state != "satisfied" and covering_p and not covered:
                    entry["classification"] = "candidate_pending_confirmation"
                elif state == "partial":
                    entry["classification"] = ("occurrence_gap" if defect_missing
                                               else "required_operand_gap")
                elif state == "missing":
                    entry["classification"] = ("no_formal_occurrence" if defect_missing
                                               else "required_operand_gap")
                else:
                    entry["classification"] = "satisfied"
                if operand_missing and not defect_missing:
                    entry["classification"] = "required_operand_gap"
            entry["rule_matches"] = sorted({i.get("rule") for i in f_items
                                            if any(cmp._anchor_covered(i, row["quote"], eval_lib, row.get("page"))
                                                   for row in remaining)}) if unit else []
        if tid in KNOWN_PENDING:
            entry["classification"] = "pending_adjudication"
        rows.append(entry)

    columns = [
        "target_id", "stage", "type", "sample_id", "rule_or_check", "gold_units",
        "evidence_pages", "historical_status", "strict_status", "known_pending",
        "current_state", "classification", "anchors_total", "anchors_covered",
        "anchors_raw_total", "covering_f_items", "new_f_items", "covering_p_items",
        "missing_anchors", "current_items", "baseline_rule", "baseline_pages",
        "baseline_message", "baseline_relation", "residual_f_entities",
        "ambiguous_f_entities", "downgraded_entities", "detail",
    ]
    with (args.out / "target_ledger.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("missing_anchors", "current_items", "baseline_pages", "baseline_relation"):
                if isinstance(flat.get(key), list):
                    flat[key] = " | ".join(str(v) for v in flat[key])
            for key in ("residual_f_entities", "ambiguous_f_entities", "downgraded_entities"):
                if isinstance(flat.get(key), list):
                    flat[key] = " | ".join(str(v.get("id")) for v in flat[key])
            writer.writerow(flat)
    (args.out / "target_ledger.json").write_text(
        json.dumps({
            "run": str(run),
            "baseline_run": str(baseline),
            "targets": len(rows),
            "counts": {
                "current_state": _counts(rows, "current_state"),
                "classification": _counts(rows, "classification"),
            },
            "rows": rows,
        }, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "targets": len(rows),
        "current_state": _counts(rows, "current_state"),
        "classification": _counts(rows, "classification"),
    }, ensure_ascii=False, indent=2))
    return 0


def _message_similarity(left: Any, right: Any) -> float:
    def normalize(value: Any) -> str:
        text = re.sub(r"\s+", "", str(value or ""))
        return re.sub(r"\d[\d,.]*", "#", text)
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def _amount_tokens(text: Any) -> List[str]:
    normalized = re.sub(r"\s+", "", str(text or ""))
    return [token.replace(",", "").replace("，", "")
            for token in re.findall(r"\d[\d,]*(?:\.\d+)?", normalized)]


def _is_control(controls: List[Dict[str, Any]], sample: str, check_key: str,
                anchor: Dict[str, Any]) -> bool:
    quote = re.sub(r"\s+", "", str(anchor.get("quote") or ""))
    for entry in controls:
        if str(entry.get("sample_id")) != sample:
            continue
        if str(entry.get("check_id")) != check_key:
            continue
        if entry.get("page") not in (None, anchor.get("page")):
            continue
        target = re.sub(r"\s+", "", str(entry.get("quote") or ""))
        if quote and target and (quote == target or quote in target or target in quote):
            return True
    return False


def _counts(rows, key):
    result = {}
    for row in rows:
        value = str(row.get(key))
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
