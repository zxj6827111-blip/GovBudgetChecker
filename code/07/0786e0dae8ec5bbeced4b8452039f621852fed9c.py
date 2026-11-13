#!/usr/bin/env python3
"""FA-04 独立复核：同冻版真实性 + 入口比较重跑（当前 68f62c5f 代码）。

1. 当前 gold/ 33 份 annotation.json 哈希 vs 审核方冻结记录（证明真值未被改动）。
2. 两侧载荷 checksum vs 冻结 inputs.json（独立材料锚，不抄载荷自证）。
3. 用当前工作树入口代码重跑 33+6 比较，与 ZCode 保存的 verdicts 逐字段比对。
"""
import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

WT = Path("/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22")
REV = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/r22_final_independent_review_codex_20261107")
PKG = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/r22_gold33_20261107")
GOLD = Path("/Volumes/workSSD/software development/GovBudgetChecker/gold")
OUT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("fa_entry", WT / "scripts/formal_acceptance_entry.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

RESULTS = []


def probe(name, ok, detail=None):
    RESULTS.append({"probe": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name)


def load_status(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def side_expected(payload):
    ident = fa.extract_payload_identity(payload)
    return {
        "material": {"checksum_sha256": ident["material.checksum_sha256"]["value"]},
        "generation": {"analysis_revision": ident["generation.analysis_revision"]["value"]},
        "code_identity": {"rule_registry_version": ident["code.rule_registry_version"]["value"],
                          "rule_policy_version": ident["code.rule_policy_version"]["value"]},
        "config_identity": {"use_local_rules": ident["config.use_local_rules"]["value"],
                            "use_ai_assist": ident["config.use_ai_assist"]["value"],
                            "mode": ident["config.mode"]["value"]},
    }


def contract_for(before, after):
    return {
        "contract_version": fa.CONTRACT_VERSION,
        "mode": "cross_generation",
        "expected_before": side_expected(before),
        "expected_after": side_expected(after),
        "parse_generation_basis": {"generation.parse_run_id": "offline_replay_without_db",
                                   "generation.document_version_id": "offline_replay_without_db"},
    }


# ---- 1. 金标真值未改动 ----
frozen = json.loads((REV / "current_frozen_hash_check.json").read_text())
mismatch = []
for row in frozen:
    sid = row["sample_id"]
    observed = hashlib.sha256((GOLD / sid / "annotation.json").read_bytes()).hexdigest()
    if observed != row["declared_annotation_sha256"]:
        mismatch.append(sid)
probe(f"金标真值零改动：当前 gold/ {len(frozen)} 份注解哈希与审核方冻结记录一致",
      not mismatch, {"mismatch": mismatch})

# FROZEN.csv 与冻版快照逐字节（审核方 frozen_hash_check.json 记录的是哪个文件？）
fh = json.loads((REV / "frozen_hash_check.json").read_text())
print("frozen_hash_check keys:", list(fh.keys())[:6] if isinstance(fh, dict) else f"list[{len(fh)}]")

# ---- 2. 材料锚：两侧 checksum vs 冻结 inputs.json ----
inputs = json.loads((Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_baseline_20261102/inputs/inputs.json")).read_text())
items = inputs["samples"]
checksum_by_sid = {it["sample_id"]: it["sha256"] for it in items}
ann_by_sid = {it["sample_id"]: it["annotation_sha256"] for it in items}
print("inputs.json entries:", len(items), "checksums extracted:", len(checksum_by_sid))

ann_mismatch = sorted(sid for sid, h in ann_by_sid.items()
                      if hashlib.sha256((GOLD / sid / "annotation.json").read_bytes()).hexdigest() != h)
known_rev2 = sorted(r["sample_id"] for r in json.loads((REV / "gold_revision_mismatch.json").read_text())["rows"])
probe("旧inputs.json注解哈希滞后恰好=已知7份rev2修订（既有状态，非新改动）",
      ann_mismatch == known_rev2, {"stale": ann_mismatch, "known_rev2": known_rev2})

anchor_bad = []
for side in ("gold_main", "gold_final"):
    for p in sorted((REV / side / "status").glob("*.status.json.gz")):
        sid = p.name.replace(".status.json.gz", "")
        payload = load_status(p)
        cs = fa._status(payload).get("checksum")
        want = checksum_by_sid.get(sid)
        if want is None or cs != want:
            anchor_bad.append({"side": side, "sid": sid, "payload": cs, "inputs": want})
probe("33金标两侧共66份载荷checksum与冻结inputs.json一致（独立材料锚）",
      not anchor_bad and len(checksum_by_sid) >= 33, {"bad": anchor_bad[:3], "n_checksums": len(checksum_by_sid)})

# ---- 3. 重跑入口比较并与 ZCode 保存件逐字段比对 ----
def rerun(samples, main_dir, final_dir, saved_file):
    saved = {v["sample_id"]: v for v in json.loads((PKG / "entry_compare" / saved_file).read_text())["verdicts"]}
    diffs = []
    stats = {"compared": 0, "pending_review": 0, "rejected": 0, "evidence_lost_total": 0,
             "withdrawn_total": 0, "zero_true": 0, "zero_false": 0}
    rows = []
    for sid in samples:
        b = load_status(REV / main_dir / "status" / f"{sid}.status.json.gz")
        a = load_status(REV / final_dir / "status" / f"{sid}.status.json.gz")
        r = fa.compare_payloads(b, a, contract_for(b, a))
        stats[r["verdict"]] = stats.get(r["verdict"], 0) + 1
        lost = r.get("evidence_lost", [])
        stats["evidence_lost_total"] += len(lost)
        removed = r.get("removed_identities") or {}
        stats["withdrawn_total"] += len(removed.get("withdrawn_with_evidence", []))
        zero = r.get("zero_regression_claim")
        stats["zero_true" if zero else "zero_false"] += 1
        mine = {"sample_id": sid, "verdict": r["verdict"],
                "added": r.get("added_identities", []),
                "removed": removed,
                "content_changed": r.get("content_changed_identities", []),
                "evidence_lost": lost, "zero_regression_claim": zero,
                "claim_reasons": r.get("claim_reasons", [])}
        rows.append({"sample_id": sid, "result": {k: r.get(k) for k in
                     ("verdict", "pending_reason", "claim_reasons", "added_identities",
                      "removed_identities", "content_changed_identities", "evidence_lost",
                      "zero_regression_claim")}})
        sv = saved.get(sid)
        if sv is None:
            diffs.append({"sid": sid, "diff": "missing_in_saved"})
            continue
        for key, mine_key in [("verdict", "verdict"), ("added", "added"), ("removed", "removed"),
                              ("content_changed", "content_changed"), ("evidence_lost", "evidence_lost"),
                              ("zero_regression_claim", "zero_regression_claim"), ("claim_reasons", "claim_reasons")]:
            if json.dumps(sv.get(key), sort_keys=True, ensure_ascii=False) != json.dumps(mine[mine_key], sort_keys=True, ensure_ascii=False):
                diffs.append({"sid": sid, "field": key, "saved": sv.get(key), "mine": mine[mine_key]})
    return stats, diffs, rows


gold_sids = sorted(p.name.replace(".status.json.gz", "") for p in (REV / "gold_main" / "status").glob("*.gz"))
indep_sids = sorted(p.name.replace(".status.json.gz", "") for p in (REV / "indep_main" / "status").glob("*.gz"))
print(f"gold={len(gold_sids)} indep={len(indep_sids)}")

gstats, gdiffs, grows = rerun(gold_sids, "gold_main", "gold_final", "gold33_main_final_entry_compare.json")
probe("33金标重跑与ZCode保存件逐字段一致（当前68f62c5f代码）", not gdiffs,
      {"stats": gstats, "diffs": gdiffs[:3]})
probe("33金标全部compared、证据丢失0、错误撤销0",
      gstats["compared"] == 33 and gstats["evidence_lost_total"] == 0 and gstats["withdrawn_total"] == 0,
      gstats)

istats, idiffs, irows = rerun(indep_sids, "indep_main", "indep_final", "indep_main_final_entry_compare.json")
probe("6独立材料重跑与ZCode保存件逐字段一致", not idiffs, {"stats": istats, "diffs": idiffs[:3]})
probe("6独立材料全部compared、证据丢失0、错误撤销0",
      istats["compared"] == 6 and istats["evidence_lost_total"] == 0 and istats["withdrawn_total"] == 0,
      istats)

# GS-2023-07 变化必须显式登记（不能静默）
g73 = next(r for r in grows if r["sample_id"] == "GS-2023-07")["result"]
probe("GS-2023-07 新增/变化显式登记（非静默零回归）",
      bool(g73["added_identities"] or g73["content_changed_identities"])
      and g73["zero_regression_claim"] is False,
      {k: g73[k] for k in ("added_identities", "content_changed_identities", "zero_regression_claim")})

(OUT / "fa04_rerun.json").write_text(json.dumps(
    {"summary": {"gold": gstats, "indep": istats}, "gold_rows": grows, "indep_rows": irows,
     "diffs_vs_saved": {"gold": gdiffs, "indep": idiffs}}, ensure_ascii=False, indent=2))

failed = [x["probe"] for x in RESULTS if not x["ok"]]
print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ===")
(OUT / "fa04_probes.json").write_text(json.dumps({"probes": RESULTS, "failed": failed}, ensure_ascii=False, indent=2))
sys.exit(1 if failed else 0)