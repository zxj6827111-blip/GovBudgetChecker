#!/usr/bin/env python3
"""entry_compare 重造驱动（观察项2收口：精确 SHA + 工具身份 + 冻版快照）。

用专项工作树当前 HEAD 的正式入口重跑审核方 33+6 原始输出比较，
verdicts 必须与上一版保存件逐字段一致（否则中止），输出绑定：
- tool_commit：工作树 HEAD 精确 SHA（git rev-parse）
- tool_identity：入口脚本 + 三载体组件 SHA256（入口自报，独立核对）
另存 frozen_truth/：FROZEN.csv 逐字节快照 + 逐份注解哈希 + 两侧真值 gold_docs 哈希。
"""
import gzip
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REV = PKG.parent / "r22_final_independent_review_codex_20261107"
WT = Path("/Volumes/workSSD/software development/GovBudgetChecker/artifacts/daily-main")
GOLD = PKG.parents[1] / "gold"
EC = PKG / "entry_compare"

spec = importlib.util.spec_from_file_location("fa_entry", WT / "scripts/formal_acceptance_entry.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=WT, capture_output=True,
                      text=True, check=True).stdout.strip()
TOOL = fa._tool_identity()
assert TOOL["entry_script_sha256"] == hashlib.sha256((WT / "scripts/formal_acceptance_entry.py").read_bytes()).hexdigest()
assert TOOL["component_script_sha256"] == hashlib.sha256((WT / "scripts/verify_three_carriers.py").read_bytes()).hexdigest()


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


def build(main_dir, final_dir, out_name):
    old_path = EC / out_name
    old = json.loads(old_path.read_text())
    old_verdicts = {v["sample_id"]: v for v in old["verdicts"]}
    sids = sorted(p.name.replace(".status.json.gz", "")
                  for p in (REV / main_dir / "status").glob("*.status.json.gz"))
    verdicts = []
    for sid in sids:
        b = load_status(REV / main_dir / "status" / f"{sid}.status.json.gz")
        a = load_status(REV / final_dir / "status" / f"{sid}.status.json.gz")
        r = fa.compare_payloads(b, a, contract_for(b, a))
        row = {
            "sample_id": sid,
            "verdict": r["verdict"],
            "pending_reason": r.get("pending_reason"),
            "claim_reasons": r.get("claim_reasons", []),
            "formal_before": (r.get("snapshot_before") or {}).get("formal_total_deduped"),
            "formal_after": (r.get("snapshot_after") or {}).get("formal_total_deduped"),
            "added": r.get("added_identities", []),
            "removed": r.get("removed_identities", {"withdrawn_with_evidence": [], "removed_unexplained": []}),
            "content_changed": r.get("content_changed_identities", []),
            "evidence_lost": r.get("evidence_lost", []),
            "zero_regression_claim": r.get("zero_regression_claim"),
        }
        prev = old_verdicts.get(sid)
        if prev != row:
            print(f"ABORT: {sid} verdict 与上一版不一致")
            print("old:", json.dumps(prev, ensure_ascii=False)[:400])
            print("new:", json.dumps(row, ensure_ascii=False)[:400])
            sys.exit(1)
        verdicts.append(row)
    out = {
        "main_head": old["main_head"],
        "final_head": old["final_head"],
        "source": old["source"],
        "frozen_truth": "frozen_truth/FROZEN.csv 快照 + annotation_hashes.json（33/33 与当前 gold/ 一致）",
        "tool_commit": HEAD,
        "tool_identity": TOOL,
        "verdicts": verdicts,
    }
    old_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"OK {out_name}: {len(verdicts)} verdicts 逐字段一致，绑定 {HEAD[:8]} + 双哈希")


# ---- 冻版快照 ----
ft = PKG / "frozen_truth"
ft.mkdir(exist_ok=True)
shutil.copyfile(GOLD / "FROZEN.csv", ft / "FROZEN.csv")
import csv
with open(GOLD / "FROZEN.csv", newline="", encoding="utf-8-sig") as f:
    frozen_rows = {r["sample_id"]: r["annotation_sha256"] for r in csv.DictReader(f)}
ann = []
for sid, declared in sorted(frozen_rows.items()):
    observed = hashlib.sha256((GOLD / sid / "annotation.json").read_bytes()).hexdigest()
    ann.append({"sample_id": sid, "frozen_annotation_sha256": declared,
                "observed_annotation_sha256": observed, "matches": observed == declared})
assert all(a["matches"] for a in ann), "FROZEN 与当前 gold/ 不一致"
gd_hashes = {side: hashlib.sha256((PKG / side / "analysis/gold_docs.json").read_bytes()).hexdigest()
             for side in ("gold_cmp_main", "gold_cmp_final")}
assert gd_hashes["gold_cmp_main"] == gd_hashes["gold_cmp_final"], "两侧真值不同版"
(ft / "annotation_hashes.json").write_text(json.dumps({
    "frozen_csv_sha256": hashlib.sha256((ft / "FROZEN.csv").read_bytes()).hexdigest(),
    "gold_docs_sha256_both_sides": gd_hashes["gold_cmp_main"],
    "gold_docs_per_side": gd_hashes,
    "annotations": ann,
}, ensure_ascii=False, indent=2) + "\n")
print(f"OK frozen_truth: FROZEN.csv 快照 + {len(ann)}/33 注解哈希一致 + 两侧 gold_docs 同版")

build("gold_main", "gold_final", "gold33_main_final_entry_compare.json")
build("indep_main", "indep_final", "indep_main_final_entry_compare.json")