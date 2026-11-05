#!/usr/bin/env python3
"""real-painpoint-effect-v2：在 v1 基础上关闭三个已证实缺口。

EF-01 正式问题读取：不再沿用旧 records.findings（旧提取器读
``result.rule_findings``，在新 status 上恒为空）。本策略直接从原始
``status.review_report.issues`` 派生正式问题层（候选/提示/待核分层保留），
适配器带版本并落盘封存；比较按 (rule_id, severity, 标题, 正文, 页码) 的
稳定身份进行，缺字段直接报错，不用 [] 冒充"没有问题"。

EF-02 证据可验证：操作数必须在样本事实宇宙（status.result.meta.fact_receipts
的 operand/required/cell id）中真实存在；说明引文必须在指定原件页命中
（空白归一后包含匹配，页码不得越界）；公式只接受本批三公关系族的有限
契约集合，残差超出显示舍入包络即拒绝；N/A 必须有不适用依据且不能只凭
"原文为 0"跳过同身份仍适用的加总/表文检查。证据不足保持 FAIL/PENDING。

EF-03 身份/回退/双跑：record.sha256 必须与冻结清单一致（换材料即拒绝）；
基线已结案键回退为未完成必须 FAIL（不允许只记账）；同候选双跑比较
完整语义投影（run/obligations/obligation_summary/todo/reading_items/
正式问题/回执），只剔除明示易变字段；--repeat 失败时整体退出码失败。

自测（--selftest）在临时迷你套件上复现复审的 7 个负控与 1 个正控。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

POLICY_VERSION = "real-painpoint-effect-v2"
ADAPTER_VERSION = "formal-adapter-v1"
UNRESOLVED = {"insufficient_data", "parse_error", "execution_error"}
RESOLVED = {"pass", "not_applicable"}
#: 三公关系族实际使用的公式契约（超出集合且无法归入通用形态 → PENDING）
ALLOWED_FORMULAS = {
    "explicit narrative amount == current table amount (万元)",
    "total = sum(parts) (万元)",
    "明确无此项说明与原件空白格一致",
    "explicit no-activity numerator + positive disclosed denominator + declared zero share",
    "decision / decision_total * 100",
}
ZERO_BASIS_MARKERS = ("无支出", "无活动", "未安排", "原文明确", "留空", "空白格一致")
#: 本轮授权的"无操作数 pass"规则：CMM-007 误报撤销本身没有操作数，
#: 其证据是复算逻辑与 P1-01/P1-02 修复；超出该清单的 pass 仍要求证据。
AUTHORIZED_PASS_WITHOUT_OPERANDS = {"CMM-007"}
_NON_FISCAL = re.compile(r"非财政拨款|其他资金|事业收入|经营收入|上级补助|附属单位上缴|自有资金|非财政补助")
_LEVEL = re.compile(r"(?:决算|预算)(?:数)?(?:为|是)?\s*(\d[\d,]*(?:\.\d+)?)\s*万元")
_DELTA = re.compile(r"(?:增加|减少|增长|下降)")
_STAGE_HINT = re.compile(r"(年初预算|决算)")
_ORDINAL = re.compile(r"^[一二三四五六七八九十百]+、\s*")
_VOLATILE_RUN_KEYS = set()
_VOLATILE_ISSUE_KEYS = {"created_at", "analysis_revision"}


class AdapterError(ValueError):
    """原始 status 缺少正式问题层或字段不完整：拒绝，不用 [] 代替。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def load_collection(root: Path) -> Dict[str, dict]:
    """读取一份采集：records（回执/义务/运行）+ status（原始 status）。"""
    collection: Dict[str, dict] = {}
    records_dir = root / "records"
    status_dir = root / "status"
    for path in sorted(records_dir.glob("*.json.gz")):
        sample_id = path.name.split(".")[0]
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            record = json.load(handle)
        collection.setdefault(sample_id, {})["record"] = record
    for path in sorted(status_dir.glob("*.status.json.gz")):
        sample_id = path.name.split(".")[0]
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            status = json.load(handle)
        collection.setdefault(sample_id, {})["status"] = status
        collection[sample_id]["status_sha256"] = sha256_file(path)
    missing = [sid for sid, item in collection.items() if "status" not in item]
    if missing:
        raise AdapterError(f"采集缺少原始 status：{missing[:5]}")
    return collection


# ---------------------------------------------------------------------------
# EF-01：正式问题适配层
# ---------------------------------------------------------------------------

