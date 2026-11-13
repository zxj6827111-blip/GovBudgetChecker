#!/usr/bin/env python3
"""独立核验 ZCode 四项收口（FA-01/02/03 探针 + FA-04 口径核查）。

不复用执行方 selftest 断言；所有探针自行构造，双向验证：
非法输入必须拒绝/登记，合法输入必须放行（防止"全拒绝"假通过）。
"""
import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

WT = Path("/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22")
REVIEW = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/r22_final_independent_review_codex_20261107")
OUT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("fa_entry", WT / "scripts/formal_acceptance_entry.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

RESULTS = []


def probe(name, ok, detail=None):
    RESULTS.append({"probe": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name)


def full_contract(**over):
    c = {
        "contract_version": fa.CONTRACT_VERSION,
        "expected": {
            "material": {"checksum_sha256": "a" * 64},
            "generation": {"analysis_revision": "rev-1", "parse_run_id": "p1", "document_version_id": 7},
            "code_identity": {"rule_registry_version": "reg-v1", "rule_policy_version": "pol-v1"},
            "config_identity": {"use_local_rules": True, "use_ai_assist": False, "mode": "standard"},
        },
        "optional_fields": [],
    }
    c.update(over)
    return c


def payload(**over):
    p = {
        "checksum": "a" * 64,
        "analysis_revision": "rev-1",
        "use_local_rules": True,
        "use_ai_assist": False,
        "mode": "standard",
        "structured_ingest": {"parse_run_id": "p1", "document_version_id": 7},
        "result": {
            "meta": {"rule_registry_version": "reg-v1", "rule_policy_version": "pol-v1",
                     "obligation_coverage": {"instances": []},
                     "check_receipts": [{"rule_id": "R1", "status": "pass", "issue_refs": []}],
                     "fact_receipts": [{"rule_id": "R1", "sub_check_id": "s1", "status": "pass"}]},
            "rule_findings": [{"id": "F1", "rule": "R1", "message": "m", "page": 1}],
        },
        "review_report": {"issues": [{"id": "F1", "rule": "R1", "message": "m", "page": 1}],
                          "check_receipts": [{"rule_id": "R1", "status": "pass", "issue_refs": []}],
                          "fact_receipts": [{"rule_id": "R1", "sub_check_id": "s1", "status": "pass"}]},
    }
    p.update(over)
    return p


def clone(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


# ============ FA-01 契约完整性 ============
r = fa.compare_payloads({}, {}, {"contract_version": fa.CONTRACT_VERSION, "expected": {}})
probe("FA-01 有效版本+空expected+空双方载荷 → rejected/contract_invalid",
      r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid"
      and not r.get("zero_regression_claim", False), {"verdict": r["verdict"], "reason": r.get("reject_reason")})

r = fa.compare_payloads({}, {}, full_contract())
probe("FA-01 完整契约+空载荷 → rejected/identity_gate_failed",
      r["verdict"] == "rejected" and r.get("reject_reason") == "identity_gate_failed",
      {"verdict": r["verdict"], "reason": r.get("reject_reason")})

r = fa.compare_payloads(payload(), payload(), {"contract_version": fa.CONTRACT_VERSION, "mode": "cross_generation"})
probe("FA-01 cross_generation 省略双侧expected → rejected",
      r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid",
      {"errors": [e["kind"] for e in r.get("contract_errors", [])][:4]})

c = full_contract(mode="cross_generation")
c["expected_before"] = clone(c["expected"]); c.pop("expected")
r = fa.compare_payloads(payload(), payload(), c)
probe("FA-01 cross_generation 缺expected_after → rejected",
      r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid")

for field, bad, label in [
    (("material", "checksum_sha256"), 12345, "checksum错类型int"),
    (("material", "checksum_sha256"), "a" * 63, "checksum63位"),
    (("material", "checksum_sha256"), "g" * 64, "checksum非hex"),
    (("generation", "analysis_revision"), "", "分析代际空串"),
    (("generation", "analysis_revision"), None, "分析代际null"),
    (("config_identity", "use_local_rules"), "true", "布尔变字符串"),
    (("code_identity", "rule_policy_version"), "  ", "规则版本空白"),
]:
    c = full_contract()
    c["expected"][field[0]][field[1]] = bad
    r = fa.compare_payloads(payload(), payload(), c)
    probe(f"FA-01 必选字段非法（{label}）→ rejected",
          r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid")

c = full_contract()
del c["expected"]["generation"]["parse_run_id"]
del c["expected"]["generation"]["document_version_id"]
r = fa.compare_payloads(payload(), payload(), c)
probe("FA-01 解析代际省略且无书面依据 → rejected",
      r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid")
c["parse_generation_basis"] = {"generation.parse_run_id": "offline_replay_without_db",
                               "generation.document_version_id": "offline_replay_without_db"}
r = fa.compare_payloads(payload(), payload(), c)
probe("FA-01 解析代际书面依据后有据放行（正控）",
      r["verdict"] == "compared", {"verdict": r["verdict"]})

c = full_contract(optional_fields=["generation.parse_run_id"])
del c["expected"]["generation"]["parse_run_id"]
r = fa.compare_payloads(payload(), payload(), c)
probe("FA-01 optional引用未声明字段 → rejected",
      r["verdict"] == "rejected" and any(e["kind"] == "optional_field_undeclared" for e in r.get("contract_errors", [])))

r = fa.compare_payloads(payload(), payload(), full_contract(mode="bogus_mode"))
probe("FA-01 非法mode → rejected", r["verdict"] == "rejected")
r = fa.compare_payloads(payload(), payload(), full_contract(contract_version="r22-formal-acceptance-v0"))
probe("FA-01 错contract_version → rejected", r["verdict"] == "rejected")
r = fa.compare_payloads(payload(), payload(), full_contract(git_commit_declared="0" * 39))
probe("FA-01 git声明39位 → rejected", r["verdict"] == "rejected")

r = fa.compare_payloads(payload(), payload(), full_contract())
probe("FA-01 完整合法契约+载荷 → compared（正控，防全拒绝）",
      r["verdict"] == "compared" and r.get("zero_regression_claim") is True,
      {"verdict": r["verdict"], "zero": r.get("zero_regression_claim"), "reasons": r.get("claim_reasons")})

r = fa.compare_payloads(payload(), payload(), full_contract(git_commit_declared="0" * 40))
probe("FA-01 git只声明未核验 → compared但零回归False+原因登记",
      r["verdict"] == "compared" and r.get("zero_regression_claim") is False
      and "code_identity_declared_not_verified" in r.get("claim_reasons", []))

ti = r.get("tool_identity", {})
probe("FA-01 工具身份分账（入口+组件SHA256且互不相同）",
      ti.get("entry_script_sha256") == hashlib.sha256((WT / "scripts/formal_acceptance_entry.py").read_bytes()).hexdigest()
      and ti.get("component_script_sha256") == hashlib.sha256((WT / "scripts/verify_three_carriers.py").read_bytes()).hexdigest()
      and ti.get("entry_script_sha256") != ti.get("component_script_sha256"))

# ============ FA-02 证据丢失 ============
base = payload()
after = clone(base)
after["result"]["meta"]["check_receipts"] = []
after["review_report"]["check_receipts"] = []
after["result"]["meta"]["fact_receipts"] = []
after["review_report"]["fact_receipts"] = []
r = fa.compare_payloads(base, after, full_contract())
kinds = {(e["carrier"], e.get("copy"), e["kind"]) for e in r.get("evidence_lost", [])}
probe("FA-02 非空回执清成[] → emptied登记+零回归False",
      ("check_receipts", "result.meta", "emptied") in kinds
      and ("fact_receipts", "review_report", "emptied") in kinds
      and r.get("zero_regression_claim") is False, {"kinds": sorted(str(k) for k in kinds)})

after = clone(base)
after["result"]["meta"]["obligation_coverage"] = {}
r = fa.compare_payloads(base, after, full_contract())
probe("FA-02 coverage改{} → 登记+零回归False",
      any(e["carrier"] == "obligation_coverage" for e in r.get("evidence_lost", []))
      and r.get("zero_regression_claim") is False,
      {"lost": r.get("evidence_lost", [])[:2]})

base2 = payload()
base2["result"]["meta"]["fact_receipts"] = [{"rule_id": "R1", "sub_check_id": "s1"}, {"rule_id": "R2", "sub_check_id": "s2"}]
base2["review_report"]["fact_receipts"] = clone(base2["result"]["meta"]["fact_receipts"])
after = clone(base2)
after["result"]["meta"]["fact_receipts"] = base2["result"]["meta"]["fact_receipts"][:1]
after["review_report"]["fact_receipts"] = clone(after["result"]["meta"]["fact_receipts"])
r = fa.compare_payloads(base2, after, full_contract())
partial = [e for e in r.get("evidence_lost", []) if e["kind"] == "partial_loss"]
probe("FA-02 删一个关系 → partial_loss带丢失身份",
      partial and partial[0].get("lost_relations") == [["R2", "s2"]]
      and r.get("zero_regression_claim") is False, {"partial": partial[:1]})

after = clone(base)
after["result"]["meta"]["check_receipts"] = "corrupted"
after["review_report"]["check_receipts"] = "corrupted"
r = fa.compare_payloads(base, after, full_contract())
probe("FA-02 回执错类型 → type_changed登记",
      any(e["kind"] == "type_changed" for e in r.get("evidence_lost", []))
      and r.get("zero_regression_claim") is False)

after = clone(base)
after["result"]["meta"]["check_receipts"] = []
r = fa.compare_payloads(base, after, full_contract())
probe("FA-02 只清空一份投影 → projection_mismatch登记",
      any(e["kind"] == "projection_mismatch" for e in r.get("evidence_lost", []))
      and r.get("zero_regression_claim") is False)

base3 = payload()
base3["result"]["meta"]["obligation_coverage"] = {"instances": [{"obligation_id": "O1"}, {"obligation_id": "O2"}]}
after = clone(base3)
after["result"]["meta"]["obligation_coverage"]["instances"] = [{"obligation_id": "O1"}]
r = fa.compare_payloads(base3, after, full_contract())
dropped = [e for e in r.get("evidence_lost", []) if e["kind"] == "coverage_instances_dropped"]
probe("FA-02 删义务实例 → dropped带obligation_id",
      dropped and dropped[0].get("dropped_obligation_ids") == ["O2"]
      and r.get("zero_regression_claim") is False, {"dropped": dropped[:1]})

after = clone(base)
for container in (after["result"]["meta"], after["review_report"]):
    for receipt in container["check_receipts"]:
        receipt["dh02_structured"] = {"new_field": "合法schema演进"}
r = fa.compare_payloads(base, after, full_contract())
probe("FA-02 同关系内容增补 → 不误报丢失（68f62c5f口径正控）",
      r["verdict"] == "compared" and not r.get("evidence_lost"),
      {"lost": r.get("evidence_lost")})

# 真实 GS-2022-01 负控
with gzip.open(REVIEW / "gold_final/status/GS-2022-01.status.json.gz", "rt", encoding="utf-8") as f:
    real = json.load(f)
real_status = fa._status(real)
meta = real_status["result"]["meta"]
n_check = len(meta.get("check_receipts") or [])
n_fact = len(meta.get("fact_receipts") or [])
n_obl = len((meta.get("obligation_coverage") or {}).get("instances") or [])
ident = fa.extract_payload_identity(real)
real_contract = {
    "contract_version": fa.CONTRACT_VERSION,
    "expected": {
        "material": {"checksum_sha256": ident["material.checksum_sha256"]["value"]},
        "generation": {"analysis_revision": ident["generation.analysis_revision"]["value"]},
        "code_identity": {"rule_registry_version": ident["code.rule_registry_version"]["value"],
                          "rule_policy_version": ident["code.rule_policy_version"]["value"]},
        "config_identity": {"use_local_rules": ident["config.use_local_rules"]["value"],
                            "use_ai_assist": ident["config.use_ai_assist"]["value"],
                            "mode": ident["config.mode"]["value"]},
    },
    "parse_generation_basis": {"generation.parse_run_id": "offline_replay_without_db",
                               "generation.document_version_id": "offline_replay_without_db"},
}
r = fa.compare_payloads(real, clone(real), real_contract)
probe(f"真实GS-2022-01正控：同载荷比较compared且证据零丢失（{n_check}规则/{n_fact}关系/{n_obl}义务）",
      r["verdict"] == "compared" and not r.get("evidence_lost"),
      {"verdict": r["verdict"], "lost_n": len(r.get("evidence_lost", [])), "zero": r.get("zero_regression_claim"),
       "reasons": r.get("claim_reasons")})

cleared = clone(real)
cs = fa._status(cleared)
cm = cs["result"]["meta"]
cm["check_receipts"] = []
cm["fact_receipts"] = []
cm["obligation_coverage"] = {}
rr = cs.get("review_report") or {}
for k in ("check_receipts", "fact_receipts"):
    if k in rr:
        rr[k] = []
if "obligation_coverage" in rr:
    rr["obligation_coverage"] = {}
r = fa.compare_payloads(real, cleared, real_contract)
lost = r.get("evidence_lost", [])
lost_kinds = {(e["carrier"], e["kind"]) for e in lost}
probe("真实GS-2022-01负控：485/360/62全清空 → 逐项登记+零回归False",
      len(lost) > 0 and ("check_receipts", "emptied") in lost_kinds
      and ("fact_receipts", "emptied") in lost_kinds
      and ("obligation_coverage", "coverage_schema_lost") in lost_kinds
      and r.get("zero_regression_claim") is False,
      {"lost_n": len(lost), "kinds": sorted(str(k) for k in lost_kinds)})

# ============ FA-03 载体可读性 ============
def no_carriers():
    p = payload()
    p["result"].pop("rule_findings", None)
    p["result"].pop("issues", None)
    p["review_report"].pop("issues", None)
    return p

r = fa.compare_payloads(no_carriers(), no_carriers(), full_contract())
probe("FA-03 完整身份+三载体全缺失 → pending_review+零回归False",
      r["verdict"] == "pending_review" and r.get("pending_reason") == "formal_carriers_unreadable"
      and r.get("zero_regression_claim") is False, {"verdict": r["verdict"]})

p = no_carriers()
p["result"]["rule_findings"] = None
p["result"]["issues"] = None
p["review_report"]["issues"] = None
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 三载体全null → pending_review",
      r["verdict"] == "pending_review" and r.get("zero_regression_claim") is False)

p = no_carriers()
p["result"]["rule_findings"] = "broken"
p["result"]["issues"] = 42
p["review_report"]["issues"] = False
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 三载体全错类型 → pending_review",
      r["verdict"] == "pending_review" and r.get("zero_regression_claim") is False)

p = payload()
p["result"].pop("rule_findings")
p["review_report"].pop("issues")
p["result"]["issues"] = {"all": [{"id": "X", "rule": "R", "message": "m"}]}
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 dict只有all投影非空 → pending_review/unreadable_projection",
      r["verdict"] == "pending_review" and r.get("zero_regression_claim") is False)

p = payload()
p["result"].pop("rule_findings")
p["review_report"].pop("issues")
p["result"]["issues"] = {"error": [{"id": "X", "rule": "R", "message": "m"}], "warn": [], "info": [], "all": []}
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 严重度桶有内容而all被清空 → pending_review",
      r["verdict"] == "pending_review" and r.get("zero_regression_claim") is False)

p = payload()
p["result"].pop("rule_findings")
p["review_report"].pop("issues")
p["result"]["issues"] = {"error": [{"id": "X", "rule": "R", "message": "m"}], "warn": [], "info": [],
                         "all": [{"id": "Y", "rule": "R", "message": "different"}]}
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 all与严重度桶内容冲突 → pending_review",
      r["verdict"] == "pending_review" and r.get("zero_regression_claim") is False)

p = payload()
p["result"]["rule_findings"] = []
p["review_report"]["issues"] = []
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 合法空[] → compared（正控，防全拒绝）",
      r["verdict"] == "compared", {"verdict": r["verdict"]})

p = payload()
p["result"].pop("rule_findings")
r = fa.compare_payloads(p, clone(p), full_contract())
probe("FA-03 旧格式主载体缺失但review_report可读 → compared（合法旧格式正控）",
      r["verdict"] == "compared", {"verdict": r["verdict"]})

# ============ 汇总 ============
failed = [x["probe"] for x in RESULTS if not x["ok"]]
summary = {"total": len(RESULTS), "passed": len(RESULTS) - len(failed), "failed": failed,
           "entry_sha256": hashlib.sha256((WT / "scripts/formal_acceptance_entry.py").read_bytes()).hexdigest(),
           "component_sha256": hashlib.sha256((WT / "scripts/verify_three_carriers.py").read_bytes()).hexdigest()}
(OUT / "independent_probes.json").write_text(json.dumps({"summary": summary, "probes": RESULTS}, ensure_ascii=False, indent=2))
print(f"\n=== {summary['passed']}/{summary['total']} PASS ===")
sys.exit(1 if failed else 0)