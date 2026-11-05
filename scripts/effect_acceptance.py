#!/usr/bin/env python3
"""real-painpoint-effect-v1：本轮"效果验收"草案（不修改、不复用旧 r4 判定）。

为什么另建一份策略
------------------
旧 r4 的诊断规则假设"基线里未完成的关系应保持未完成"，因此把关系修成
pass / not_applicable 会被 T1/T11 判失败。本次修复的目标恰恰是把有证据的
关系修好，所以需要一份**区分"结论保持不变"与"允许修复带来正确变化"**的
策略；旧 r4 及其报告保持原样。

与 r4 的关系
------------
- 输入复用冻结 160 材料的采集记录（同一 record 结构）；
- 记录旧 kit 摘要与本策略摘要，不重封印旧包、不改旧 meta；
- 本草案由执行方实现并自测，最终由协调方独立审核，不自行签发正式通过。

检查分组
--------
A. 同一候选双跑可重复性（--repeat）：两次采集的代码摘要、样本集与逐键
   语义输出必须一致。
B. 基线 → 候选的效果差异（--baseline/--candidate）：
   E1 样本与包身份；E2 仍未完成键的原因完整性；E3 未完成→pass 必须有
   原件来源/操作数/公式；E4 未完成→N-A 必须有不适用依据；E5 纯状态变更
   拒绝；E6 键消失/新增必须归因；E7 迁移账字段完整；E8 目标外异常
   （正式问题、操作数）不得悄悄消失。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

POLICY_VERSION = "real-painpoint-effect-v1"
UNRESOLVED = {"insufficient_data", "parse_error", "execution_error"}
RESOLVED = {"pass", "not_applicable"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_records(root: Path) -> Tuple[Dict[str, dict], dict]:
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    records: Dict[str, dict] = {}
    for path in sorted((root / "records").glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            record = json.load(handle)
        records[record.get("sample_id") or path.name.split(".")[0]] = record
    return records, meta


def receipt_key(receipt: dict) -> Tuple[str, str, str]:
    return (str(receipt.get("obligation_id") or ""),
            str(receipt.get("rule_id") or ""),
            str(receipt.get("sub_check_id") or ""))


def evidence_tuple(receipt: dict) -> Tuple[str, ...]:
    """回执里可核验的证据字段（用于识别"只改状态"）。"""
    parts: List[str] = []
    if receipt.get("operand_fact_ids"):
        parts.append("operands:" + ",".join(map(str, receipt["operand_fact_ids"])))
    if receipt.get("formula"):
        parts.append("formula:" + str(receipt["formula"]))
    for source in receipt.get("disclosure_sources") or []:
        if isinstance(source, dict) and (source.get("span") or source.get("quote")):
            parts.append(f"source:{source.get('page')}:{(source.get('span') or source.get('quote'))[:60]}")
    for cell in receipt.get("cell_observations") or []:
        if isinstance(cell, dict) and cell.get("source_cell_id"):
            parts.append(f"cell:{cell.get('source_cell_id')}")
    if receipt.get("explained_by"):
        parts.append("explained:" + str(receipt["explained_by"])[:60])
    return tuple(parts)


def evidence_complete(receipt: dict) -> bool:
    """两条可接受的证据路径：

    1. 事实计算：有操作数 + 公式/单元格观测；
    2. 披露证明：有带页码与原文的说明来源 + 说明依据（explained_by 或原因文本）。
       显式零证据走第 2 条，空表格没有物化事实，不能强求 operand ids。
    """
    sources = [s for s in receipt.get("disclosure_sources") or []
               if isinstance(s, dict) and (s.get("span") or s.get("quote"))]
    if receipt.get("operand_fact_ids") and (receipt.get("formula") or receipt.get("cell_observations")):
        return True
    return bool(sources) and bool(str(receipt.get("explained_by") or "").strip()
                                  or str(receipt.get("incomplete_reason") or "").strip())


_NON_FISCAL = re.compile(r"非财政拨款|其他资金|事业收入|经营收入|上级补助|附属单位上缴|自有资金|非财政补助")
_LEVEL = re.compile(r"(?:决算|预算)(?:数)?(?:为|是)?\s*(\d[\d,]*(?:\.\d+)?)\s*万元")
_DELTA = re.compile(r"(?:增加|减少|增长|下降)")


def zero_source_problems(receipt: dict) -> List[str]:
    """零/结案证据的语义资格：资金范围、阶段、水平值、矛盾。"""
    problems: List[str] = []
    quotes = [str(s.get("span") or s.get("quote") or "")
              for s in receipt.get("disclosure_sources") or [] if isinstance(s, dict)]
    quotes = [q for q in quotes if q]
    if not quotes:
        return problems
    if any(_NON_FISCAL.search(q) for q in quotes):
        problems.append("来源为其他资金口径，不能证明财政拨款")
    levels = [m.group(1).replace(",", "") for q in quotes for m in _LEVEL.finditer(q)]
    if not levels and any(_DELTA.search(q) for q in quotes):
        problems.append("只有增减额，没有本期水平值")
    parsed = {float(v) for v in levels}
    if any(v == 0 for v in parsed) and any(v != 0 for v in parsed):
        problems.append("同一指标同时出现 0 与非零水平值")
    expected_stage = str((receipt.get("operand_sources") or {}).get("stage") or "")
    if expected_stage:
        word = "决算" if expected_stage in {"决算", "final"} else "预算"
        other = "预算" if word == "决算" else "决算"
        if any(other in q and word not in q for q in quotes):
            problems.append(f"来源为{other}口径，不能证明{word}关系")
    return problems


def na_basis(receipt: dict) -> bool:
    """不适用依据：必须写明原因或给出披露来源，不能只有状态。"""
    if str(receipt.get("explained_by") or "").strip():
        return True
    if str(receipt.get("incomplete_reason") or "").strip():
        return True
    return any(isinstance(s, dict) and (s.get("span") or s.get("quote"))
               for s in receipt.get("disclosure_sources") or [])


def merge_support(receipt: dict, relations: List[dict]) -> dict:
    """义务级汇总回执没有证据，用同 (义务, 规则) 的关系回执补齐。"""
    merged = dict(receipt)
    if evidence_complete(receipt):
        return merged
    sources, cells, operands, formulas = [], [], [], []
    for rel in relations:
        sources.extend(rel.get("disclosure_sources") or [])
        cells.extend(rel.get("cell_observations") or [])
        operands.extend(rel.get("operand_fact_ids") or [])
        if rel.get("formula"):
            formulas.append(str(rel["formula"]))
    if sources:
        merged["disclosure_sources"] = sources
    if cells:
        merged["cell_observations"] = cells
    if operands:
        merged["operand_fact_ids"] = list(dict.fromkeys(map(str, operands)))
    if formulas:
        merged["formula"] = "；".join(dict.fromkeys(formulas))
    if not merged.get("explained_by") and not merged.get("incomplete_reason"):
        reason = next((str(r.get("incomplete_reason")) for r in relations if r.get("incomplete_reason")), None)
        if reason:
            merged["explained_by"] = reason
    return merged


def ledger_row(sample: dict, key: Tuple[str, str, str], before: dict, after: dict,
               conclusion: str) -> dict:
    sources = [s for s in (after.get("disclosure_sources") or []) if isinstance(s, dict)]
    cells = [c for c in (after.get("cell_observations") or []) if isinstance(c, dict)]
    return {
        "sample_id": sample.get("sample_id"),
        "artifact_sha256": sample.get("sha256"),
        "key": list(key),
        "from": before.get("status"),
        "to": after.get("status"),
        "reason_codes_before": list(before.get("reason_codes") or []),
        "reason_codes_after": list(after.get("reason_codes") or []),
        "operand_fact_ids": list(after.get("operand_fact_ids") or []),
        "formula": after.get("formula"),
        "source_pages": sorted({s.get("page") for s in sources if s.get("page") is not None}),
        "source_quotes": [(s.get("span") or s.get("quote") or "")[:120] for s in sources][:5],
        "cell_ids": [c.get("source_cell_id") for c in cells][:10],
        "explained_by": after.get("explained_by"),
        "conclusion": conclusion,
    }


def compare_collections(baseline_dir: Path, candidate_dir: Path) -> dict:
    baseline, bmeta = load_records(baseline_dir)
    candidate, cmeta = load_records(candidate_dir)
    results: List[dict] = []
    ledger: List[dict] = []

    def add(check_id: str, title: str, problems: Iterable[str], details: Optional[List[dict]] = None):
        items = [str(p) for p in problems]
        results.append({"id": check_id, "title": title, "verdict": "PASS" if not items else "FAIL",
                        "count": len(items), "details": (details or items)[:200]})

    # E1 样本与包身份
    problems: List[str] = []
    if set(baseline) != set(candidate):
        problems.append(f"样本集合不同：缺 {sorted(set(baseline)-set(candidate))[:5]}，"
                        f"多 {sorted(set(candidate)-set(baseline))[:5]}")
    if bmeta.get("kit_digest") != cmeta.get("kit_digest"):
        problems.append("两次采集不属于同一冻结包")
    add("E1", "样本集合与冻结包身份一致", problems)

    # E2 仍未完成键的原因完整性
    problems = []
    preexisting: List[str] = []
    for sample_id in sorted(set(baseline) & set(candidate)):
        before_map = {receipt_key(r): r for r in baseline[sample_id].get("receipts") or []}
        for receipt in candidate[sample_id].get("receipts") or []:
            if receipt.get("status") not in UNRESOLVED:
                continue
            key = receipt_key(receipt)
            old = before_map.get(key) or {}
            where = f"{sample_id} {'|'.join(key)}"
            if not receipt.get("reason_codes"):
                problems.append(f"{where}: 未完成但无原因码")
            if not receipt.get("next_action"):
                problems.append(f"{where}: 未完成但无 next_action")
            if not (receipt.get("blocked_by") or receipt.get("missing_operands")):
                # 基线同样缺失 → 既有缺口；只有"基线有、候选没有"才算回归
                if old.get("blocked_by") or old.get("missing_operands"):
                    problems.append(f"{where}: 阻断/缺数位置被删除")
                else:
                    preexisting.append(where)
    add("E2", "仍未完成的检查具备原因与动作（阻断缺失仅算回归）", problems)
    if preexisting:
        results.append({"id": "E2i", "title": "基线也缺阻断位置的未完成回执（仅报告）",
                        "verdict": "INFO", "count": len(preexisting), "details": preexisting[:50]})

    # E3/E4/E5 状态迁移的证据资格
    e3: List[str] = []
    e4: List[str] = []
    e5: List[str] = []
    e6: List[str] = []
    e8: List[str] = []
    changed = 0
    for sample_id in sorted(set(baseline) & set(candidate)):
        before_rec = baseline[sample_id]
        after_rec = candidate[sample_id]
        before_map = {receipt_key(r): r for r in before_rec.get("receipts") or []}
        after_map = {receipt_key(r): r for r in after_rec.get("receipts") or []}
        for key, old in before_map.items():
            new = after_map.get(key)
            if new is None:
                e6.append(f"{sample_id} {'|'.join(key)}: 回执消失")
                continue
            if old.get("status") == new.get("status"):
                # 同状态但操作数被删：目标外异常
                old_ops, new_ops = set(old.get("operand_fact_ids") or []), set(new.get("operand_fact_ids") or [])
                if old_ops - new_ops:
                    e8.append(f"{sample_id} {'|'.join(key)}: 操作数被删除 {sorted(old_ops-new_ops)[:3]}")
                continue
            changed += 1
            where = f"{sample_id} {'|'.join(key)}"
            is_aggregate = key[2] == f"{key[0]}:{key[1]}"
            relations = [r for r in after_rec.get("receipts") or []
                         if str(r.get("obligation_id")) == key[0]
                         and str(r.get("rule_id")) == key[1]
                         and receipt_key(r) != key]
            if old.get("status") in UNRESOLVED and new.get("status") in RESOLVED:
                # 只有义务级汇总回执需要关系回执佐证；单条关系回执自身即证据。
                new_evidenced = merge_support(new, relations) if is_aggregate else new
            else:
                new_evidenced = new
            if old.get("status") in UNRESOLVED and new.get("status") == "pass":
                source_problems = zero_source_problems(new_evidenced)
                if is_aggregate and any(r.get("status") in UNRESOLVED for r in relations):
                    source_problems.append("仍有未完成子关系")
                if not evidence_complete(new_evidenced) or source_problems:
                    e3.append(f"{where}: 未完成→pass 证据不足"
                              + (f"（{'；'.join(source_problems)}）" if source_problems else ""))
                    ledger.append(ledger_row(before_rec, key, old, new_evidenced, "REJECTED_NO_EVIDENCE"))
                else:
                    ledger.append(ledger_row(before_rec, key, old, new_evidenced, "RECOVERED_WITH_EVIDENCE"))
            elif old.get("status") in UNRESOLVED and new.get("status") == "not_applicable":
                if not na_basis(new_evidenced):
                    e4.append(f"{where}: 未完成→N/A 缺不适用依据")
                    ledger.append(ledger_row(before_rec, key, old, new_evidenced, "REJECTED_NO_NA_BASIS"))
                else:
                    ledger.append(ledger_row(before_rec, key, old, new_evidenced, "NOT_APPLICABLE_WITH_BASIS"))
            elif new.get("status") in RESOLVED and old.get("issue_refs"):
                # 撤销误报：基线有正式问题、候选没有；不需要操作数证据，
                # 但必须在迁移账里标明"撤销并归因"。
                ledger.append(ledger_row(before_rec, key, old, new, "FINDING_REMOVED"))
            elif new.get("status") in RESOLVED:
                if not evidence_tuple(new):
                    e5.append(f"{where}: 状态改变但无任何证据字段")
                    ledger.append(ledger_row(before_rec, key, old, new, "REJECTED_STATE_ONLY"))
                else:
                    ledger.append(ledger_row(before_rec, key, old, new, "CHANGED_WITH_EVIDENCE"))
            else:
                ledger.append(ledger_row(before_rec, key, old, new, "CHANGED_TO_UNRESOLVED"))
        for key in set(after_map) - set(before_map):
            e6.append(f"{sample_id} {'|'.join(key)}: 新增回执")
        # F 层问题数变化（只报告，逐一列出）
        if len(after_rec.get("findings") or []) != len(before_rec.get("findings") or []):
            e8.append(f"{sample_id}: 正式问题数 {len(before_rec.get('findings') or [])} → "
                      f"{len(after_rec.get('findings') or [])}")
    add("E3", "未完成→pass 具备原件来源、操作数与公式", e3)
    add("E4", "未完成→不适用 具备不适用依据", e4)
    add("E5", "禁止只改状态、没有证据", e5)
    add("E6", "回执键不得消失或凭空新增", e6)
    add("E8", "正式问题与操作数不得目标外减少", e8)

    # E7 迁移账完整性
    problems = []
    for row in ledger:
        missing = [field for field in ("sample_id", "artifact_sha256", "key", "from", "to", "conclusion")
                   if not row.get(field)]
        if missing:
            problems.append(f"{row.get('sample_id')} {'|'.join(row.get('key') or [])}: 缺 {missing}")
    add("E7", "迁移账字段完整（样本/哈希/键/前后/结论）", problems)

    overall = "PASS" if all(item["verdict"] in {"PASS", "INFO"} for item in results) else "FAIL"
    report = {
        "policy": POLICY_VERSION,
        "baseline": str(baseline_dir), "candidate": str(candidate_dir),
        "baseline_kit_digest": bmeta.get("kit_digest"), "candidate_kit_digest": cmeta.get("kit_digest"),
        "baseline_policy_digest": sha256_file(Path(__file__)),
        "samples": len(set(baseline) & set(candidate)),
        "changed_receipts": changed,
        "checks": results, "overall": overall,
    }
    return {"report": report, "ledger": ledger}


def reproducibility(first_dir: Path, second_dir: Path) -> dict:
    first, fmeta = load_records(first_dir)
    second, smeta = load_records(second_dir)
    problems: List[str] = []
    if set(first) != set(second):
        problems.append("两次采样的样本集合不同")
    if fmeta.get("code") != smeta.get("code"):
        problems.append("两次采集的代码摘要不同")
    for sample_id in sorted(set(first) & set(second)):
        a = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                   for r in (first[sample_id].get("receipts") or []))
        b = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                   for r in (second[sample_id].get("receipts") or []))
        if a != b:
            problems.append(f"{sample_id}: 两次采集的回执语义不一致")
        fa = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                    for r in (first[sample_id].get("findings") or []))
        fb = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False)
                    for r in (second[sample_id].get("findings") or []))
        if fa != fb:
            problems.append(f"{sample_id}: 两次采集的正式问题不一致")
    return {"policy": POLICY_VERSION, "check": "same_candidate_reproducibility",
            "verdict": "PASS" if not problems else "FAIL",
            "samples": len(set(first) & set(second)), "problems": problems[:200]}


# ---------------------------------------------------------------------------
# 自控：后 8 类错误必须被拒绝
# ---------------------------------------------------------------------------

def _receipt(status: str, **extra: Any) -> dict:
    base = {"obligation_id": "OBL-X", "rule_id": "R-X", "sub_check_id": "R-X:1",
            "status": status, "reason_codes": [], "blocked_by": [], "missing_operands": [],
            "next_action": None, "root_cause_key": "rc:x", "operand_fact_ids": [],
            "formula": None, "disclosure_sources": [], "cell_observations": [],
            "explained_by": None, "incomplete_reason": None}
    base.update(extra)
    return base


def _record(receipts: List[dict], findings: Optional[List[dict]] = None) -> dict:
    return {"sample_id": "S1", "sha256": "a" * 64, "receipts": receipts,
            "findings": findings or [], "obligations": {}, "coverage_items": {}}


def selftest() -> int:
    import tempfile

    def write(root: Path, record: dict, code: str = "code-1") -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "records").mkdir(exist_ok=True)
        (root / "meta.json").write_text(json.dumps({"kit_digest": "kit", "code": code}), encoding="utf-8")
        with gzip.open(root / "records" / "S1.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        return root

    failures: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        unresolved = _receipt("insufficient_data", reason_codes=["operand_missing"],
                              blocked_by=[{"dimension": "cell", "ref": "第1页", "reason_code": "operand_missing"}],
                              missing_operands=[{"page": 1}], next_action="system_recheck")
        base = write(root / "base", _record([unresolved]))

        # 1 正确恢复正控：带来源与操作数
        good = _receipt("pass", operand_fact_ids=["f1"], formula="a+b",
                        disclosure_sources=[{"page": 2, "span": "原文明确为0"}])
        result = compare_collections(base, write(root / "good", _record([good])))["report"]
        if result["overall"] != "PASS":
            failures.append("正控应通过：" + json.dumps(result["checks"], ensure_ascii=False)[:300])

        def expect_fail(name: str, receipt: dict, check_id: str) -> None:
            result = compare_collections(base, write(root / f"bad_{name}", _record([receipt])))["report"]
            hit = [c for c in result["checks"] if c["id"] == check_id]
            if not hit or hit[0]["verdict"] != "FAIL":
                failures.append(f"{name}: 应被 {check_id} 拒绝")

        # 2 只有 pass、没有任何证据
        expect_fail("state_only", _receipt("pass"), "E3")
        # 3 非财政零串财政：来源文本含“非财政拨款”
        expect_fail("non_fiscal", _receipt("pass", operand_fact_ids=["f1"],
                                           disclosure_sources=[{"page": 2, "span": "非财政拨款公务接待费决算0万元"}]), "E3")
        # 4 决算串预算：来源只写决算，却标成年初预算
        expect_fail("final_to_budget", _receipt("pass", operand_fact_ids=["f1"],
                                                operand_sources={"stage": "年初预算"},
                                                disclosure_sources=[{"page": 2, "span": "决算0万元"}]), "E3")
        # 5 纯增减额误用：没有本期水平值
        expect_fail("delta_only", _receipt("pass", operand_fact_ids=["f1"],
                                           disclosure_sources=[{"page": 2, "span": "比上年增加0.20万元"}]), "E3")
        # 6 矛盾本期值：同一来源既有 0 又有 1
        expect_fail("contradiction", _receipt("pass", operand_fact_ids=["f1"],
            disclosure_sources=[{"page": 2, "span": "决算0万元"}, {"page": 2, "span": "决算1万元"}]), "E3")
        # 7 无资格 N/A：只写状态
        expect_fail("na_without_basis", _receipt("not_applicable"), "E4")
        # 8 删除回执
        result = compare_collections(base, write(root / "deleted", _record([])))["report"]
        if not [c for c in result["checks"] if c["id"] == "E6" and c["verdict"] == "FAIL"]:
            failures.append("deleted_receipt: 应被 E6 拒绝")
        # 9 新增未完成被忽略
        extra = _receipt("insufficient_data", rule_id="R-Y", sub_check_id="R-Y:1",
                         reason_codes=["operand_missing"], next_action="system_recheck",
                         blocked_by=[{"dimension": "cell", "ref": "x", "reason_code": "operand_missing"}])
        result = compare_collections(base, write(root / "new_key", _record([unresolved, extra])))["report"]
        if not [c for c in result["checks"] if c["id"] == "E6" and c["verdict"] == "FAIL"]:
            failures.append("new_unresolved: 新增键应被 E6 报告")

        # 可重复性：同一份采集自比必须 PASS
        same = reproducibility(base, base)
        if same["verdict"] != "PASS":
            failures.append("reproducibility 自比应通过")

    print(json.dumps({"policy": POLICY_VERSION, "selftest": "PASS" if not failures else "FAIL",
                      "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--repeat", help="同一候选的第二次独立采集，仅做可重复性检查")
    parser.add_argument("--out")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.baseline and args.candidate and args.out):
        parser.error("需要 --baseline --candidate --out（或 --selftest）")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result = compare_collections(Path(args.baseline), Path(args.candidate))
    (out / "effect_report.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    (out / "transition_ledger.json").write_text(json.dumps(result["ledger"], ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    if args.repeat:
        repro = reproducibility(Path(args.candidate), Path(args.repeat))
        (out / "reproducibility.json").write_text(json.dumps(repro, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print(json.dumps({"overall": result["report"]["overall"],
                          "reproducibility": repro["verdict"],
                          "changed_receipts": result["report"]["changed_receipts"]}, ensure_ascii=False))
    else:
        print(json.dumps({"overall": result["report"]["overall"],
                          "changed_receipts": result["report"]["changed_receipts"]}, ensure_ascii=False))
    return 0 if result["report"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