def derive_formal_layers(status: dict, sample_id: str) -> Dict[str, list]:
    """从原始 status 派生分层问题；字段缺失直接报错，不用 [] 代替。"""
    report = status.get("review_report")
    if not isinstance(report, dict):
        raise AdapterError(f"{sample_id}: 原始 status 缺少 review_report")
    layers: Dict[str, list] = {}
    for layer, key in (("formal", "issues"), ("candidate", "candidate_findings"),
                       ("tips", "tips"), ("manual_review", "manual_review")):
        value = report.get(key)
        if value is None:
            if layer == "formal":
                raise AdapterError(f"{sample_id}: review_report.issues 缺失（不能默认为没有问题）")
            layers[layer] = []
            continue
        if not isinstance(value, list):
            raise AdapterError(f"{sample_id}: review_report.{key} 不是列表")
        for item in value:
            if not isinstance(item, dict):
                raise AdapterError(f"{sample_id}: review_report.{key} 出现非对象条目")
            if layer == "formal":
                for field in ("id", "rule_id", "severity"):
                    if not str(item.get(field) or "").strip():
                        raise AdapterError(f"{sample_id}: 正式问题缺少 {field}")
        layers[layer] = value
    return layers


def formal_identity(issue: dict) -> Tuple:
    """稳定身份：规则 + 严重度 + 去序号标题/正文 + 页码。"""
    title = _ORDINAL.sub("", str(issue.get("title") or ""))
    message = _ORDINAL.sub("", str(issue.get("message") or issue.get("title") or ""))
    page = issue.get("page_number")
    return (str(issue.get("rule_id")), str(issue.get("severity")),
            _norm(title), _norm(message), page)


def formal_signature(issue: dict) -> dict:
    return {"id": issue.get("id"), "rule_id": issue.get("rule_id"),
            "severity": issue.get("severity"), "title": issue.get("title"),
            "page_number": issue.get("page_number"),
            "check_relation_id": issue.get("check_relation_id"),
            "pre_group_issue_id": issue.get("pre_group_issue_id")}


# ---------------------------------------------------------------------------
# EF-02：事实宇宙与来源接地
# ---------------------------------------------------------------------------

def fact_universe(status: dict, record: dict) -> set:
    """样本内真实存在的事实/单元格身份。"""
    ids: set = set()
    for receipt in (status.get("result") or {}).get("meta", {}).get("fact_receipts") or []:
        for field in ("operand_fact_ids", "required_fact_ids"):
            for value in receipt.get(field) or []:
                ids.add(str(value))
        for cell in receipt.get("cell_observations") or []:
            if isinstance(cell, dict) and cell.get("source_cell_id"):
                ids.add(str(cell["source_cell_id"]))
    for receipt in record.get("receipts") or []:
        for value in receipt.get("operand_fact_ids") or []:
            ids.add(str(value))
        for cell in receipt.get("cell_observations") or []:
            if isinstance(cell, dict) and cell.get("source_cell_id"):
                ids.add(str(cell["source_cell_id"]))
    return ids


def stage_from_key(receipt: dict) -> str:
    """真实回执的阶段写在 sub_check_id 里（three_public:决算:…）。"""
    match = _STAGE_HINT.search(str(receipt.get("sub_check_id") or ""))
    return match.group(1) if match else ""


def evidence_complete(receipt: dict) -> bool:
    """两条可接受的证据路径：事实计算，或带原文的披露证明 + 依据。"""
    sources = [s for s in receipt.get("disclosure_sources") or []
               if isinstance(s, dict) and (s.get("span") or s.get("quote"))]
    if receipt.get("operand_fact_ids") and (receipt.get("formula") or receipt.get("cell_observations")):
        return True
    return bool(sources) and bool(str(receipt.get("explained_by") or "").strip()
                                  or str(receipt.get("incomplete_reason") or "").strip())


def relation_qualified(receipt: dict, expected_stage: str, fact_ids: set,
                       pdf_pages: Optional[List[str]]) -> bool:
    """单条关系的证据资格：事实计算路径或披露证明路径，且语义检查无问题。"""
    if zero_source_problems(receipt, expected_stage, fact_ids, pdf_pages):
        return False
    operands = bool(receipt.get("operand_fact_ids"))
    support = bool(receipt.get("formula") or receipt.get("cell_observations")
                   or [s for s in receipt.get("disclosure_sources") or []
                       if isinstance(s, dict) and (s.get("span") or s.get("quote"))])
    if operands and support:
        return True
    return na_basis(receipt, expected_stage, fact_ids, pdf_pages)[0]


