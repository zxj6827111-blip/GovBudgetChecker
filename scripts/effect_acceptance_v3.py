#!/usr/bin/env python3
"""效果核验 v3：原始分层、独立事实、逐材料逐关系授权与证据退化门禁。

--evidence 的 SHA 由核验人从候选目录外提供；脚本不生成真值或签收身份。
旧采集缺独立事实时拒收恢复声明，不把历史 PASS 升格为新版通过。
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.acceptance_artifacts import AdapterError, adapt_record, canonical, digest

POLICY_VERSION = "real-painpoint-effect-v3"
UNRESOLVED = {"insufficient_data", "parse_error", "execution_error"}
RESOLVED = {"pass", "not_applicable"}
VOLATILE = {"created_at", "analysis_revision", "evidence_id", "evidence_ids",
            "parse_run_uid", "fact_version", "fact_context_version_token"}


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open(encoding="utf-8")) as handle:
        return json.load(handle)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt_key(row):
    return tuple(str(row.get(k) or "") for k in ("obligation_id", "rule_id", "sub_check_id"))


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def formal_identity(row):
    def text(v):
        return re.sub(r"\s+", "", re.sub(r"^[一二三四五六七八九十百]+、\s*", "", str(v or "")))
    return (row["rule_id"], row["severity"], text(row.get("title")),
            text(row.get("message") or row.get("title")), row.get("page_number"))


def issue_digest(issue):
    # 展示编号可以变化，正文、关系与证据变化仍需逐项验证。
    return digest(stable({k: v for k, v in issue.items() if k not in {"id", "pre_group_issue_id"}}))


def load_collection(root):
    meta = read(root / "meta.json")
    records = {p.name.removesuffix(".json.gz"): read(p) for p in (root / "records").glob("*.json.gz")}
    statuses = {p.name.removesuffix(".status.json.gz"): read(p)
                for p in (root / "status").glob("*.status.json.gz")}
    if not records or records.keys() != statuses.keys():
        raise AdapterError("records/status 为空或样本集合不一致")
    expected = {s["sample_id"]: s["sha256"] for s in meta["samples"]}
    if records.keys() != expected.keys():
        raise AdapterError("meta.samples 与实际样本集合不一致")
    result = {}
    for sid, record in records.items():
        if record.get("sample_id") != sid or record.get("sha256") != expected[sid]:
            raise AdapterError(f"{sid}: 记录身份与采集清单不一致")
        raw = statuses[sid]
        raw_receipts = (raw.get("review_report") or {}).get("check_receipts")
        if not isinstance(raw_receipts, list) or not isinstance(record.get("receipts"), list):
            raise AdapterError(f"{sid}: 缺少原始或标准化回执")
        def receipts(rows):
            return Counter(canonical({k: v for k, v in r.items() if k != "issue_refs"}) for r in rows)
        if receipts(raw_receipts) != receipts(record["receipts"]):
            raise AdapterError(f"{sid}: 标准化回执与原始工件不一致")
        row = adapt_record(record, raw, meta)
        row["_raw_status"] = raw
        result[sid] = row
    return result


def load_evidence(path, expected_sha):
    if not path or not expected_sha or sha(path) != expected_sha:
        raise AdapterError("缺少独立证据包或其外部 SHA256 不匹配")
    result = read(path)
    if result.get("schema") != "independent-effect-evidence-v1":
        raise AdapterError("独立证据包契约版本错误")
    return result


def authorized(evidence, action, before, after, key, old, new):
    """授权必须同时绑定前后身份与具体对象；任何规则级通行证均无效。"""
    return next((d for d in evidence.get("decisions", [])
                 if d.get("action") == action and d.get("before") == before["source_binding"]
                 and d.get("after") == after["source_binding"] and d.get("key") == list(key)
                 and d.get("old_digest") == digest(old) and d.get("new_digest") == digest(new)
                 and d.get("reason") and d.get("sources")), None)


def source_errors(source, pages, pdf_sha):
    errors = []
    if source.get("file_hash", pdf_sha) != pdf_sha:
        errors.append("证据来自其他材料")
    page = source.get("physical_page", source.get("page"))
    quote = source.get("quote") or source.get("span") or source.get("raw_text") or source.get("text")
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= len(pages):
        return errors + ["证据页码缺失或越界"]
    def norm(text):
        return re.sub(r"\s+", "", str(text or ""))
    if not quote or norm(quote) not in norm(pages[page - 1].get_text()):
        errors.append("证据引文未在原页命中")
    box = source.get("bbox")
    if box is not None:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(not isinstance(v, (int, float)) for v in box)
                or not (0 <= box[0] < box[2] <= pages[page - 1].rect.width + 1
                        and 0 <= box[1] < box[3] <= pages[page - 1].rect.height + 1)):
            errors.append("证据 bbox 非法")
        elif quote:
            # 框选区域也须命中，不能只证明同页其他位置有相同数字。
            if norm(quote) not in norm(pages[page - 1].get_textbox(box)):
                errors.append("证据引文不在指定 bbox 内")
    return errors


def verified_decision(decision, pages, pdf_sha):
    if not decision:
        return False
    return all(not source_errors(s, pages, pdf_sha) for s in decision["sources"])


def recovery_errors(receipt, row, evidence, pages):
    errors = []
    bundle = next((s for s in evidence.get("samples", []) if s.get("binding") == row["source_binding"]), None)
    if not bundle:
        return ["缺少同材料/同分析版本的独立事实快照"]
    facts = bundle.get("facts")
    if not isinstance(facts, list):
        return ["独立事实快照缺少 facts 列表"]
    index = {f.get("fact_id"): f for f in facts}
    if len(index) != len(facts) or None in index:
        return ["独立事实身份重复或缺失"]
    contract = next((c for c in bundle.get("relation_contracts", [])
                     if c.get("key") == list(receipt_key(receipt))
                     and c.get("receipt_digest") == digest(receipt)), None)
    if not contract:
        return ["缺少独立逐关系操作数身份契约"]
    values = []
    for fid in receipt.get("operand_fact_ids") or []:
        fact = index.get(fid)
        if not fact:
            errors.append(f"操作数不在独立事实快照：{fid}")
            continue
        errors.extend(source_errors(fact, pages, row["sha256"]))
        expected = (contract.get("operand_identities") or {}).get(fid)
        dimensions = ("table_code", "indicator", "row_key", "fund_scope", "stage")
        if (not isinstance(expected, dict) or any(not expected.get(k) for k in dimensions)
                or any(fact.get(k) != expected[k] for k in dimensions)):
            errors.append("事实表/行/列/资金/阶段身份与独立关系契约不符")
        if fact.get("organization") != row["source_binding"]["organization"] or fact.get("file_hash") != row["sha256"]:
            errors.append("事实主体或材料身份不符")
        # 原件、资金、年度、阶段、关系必须由独立核对记录声明，不能从被测回执补齐。
        if list(receipt_key(receipt)) not in fact.get("relation_keys", []):
            errors.append("事实未授权用于当前关系")
        if fact.get("unit") != "万元" or fact.get("year") != row["source_binding"]["year"]:
            errors.append("事实单位或年度错配")
        try:
            value = Decimal(str(fact["value_wan"]))
            if not value.is_finite():
                raise InvalidOperation
            values.append(value)
        except (KeyError, InvalidOperation):
            errors.append("独立事实金额非法")
    if receipt.get("cell_observations"):
        # 空白观察的解释必须在独立包中显式核对，不能凭回执里的 presence=blank 授权。
        for cell in receipt["cell_observations"]:
            if cell not in bundle.get("verified_blank_observations", []):
                errors.append("空白解释未经独立原格核对")
    formula = receipt.get("formula")
    if formula == "total = sum(parts) (万元)" and len(values) >= 2 and not receipt.get("cell_observations"):
        if abs(values[0] - sum(values[1:])) > max(Decimal("0.01"), Decimal("0.005") * len(values)):
            errors.append("独立复算不成立")
    else:
        # 未实现的公式和空白/N/A路径必须有精确到回执内容的独立复算证明。
        proof = next((p for p in bundle.get("relation_proofs", [])
                      if p.get("receipt_digest") == digest(receipt) and p.get("key") == list(receipt_key(receipt))
                      and p.get("conclusion") == receipt.get("status") and p.get("reason")
                      and p.get("sources")), None)
        if not proof or not verified_decision(proof, pages, row["sha256"]):
            errors.append("当前公式/空白/不适用声明缺少独立逐关系复算或适用性证明")
    return errors


def compare_collections(baseline_dir, candidate_dir, kit_dir, evidence=None):
    evidence = evidence or {}
    problems, pending, ledger = [], [], []
    try:
        before, after = load_collection(baseline_dir), load_collection(candidate_dir)
    except (AdapterError, KeyError, TypeError, ValueError) as exc:
        return {"policy": POLICY_VERSION, "overall": "FAIL", "problems": [str(exc)], "pending": [], "ledger": []}
    if before.keys() != after.keys():
        problems.append("前后样本集合不同")
    if baseline_dir.resolve() == candidate_dir.resolve():
        problems.append("效果比较需要前后独立采集；自比请用 repeat")
    import fitz
    for sid in sorted(before.keys() & after.keys()):
        b, c = before[sid], after[sid]
        if b["sha256"] != c["sha256"]:
            problems.append(f"{sid}: 前后材料 SHA 不同")
            continue
        pdfs = list((kit_dir / "pdfs" / sid).glob("*.pdf"))
        if len(pdfs) != 1 or sha(pdfs[0]) != c["sha256"]:
            problems.append(f"{sid}: 独立原件缺失、不唯一或 SHA 不符")
            continue
        with fitz.open(pdfs[0]) as pages:
            bm = {receipt_key(r): r for r in b["receipts"]}
            cm = {receipt_key(r): r for r in c["receipts"]}
            if len(bm) != len(b["receipts"]) or len(cm) != len(c["receipts"]):
                problems.append(f"{sid}: 回执身份重复，不允许字典覆盖")
            for key in bm.keys() - cm.keys():
                replacements = next((d.get("replacement_keys") for d in evidence.get("decisions", [])
                                     if d.get("action") == "replace_relation" and d.get("key") == list(key)
                                     and d.get("before") == b["source_binding"]
                                     and d.get("after") == c["source_binding"]), None)
                valid_keys = (isinstance(replacements, list) and bool(replacements)
                              and all(isinstance(k, list) and len(k) == 3 and tuple(k) in cm for k in replacements))
                new_rows = [cm[tuple(k)] for k in replacements] if valid_keys else None
                decision = authorized(evidence, "replace_relation", b, c, key, bm[key], new_rows)
                if not valid_keys or not verified_decision(decision, pages, c["sha256"]):
                    problems.append(f"{sid} {key}: 回执消失，缺少逐键替代及原件依据")
            for key, r in cm.items():
                old = bm.get(key)
                if r.get("status") in UNRESOLVED and (not r.get("reason_codes") or not r.get("next_action")):
                    problems.append(f"{sid} {key}: 未完成回执缺原因/动作")
                if old is None or stable(old) != stable(r):
                    if r.get("status") in RESOLVED:
                        problems.extend(f"{sid} {key}: {e}" for e in recovery_errors(r, c, evidence, pages))
                    if old and old.get("status") in RESOLVED and r.get("status") in UNRESOLVED:
                        decision = authorized(evidence, "justified_regression", b, c, key, old, r)
                        if not verified_decision(decision, pages, c["sha256"]):
                            problems.append(f"{sid} {key}: 已完成关系回退无独立依据")
                    ledger.append({"sample_id": sid, "key": key, "old": digest(old), "new": digest(r)})
            bg, cg = {}, {}
            for issue in b["finding_layers"]["formal"]:
                bg.setdefault(formal_identity(issue), []).append(issue)
            for issue in c["finding_layers"]["formal"]:
                cg.setdefault(formal_identity(issue), []).append(issue)
                from src.services.issue_relation_refs import unresolved_issue_relations
                if issue.get("check_relation_id"):
                    missing = unresolved_issue_relations(issue, c["receipts"])
                    if missing:
                        pending.append(f"{sid}: 正式问题引用未闭合 {missing}")
            for identity in bg.keys() | cg.keys():
                bs, cs = bg.get(identity, []), cg.get(identity, [])
                for old, new in zip(bs, cs, strict=False):
                    old_ev = Counter(canonical(stable(e)) for e in old.get("evidence") or [])
                    new_ev = Counter(canonical(stable(e)) for e in new.get("evidence") or [])
                    lost = (old_ev - new_ev or (old.get("location") and not new.get("location"))
                            or (old.get("bbox") and not new.get("bbox")))
                    if lost:
                        decision = authorized(evidence, "evidence_replaced", b, c, identity, old, new)
                        if not verified_decision(decision, pages, c["sha256"]):
                            problems.append(f"{sid} {identity}: 已有问题证据退化/替换无独立依据")
                        if old_ev and not new_ev:
                            problems.append(f"{sid} {identity}: 既有正式问题不能丢失全部报告证据")
                    if issue_digest(old) != issue_digest(new):
                        for source in new.get("evidence") or []:
                            problems.extend(f"{sid}: {e}" for e in source_errors(source, pages, c["sha256"]))
                for action, items in (("remove_issue", bs[len(cs):]), ("add_issue", cs[len(bs):])):
                    for issue in items:
                        old, new = (issue, None) if action == "remove_issue" else (None, issue)
                        decision = authorized(evidence, action, b, c, identity, old, new)
                        if not verified_decision(decision, pages, c["sha256"]):
                            problems.append(f"{sid} {identity}: 正式问题增删无本材料本版本逐问题授权")
            # 其他问题层也必须保留：变化不会自动当作业务错误，但不能无裁决 PASS。
            for layer in ("candidate", "tips", "manual_review"):
                if stable(b["finding_layers"][layer]) != stable(c["finding_layers"][layer]):
                    pending.append(f"{sid}: {layer} 层变化待逐项裁决")
    return {"policy": POLICY_VERSION, "overall": "FAIL" if problems else "PENDING" if pending else "PASS",
            "samples": len(before.keys() & after.keys()), "problems": problems,
            "pending": pending, "ledger": ledger}


def reproducibility(first_dir, second_dir):
    try:
        a, b = load_collection(first_dir), load_collection(second_dir)
        problems = []
        if a.keys() != b.keys():
            problems.append("双跑样本集合不一致")
        for sid in a.keys() & b.keys():
            left, right = dict(a[sid]), dict(b[sid])
            for row in (left, right):
                row.pop("_raw_status")
                row.pop("raw_status_digest")
                row["source_binding"] = {k: v for k, v in row["source_binding"].items() if k != "analysis_revision"}
                # 指纹包含运行代际；比较原始四层语义及证据，不比较由指纹派生的展示索引。
                row.pop("findings")
                row.pop("finding_bodies")
            if stable(left) != stable(right):
                problems.append(f"{sid}: 同版本双跑语义或版本身份不一致")
        return {"verdict": "FAIL" if problems else "PASS", "problems": problems}
    except (AdapterError, KeyError, ValueError, TypeError) as exc:
        return {"verdict": "FAIL", "problems": [str(exc)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--repeat", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("输出目录已存在，拒绝覆盖")
    try:
        evidence = load_evidence(args.evidence, args.evidence_sha256) if args.evidence else {}
        report = compare_collections(args.baseline, args.candidate, args.kit, evidence)
    except (AdapterError, OSError, ValueError) as exc:
        report = {"overall": "FAIL", "problems": [str(exc)]}
    if args.repeat:
        report["repeat"] = reproducibility(args.candidate, args.repeat)
        if report["repeat"]["verdict"] != "PASS":
            report["overall"] = "FAIL"
    report["tool_sha256"] = sha(Path(__file__))
    report["evidence_sha256"] = args.evidence_sha256
    args.out.mkdir(parents=True)
    (args.out / "effect_report.json").write_text(canonical(report), encoding="utf-8")
    print(canonical({"overall": report["overall"]}))
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