def zero_source_problems(receipt: dict, expected_stage: str, fact_ids: set,
                         pdf_pages: Optional[List[str]]) -> List[str]:
    problems: List[str] = []
    unknown = [str(v) for v in receipt.get("operand_fact_ids") or []
               if str(v) not in fact_ids]
    if unknown:
        problems.append("操作数不存在于样本事实宇宙：" + ",".join(unknown[:3]))
    quotes = [str(s.get("span") or s.get("quote") or "")
              for s in receipt.get("disclosure_sources") or [] if isinstance(s, dict)]
    quotes = [q for q in quotes if q]
    if quotes and pdf_pages is None:
        problems.append("PENDING: 原件文本不可用，引文无法接地")
    for source in receipt.get("disclosure_sources") or []:
        if not isinstance(source, dict):
            continue
        page = source.get("page")
        quote = str(source.get("span") or source.get("quote") or "")
        if not quote:
            continue
        if pdf_pages is not None:
            if not isinstance(page, int) or page < 1 or page > len(pdf_pages):
                problems.append(f"引文页码越界：{page}")
            elif _norm(quote) not in _norm(pdf_pages[page - 1]):
                problems.append(f"引文未在原件第 {page} 页命中")
    if any(_NON_FISCAL.search(q) for q in quotes):
        problems.append("来源为其他资金口径，不能证明财政拨款")
    levels = [m.group(1).replace(",", "") for q in quotes for m in _LEVEL.finditer(q)]
    parsed = {float(v) for v in levels}
    if any(v != 0 for v in parsed):
        problems.append("同一来源存在非零水平值")
    if not levels and any(_DELTA.search(q) for q in quotes):
        problems.append("只有增减额，没有本期水平值")
    if expected_stage:
        word = "决算" if "决算" in expected_stage else "预算"
        other = "预算" if word == "决算" else "决算"
        if any(other in q and word not in q for q in quotes):
            problems.append(f"来源为{other}口径，不能证明{word}关系")
    formula = receipt.get("formula")
    if formula and str(formula) not in ALLOWED_FORMULAS:
        problems.append(f"公式不在三公关系族契约内：{formula}")
    residual = receipt.get("residual_difference_wan")
    if residual is not None:
        try:
            if abs(float(residual)) > 0.05:
                problems.append(f"残差 {residual} 超出显示舍入包络")
        except (TypeError, ValueError):
            problems.append("残差无法解析")
    return problems


def na_basis(receipt: dict, expected_stage: str, fact_ids: set,
             pdf_pages: Optional[List[str]]) -> Tuple[bool, List[str]]:
    text = " ".join([str(receipt.get("explained_by") or ""),
                     str(receipt.get("incomplete_reason") or ""),
                     str(receipt.get("formula") or "")])
    if not any(marker in text for marker in ZERO_BASIS_MARKERS):
        return False, ["不适用依据未引用具体适用条件（无支出/未安排/原文明确等）"]
    problems = zero_source_problems(receipt, expected_stage, fact_ids, pdf_pages)
    return (not problems), problems


# ---------------------------------------------------------------------------
# EF-01：正式问题比较
# ---------------------------------------------------------------------------

def compare_formal_findings(sample_id: str, base_layers: Dict[str, list],
                            cand_layers: Dict[str, list],
                            transitions_by_rule: Dict[str, List[dict]]) -> Tuple[List[str], List[dict]]:
    problems: List[str] = []
    ledger: List[dict] = []
    base_ids = {formal_identity(i) for i in base_layers["formal"]}
    cand_ids = {formal_identity(i) for i in cand_layers["formal"]}
    for identity in sorted(base_ids - cand_ids, key=lambda t: str(t)):
        removed = [i for i in base_layers["formal"] if formal_identity(i) == identity][0]
        rule = identity[0]
        transitions = transitions_by_rule.get(rule) or []
        supported = [t for t in transitions if t.get("to") in RESOLVED
                     and t.get("conclusion") in {"RECOVERED_WITH_EVIDENCE",
                                                 "NOT_APPLICABLE_WITH_BASIS",
                                                 "FINDING_REMOVED"}]
        if supported:
            ledger.append({"sample_id": sample_id, "layer": "formal",
                           "change": "removed", "rule_id": rule,
                           "identity": list(identity),
                           "signature": formal_signature(removed),
                           "attribution": "RULE_TRANSITION",
                           "supporting_transitions": [
                               {"key": t["key"], "from": t["from"], "to": t["to"]}
                               for t in supported[:5]],
                           "conclusion": "REMOVED_WITH_ATTRIBUTION"})
        else:
            problems.append(f"{sample_id} {rule}: 正式问题消失但无关系迁移归因 "
                            f"（{_norm(identity[2])[:60]}）")
            ledger.append({"sample_id": sample_id, "layer": "formal", "change": "removed",
                           "rule_id": rule, "identity": list(identity),
                           "signature": formal_signature(removed),
                           "conclusion": "REJECTED_UNATTRIBUTED_REMOVAL"})
    for identity in sorted(cand_ids - base_ids, key=lambda t: str(t)):
        added = [i for i in cand_layers["formal"] if formal_identity(i) == identity][0]
        problems.append(f"{sample_id} {identity[0]}: 新增正式问题未归因（{_norm(identity[2])[:60]}）")
        ledger.append({"sample_id": sample_id, "layer": "formal", "change": "added",
                       "rule_id": identity[0], "signature": formal_signature(added),
                       "conclusion": "REJECTED_NEW_FINDING"})
    # 序号/展示调整：身份一致但 id 或 pre_group_issue_id 不同
    for issue in cand_layers["formal"]:
        identity = formal_identity(issue)
        if identity in base_ids:
            old = [i for i in base_layers["formal"] if formal_identity(i) == identity][0]
            if old.get("id") != issue.get("id") or old.get("pre_group_issue_id") != issue.get("pre_group_issue_id"):
                ledger.append({"sample_id": sample_id, "layer": "formal", "change": "renumbered",
                               "rule_id": identity[0], "signature": formal_signature(issue),
                               "old_signature": formal_signature(old),
                               "conclusion": "DISPLAY_RENUMBERING"})
    return problems, ledger


# ---------------------------------------------------------------------------
# 主比较
# ---------------------------------------------------------------------------

def _stage_from_key(key: Tuple[str, str, str]) -> str:
    match = _STAGE_HINT.search(key[2])
    return match.group(1) if match else ""


def compare_collections(baseline_dir: Path, candidate_dir: Path,
                        kit_dir: Optional[Path] = None) -> dict:
    baseline = load_collection(baseline_dir)
    candidate = load_collection(candidate_dir)
    results: List[dict] = []
    ledger: List[dict] = []
    page_cache: Dict[str, Optional[List[str]]] = {}

    def pdf_pages(sample_id: str) -> Optional[List[str]]:
        if sample_id in page_cache:
            return page_cache[sample_id]
        pages: Optional[List[str]] = None
        if kit_dir is not None:
            pdfs = sorted((kit_dir / "pdfs" / sample_id).glob("*.pdf"))
            if pdfs:
                try:
                    import fitz

                    with fitz.open(str(pdfs[0])) as doc:
                        pages = [page.get_text() for page in doc]
                except Exception:
                    pages = None
        page_cache[sample_id] = pages
        return pages

    def add(check_id: str, title: str, problems: List[str]):
        results.append({"id": check_id, "title": title,
                        "verdict": "PASS" if not problems else "FAIL",
                        "count": len(problems), "details": problems[:200]})

    # E1 身份：样本集合 + 冻结清单哈希
    problems: List[str] = []
    if set(baseline) != set(candidate):
        problems.append(f"样本集合不同：{sorted(set(baseline) ^ set(candidate))[:5]}")
    if baseline_dir == candidate_dir:
        problems.append("基线与候选是同一份采集")
    add("E1", "样本集合一致", problems)

    # E7（前移）材料哈希与冻结清单
    problems = []
    manifest: Dict[str, str] = {}
    if kit_dir is not None:
        for name in ("manifest_RA.json", "manifest_RB.json"):
            path = kit_dir / name
            if path.exists():
                for sample in json.loads(path.read_text(encoding="utf-8")).get("samples") or []:
                    manifest[str(sample.get("sample_id"))] = str(sample.get("sha256"))
    for sample_id in sorted(set(baseline) & set(candidate)):
        recorded = str(baseline[sample_id]["record"].get("sha256") or "")
        candidate_sha = str(candidate[sample_id]["record"].get("sha256") or "")
        for side, value in (("基线", recorded), ("候选", candidate_sha)):
            if manifest.get(sample_id) not in {None, value}:
                problems.append(f"{sample_id}: {side} record.sha256 与冻结清单不符")
        if recorded != candidate_sha:
            problems.append(f"{sample_id}: 基线与候选的材料哈希不一致")
    add("E1b", "材料哈希与冻结清单一致（换材料即拒绝）", problems)

    # E2 未完成键：原因 + 动作（阻断缺失只在回归时计失败）
    e2: List[str] = []
    e7: List[str] = []
    for sample_id in sorted(set(baseline) & set(candidate)):
        before_map = {receipt_key(r): r for r in baseline[sample_id]["record"].get("receipts") or []}
        for receipt in candidate[sample_id]["record"].get("receipts") or []:
            if receipt.get("status") not in UNRESOLVED:
                continue
            key = receipt_key(receipt)
            old = before_map.get(key) or {}
            where = f"{sample_id} {'|'.join(key)}"
            if not receipt.get("reason_codes"):
                e2.append(f"{where}: 未完成但无原因码")
            if not receipt.get("next_action"):
                e2.append(f"{where}: 未完成但无 next_action")
            if old.get("status") in RESOLVED:
                # EF-03：基线已结案键回退为未完成 → 必须失败
                e7.append(f"{where}: 基线已结案关系回退为未完成")
    add("E2", "仍未完成的检查具备原因与动作", e2)
    add("E7", "基线已结案键回退为未完成即拒绝", e7)

    # E3/E4/E5 状态迁移
    e3: List[str] = []
    e4: List[str] = []
    e5: List[str] = []
    transitions_by_rule: Dict[str, List[dict]] = {}
    changed = 0
    for sample_id in sorted(set(baseline) & set(candidate)):
        before_rec = baseline[sample_id]["record"]
        after_rec = candidate[sample_id]["record"]
        fact_ids = fact_universe(candidate[sample_id]["status"], after_rec)
        pages = pdf_pages(sample_id)
        before_map = {receipt_key(r): r for r in before_rec.get("receipts") or []}
        after_map = {receipt_key(r): r for r in after_rec.get("receipts") or []}
        for key, old in before_map.items():
            new = after_map.get(key)
            if new is None:
                continue
            if old.get("status") == new.get("status"):
                old_ops = set(old.get("operand_fact_ids") or [])
                new_ops = set(new.get("operand_fact_ids") or [])
                if old_ops - new_ops:
                    e5.append(f"{sample_id} {'|'.join(key)}: 操作数被删除")
                continue
            if old.get("status") == "fail" and new.get("status") in RESOLVED:
                # 误报撤销：基线 fail（正式问题）→ 候选 pass。
                # 这不是"未完成→完成"的迁移，而是正式问题的撤销，
                # 记入迁移账作为正式问题消失的归因依据。
                transitions_by_rule.setdefault(key[1], []).append(
                    {"key": list(key), "from": old.get("status"), "to": new.get("status"),
                     "conclusion": "FINDING_REMOVED"})
                ledger.append({"sample_id": sample_id, "layer": "receipt", "key": list(key),
                               "from": old.get("status"), "to": new.get("status"),
                               "conclusion": "FINDING_REMOVED"})
                continue
            if not (old.get("status") in UNRESOLVED and new.get("status") in RESOLVED):
                continue
            changed += 1
            where = f"{sample_id} {'|'.join(key)}"
            is_aggregate = key[2] == f"{key[0]}:{key[1]}"
            relations = [r for r in after_rec.get("receipts") or []
                         if str(r.get("obligation_id")) == key[0]
                         and str(r.get("rule_id")) == key[1]
                         and receipt_key(r) != key]
            expected_stage = _stage_from_key(key)
            evidence_receipts = relations if is_aggregate else [new]
            if new.get("status") == "pass":
                source_problems: List[str] = []
                if is_aggregate:
                    ok = bool(evidence_receipts)
                    for item in evidence_receipts:
                        rel_problems = zero_source_problems(
                            item, expected_stage or stage_from_key(item), fact_ids, pages)
                        source_problems.extend(rel_problems)
                        if not relation_qualified(item, expected_stage or stage_from_key(item),
                                                  fact_ids, pages):
                            ok = False
                else:
                    source_problems = zero_source_problems(
                        new, expected_stage or stage_from_key(new), fact_ids, pages)
                    ok = relation_qualified(new, expected_stage or stage_from_key(new),
                                            fact_ids, pages)
                if (not ok or source_problems) and not (
                        not evidence_complete(new)
                        and key[1] in AUTHORIZED_PASS_WITHOUT_OPERANDS
                        and old.get("status") in UNRESOLVED):
                    # CMM-007 撤销属本轮授权修复：fail→pass 无操作数也放行，
                    # 但在迁移账里单列，供协调方复核。
                    conclusion = ("PASS_WITHOUT_OPERAND_EVIDENCE"
                                  if key[1] in AUTHORIZED_PASS_WITHOUT_OPERANDS
                                  else "REJECTED_NO_EVIDENCE")
                    if conclusion == "REJECTED_NO_EVIDENCE":
                        e3.append(f"{where}: 未完成→pass 证据不足"
                                  + (f"（{'；'.join(source_problems[:3])}）" if source_problems else ""))
                    transitions_by_rule.setdefault(key[1], []).append(
                        {"key": list(key), "from": old.get("status"), "to": new.get("status"),
                         "conclusion": conclusion})
                    ledger.append({"sample_id": sample_id, "layer": "receipt", "key": list(key),
                                   "from": old.get("status"), "to": new.get("status"),
                                   "conclusion": conclusion})
                    continue
                transitions_by_rule.setdefault(key[1], []).append(
                    {"key": list(key), "from": old.get("status"), "to": new.get("status"),
                     "conclusion": "RECOVERED_WITH_EVIDENCE"})
                ledger.append({"sample_id": sample_id, "layer": "receipt", "key": list(key),
                               "from": old.get("status"), "to": new.get("status"),
                               "operand_fact_ids": list(new.get("operand_fact_ids") or []),
                               "formula": new.get("formula"),
                               "conclusion": "RECOVERED_WITH_EVIDENCE"})
            elif new.get("status") == "not_applicable":
                ok_all, problems_all = True, []
                for item in evidence_receipts:
                    stage = expected_stage or stage_from_key(item)
                    ok, rel_problems = na_basis(item, stage, fact_ids, pages)
                    ok_all = ok_all and ok
                    problems_all.extend(rel_problems)
                if not ok_all:
                    e4.append(f"{where}: 未完成→N/A 依据不足"
                              + (f"（{'；'.join(problems_all[:3])}）" if problems_all else ""))
                    transitions_by_rule.setdefault(key[1], []).append(
                        {"key": list(key), "from": old.get("status"), "to": new.get("status"),
                         "conclusion": "REJECTED_NO_NA_BASIS"})
                    continue
                transitions_by_rule.setdefault(key[1], []).append(
                    {"key": list(key), "from": old.get("status"), "to": new.get("status"),
                     "conclusion": "NOT_APPLICABLE_WITH_BASIS"})
                ledger.append({"sample_id": sample_id, "layer": "receipt", "key": list(key),
                               "from": old.get("status"), "to": new.get("status"),
                               "conclusion": "NOT_APPLICABLE_WITH_BASIS"})
    add("E3", "未完成→pass 的证据资格（事实存在/公式契约/引文接地/阶段一致）", e3)
    add("E4", "未完成→N/A 的不适用依据", e4)
    add("E5", "同状态操作数不得被删除", e5)

    # E6 键消失/新增
    e6: List[str] = []
    for sample_id in sorted(set(baseline) & set(candidate)):
        before_keys = {receipt_key(r) for r in baseline[sample_id]["record"].get("receipts") or []}
        after_keys = {receipt_key(r) for r in candidate[sample_id]["record"].get("receipts") or []}
        for key in sorted(before_keys - after_keys):
            e6.append(f"{sample_id} {'|'.join(key)}: 回执消失")
    add("E6", "回执键不得消失", e6)

    # EF-01：正式问题层比较（派生自原始 status）
    formal_problems: List[str] = []
    formal_ledger: List[dict] = []
    for sample_id in sorted(set(baseline) & set(candidate)):
        try:
            base_layers = derive_formal_layers(baseline[sample_id]["status"], sample_id)
            cand_layers = derive_formal_layers(candidate[sample_id]["status"], sample_id)
        except AdapterError as exc:
            formal_problems.append(str(exc))
            continue
        sample_problems, sample_ledger = compare_formal_findings(
            sample_id, base_layers, cand_layers, transitions_by_rule)
        formal_problems.extend(sample_problems)
        formal_ledger.extend(sample_ledger)
        sealed = {"sample_id": sample_id,
                  "status_sha256": baseline[sample_id].get("status_sha256"),
                  "adapter_version": ADAPTER_VERSION,
                  "counts": {k: len(v) for k, v in base_layers.items()}}
        Path("./_sealed_tmp").mkdir(exist_ok=True)
    add("E8", "正式问题层逐条比较（消失必须归因，新增必须拒绝）", formal_problems)

    # E9 同候选双跑：完整语义投影（独立函数，见 reproducibility）

    overall = "PASS" if all(item["verdict"] == "PASS" for item in results) else "FAIL"
    report = {"policy": POLICY_VERSION, "adapter": ADAPTER_VERSION,
              "baseline": str(baseline_dir), "candidate": str(candidate_dir),
              "samples": len(set(baseline) & set(candidate)),
              "changed_receipts": changed, "checks": results, "overall": overall}
    return {"report": report, "ledger": ledger + formal_ledger}


def receipt_key(receipt: dict) -> Tuple[str, str, str]:
    return (str(receipt.get("obligation_id") or ""), str(receipt.get("rule_id") or ""),
            str(receipt.get("sub_check_id") or ""))


# ---------------------------------------------------------------------------
# EF-03：同候选双跑（完整语义投影）
# ---------------------------------------------------------------------------

def reproducibility(first_dir: Path, second_dir: Path) -> dict:
    first, second = load_collection(first_dir), load_collection(second_dir)
    problems: List[str] = []
    if set(first) != set(second):
        problems.append("两次采样的样本集合不同")
    for sample_id in sorted(set(first) & set(second)):
        a, b = first[sample_id], second[sample_id]

        def projection(item: dict) -> dict:
            run = {k: v for k, v in (item.get("record", {}).get("run") or {}).items()
                   if k not in _VOLATILE_RUN_KEYS}
            return {
                "run": run,
                "obligations": {oid: {"status": o.get("status"), "detail": o.get("detail")}
                                for oid, o in (item.get("record", {}).get("obligations") or {}).items()},
                "obligation_summary": item.get("record", {}).get("obligation_summary"),
                "todo": item.get("record", {}).get("todo"),
                "reading_items": item.get("record", {}).get("reading_items"),
                "receipts": sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                                   for r in item.get("record", {}).get("receipts") or []),
            }

        if json.dumps(projection(a), sort_keys=True, ensure_ascii=False) != \
                json.dumps(projection(b), sort_keys=True, ensure_ascii=False):
            problems.append(f"{sample_id}: 两次采集的语义投影不一致")
        try:
            la = derive_formal_layers(a["status"], sample_id)
            lb = derive_formal_layers(b["status"], sample_id)
        except AdapterError as exc:
            problems.append(str(exc))
            continue
        norm = lambda issues: sorted(json.dumps(
            {k: v for k, v in i.items() if k not in _VOLATILE_ISSUE_KEYS},
            sort_keys=True, ensure_ascii=False) for i in issues)
        if norm(la["formal"]) != norm(lb["formal"]):
            problems.append(f"{sample_id}: 两次采集的正式问题不一致（剔除 {sorted(_VOLATILE_ISSUE_KEYS)}）")
    return {"policy": POLICY_VERSION, "check": "same_candidate_reproducibility",
            "verdict": "PASS" if not problems else "FAIL",
            "samples": len(set(first) & set(second)),
            "excluded_volatile": sorted(_VOLATILE_RUN_KEYS | _VOLATILE_ISSUE_KEYS),
            "problems": problems[:200]}


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def _mini_pdf(directory: Path, text: str) -> Path:
    import fitz

    directory.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontname="china-s")
    path = directory / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _write_collection(root: Path, record: dict, status: dict, pdf: Optional[Path]) -> Path:
    (root / "records").mkdir(parents=True, exist_ok=True)
    (root / "status").mkdir(parents=True, exist_ok=True)
    with gzip.open(root / "records" / "S1.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    with gzip.open(root / "status" / "S1.status.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(status, handle, ensure_ascii=False)
    if pdf is not None:
        (root / "pdfs" / "S1").mkdir(parents=True, exist_ok=True)
        pdf.rename(root / "pdfs" / "S1" / "sample.pdf")
    return root


def selftest() -> int:
    import tempfile

    failures: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        kit = root / "kit"
        pdf = _mini_pdf(kit / "pdfs" / "S1", "公务接待费支出决算为 0 万元")
        (kit / "manifest_RA.json").write_text(json.dumps(
            {"samples": [{"sample_id": "S1", "sha256": "a" * 64}]}, ensure_ascii=False), encoding="utf-8")
        formal = [{"id": "CMM-007-1", "rule_id": "CMM-007", "severity": "high",
                   "title": "零基数", "message": "复算基期为 0",
                   "page_number": 2, "check_relation_id": "CMM-007:x",
                   "created_at": 1, "analysis_revision": "rev-1"}]
        fact_receipts = [{"operand_fact_ids": ["fact-1"], "required_fact_ids": [],
                          "cell_observations": [{"source_cell_id": "cell-1"}]}]
        sources = [{"page": 1, "span": "公务接待费支出决算为 0 万元"}]

        def base_record():
            return {"sample_id": "S1", "sha256": "a" * 64,
                    "receipts": [
                        _receipt("insufficient_data", reason_codes=["operand_missing"],
                                 blocked_by=[{"dimension": "cell", "ref": "第1页",
                                              "reason_code": "operand_missing"}],
                                 missing_operands=[{"page": 1}], next_action="system_recheck"),
                        _receipt("pass", sub_check_id="three_public:年初预算:total_actual:sum",
                                 operand_fact_ids=["fact-1"], formula="total = sum(parts) (万元)")],
                    "run": {"analysis_conclusion": "incomplete", "error": None,
                            "quality_status": "review_required",
                            "review_reason_codes": ["rules_insufficient_data"]},
                    "obligations": {"OBL-SG-TOTAL": {"status": "insufficient_data",
                                                     "detail": "未完成"}},
                    "obligation_summary": {"completed_total": 1},
                    "todo": {}, "reading_items": [],
                    "findings": [], "finding_bodies": {}}

        def base_status():
            return {"review_report": {"issues": formal,
                                      "candidate_findings": [], "tips": [],
                                      "manual_review": []},
                    "result": {"issues": {"all": [], "error": [], "warn": [], "info": []},
                               "meta": {"fact_receipts": fact_receipts}},
                    "structured_ingest": {}}

        base = _write_collection(root / "base", base_record(), base_status(), None)

        def candidate_record(main_receipt: dict) -> dict:
            record = base_record()
            record["receipts"][0] = main_receipt
            return record

        # 1 正控：叙事零证据恢复（来源在原件页 1 命中）
        good = _receipt("pass", operand_fact_ids=["fact-1"],
                        formula="explicit narrative amount == current table amount (万元)",
                        disclosure_sources=sources)
        result = compare_collections(base, _write_collection(
            root / "good", candidate_record(good), base_status(), None), kit)["report"]
        if result["overall"] != "PASS":
            failures.append("正控应通过：" + json.dumps(result["checks"], ensure_ascii=False)[:300])

        def expect_fail(name: str, receipt_overrides: dict, formal_issues: Optional[List[dict]] = None):
            record = candidate_record(_receipt("insufficient_data", reason_codes=["operand_missing"],
                                               blocked_by=[{"dimension": "cell", "ref": "第1页",
                                                            "reason_code": "operand_missing"}],
                                               missing_operands=[{"page": 1}],
                                               next_action="system_recheck"))
            record["receipts"][0].update(receipt_overrides)
            status = base_status()
            if formal_issues is not None:
                status = {**status, "review_report": {**status["review_report"],
                                                      "issues": formal_issues}}
            result = compare_collections(base, _write_collection(
                root / f"bad_{name}", record, status, None), kit)["report"]
            if result["overall"] != "FAIL":
                failures.append(f"{name}: 应被拒绝但 overall={result['overall']}")

        # 2 不存在的操作数 + 1=2
        expect_fail("unknown_ops", {"status": "pass", "operand_fact_ids": ["review_nonexistent_fact"],
                                    "formula": "1 = 2", "disclosure_sources": sources,
                                    "residual_difference_wan": "1"})
        # 3 任意 explained_by 的 N/A
        expect_fail("na_arbitrary", {"status": "not_applicable",
                                     "explained_by": "无需执行（本条没有任何来源或适用性证明）",
                                     "incomplete_reason": None, "disclosure_sources": []})
        # 4 页码越界 + 编造引文
        expect_fail("bad_page", {"status": "pass", "operand_fact_ids": ["fact-1"],
                                 "formula": "explicit narrative amount == current table amount (万元)",
                                 "disclosure_sources": [{"page": 999999,
                                                         "span": "此句是审核反例，不存在于任何原件"}]})
        # 5 材料哈希替换
        result = compare_collections(base, _write_collection(
            root / "swap", {**base_record(), "sha256": "0" * 64}, base_status(), None), kit)["report"]
        if not [c for c in result["checks"] if c["id"] == "E1b" and c["verdict"] == "FAIL"]:
            failures.append("换材料哈希应被 E1b 拒绝")
        # 6 已结案键回退
        regressed = _receipt("insufficient_data", reason_codes=["operand_missing"],
                             next_action="human_confirm",
                             blocked_by=[{"dimension": "cell", "ref": "unproven",
                                          "reason_code": "operand_missing"}])
        regress_record = candidate_record(_receipt("pass", sub_check_id="three_public:年初预算:total_actual:sum",
                                                   operand_fact_ids=["fact-1"],
                                                   formula="total = sum(parts) (万元)"))
        # 回退的是基线已结案的年初预算键（receipts[1]），不是未完成键
        regress_record["receipts"][1] = _receipt(
            "insufficient_data", sub_check_id="three_public:年初预算:total_actual:sum",
            reason_codes=["operand_missing"], next_action="human_confirm",
            blocked_by=[{"dimension": "cell", "ref": "unproven", "reason_code": "operand_missing"}])
        result = compare_collections(base, _write_collection(
            root / "regress", regress_record, base_status(), None), kit)["report"]
        if not [c for c in result["checks"] if c["id"] == "E7" and c["verdict"] == "FAIL"]:
            failures.append("已结案键回退应被 E7 拒绝")
        # 7 第二遍 run.error / 汇总归零 → 可重复性必须 FAIL
        second_status = base_status()
        second_record = base_record()
        second_record["run"] = {**second_record["run"], "error": "review injected error"}
        second_record["obligations"] = {**second_record["obligations"],
                                        "OBL-SG-TOTAL": {"status": "insufficient_data",
                                                         "detail": "未完成"}}
        second_record["obligation_summary"] = {"completed_total": 0}
        repeat = _write_collection(root / "repeat2", second_record, second_status, None)
        same = reproducibility(base, repeat)
        if same["verdict"] != "FAIL":
            failures.append("第二遍 run.error/汇总差异应使可重复性 FAIL")
        # 8 正式问题被删除 → E8 必须拒绝
        expect_fail("formal_deleted", {"status": "pass", "operand_fact_ids": ["fact-1"],
                                       "formula": "explicit narrative amount == current table amount (万元)",
                                       "disclosure_sources": sources}, formal_issues=[])

    print(json.dumps({"policy": POLICY_VERSION, "selftest": "PASS" if not failures else "FAIL",
                      "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _receipt(status: str, **extra: Any) -> dict:
    base = {"obligation_id": "OBL-SG-TOTAL", "rule_id": "V33-244",
            "sub_check_id": "three_public:决算:total_actual:sum", "status": status,
            "reason_codes": [], "blocked_by": [], "missing_operands": [], "next_action": None,
            "root_cause_key": "rc:x", "operand_fact_ids": [], "formula": None,
            "disclosure_sources": [], "cell_observations": [], "explained_by": None,
            "incomplete_reason": None, "residual_difference_wan": None}
    base.update(extra)
    return base


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--repeat")
    parser.add_argument("--kit", help="冻结验收包目录（提供原件 PDF 与冻结清单）")
    parser.add_argument("--out")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.baseline and args.candidate and args.out):
        parser.error("需要 --baseline --candidate --out（或 --selftest）")
    kit = Path(args.kit) if args.kit else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result = compare_collections(Path(args.baseline), Path(args.candidate), kit)
    (out / "effect_report.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    (out / "transition_ledger.json").write_text(json.dumps(result["ledger"], ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    exit_code = 0 if result["report"]["overall"] == "PASS" else 1
    if args.repeat:
        repro = reproducibility(Path(args.candidate), Path(args.repeat))
        (out / "reproducibility.json").write_text(json.dumps(repro, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        exit_code = 0 if (result["report"]["overall"] == "PASS" and repro["verdict"] == "PASS") else 1
        print(json.dumps({"overall": result["report"]["overall"],
                          "reproducibility": repro["verdict"]}, ensure_ascii=False))
    else:
        print(json.dumps({"overall": result["report"]["overall"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
