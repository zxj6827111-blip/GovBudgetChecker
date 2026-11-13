#!/usr/bin/env python3
"""M-01/02/03 修复独立验证：审核方6负控+1正控精确复现 + 真实39份回归。"""
import copy
import gzip
import importlib.util
import json
from pathlib import Path

WT = Path("/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22")
REV = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/r22_final_independent_review_codex_20261107")
OUT = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location("fa_fix", WT / "scripts/formal_acceptance_entry.py")
e = importlib.util.module_from_spec(s); s.loader.exec_module(e)

R = []
def probe(name, ok, detail=None):
    R.append({"probe": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name)

def contract():
    return {'contract_version': e.CONTRACT_VERSION, 'expected': {
        'material': {'checksum_sha256': 'a'*64},
        'generation': {'analysis_revision': 'rev-1', 'parse_run_id': 'parse-1', 'document_version_id': 7},
        'code_identity': {'rule_registry_version': 'reg-v1', 'rule_policy_version': 'pol-v1'},
        'config_identity': {'use_local_rules': True, 'use_ai_assist': False, 'mode': 'standard'}},
        'optional_fields': []}

def payload():
    checks = [{'rule_id': 'R1', 'status': 'pass', 'issue_refs': []}]
    facts = [{'rule_id': 'R1', 'sub_check_id': 'r1', 'status': 'pass',
              'formula': '基本支出10≤三公表10万元', 'operand_fact_ids': ['left-fact', 'right-fact'],
              'operand_sources': {'left': [{'physical_page': 3, 'bbox': [10,10,20,20]}],
                                  'right': [{'physical_page': 4, 'bbox': [10,10,20,20]}]}}]
    cov = {'instances': [{'obligation_id': 'OBL-1', 'status': 'completed'}]}
    return {'checksum': 'a'*64, 'analysis_revision': 'rev-1', 'use_local_rules': True,
            'use_ai_assist': False, 'mode': 'standard',
            'structured_ingest': {'parse_run_id': 'parse-1', 'document_version_id': 7},
            'result': {'rule_findings': [], 'meta': {'rule_registry_version': 'reg-v1', 'rule_policy_version': 'pol-v1',
                       'check_receipts': copy.deepcopy(checks), 'fact_receipts': copy.deepcopy(facts),
                       'obligation_coverage': copy.deepcopy(cov)}},
            'review_report': {'issues': [], 'check_receipts': copy.deepcopy(checks),
                              'fact_receipts': copy.deepcopy(facts), 'obligation_coverage': copy.deepcopy(cov)}}

# 正控
b = payload(); r = e.compare_payloads(b, copy.deepcopy(b), contract())
probe("正控 positive_intact → compared + zero_regression=True",
      r["verdict"] == "compared" and r.get("zero_regression_claim") is True, {"verdict": r["verdict"]})

# M-01 复现A
c = contract(); c['optional_fields'] = ['material.checksum_sha256']; a = payload(); a.pop('checksum')
r = e.compare_payloads(a, copy.deepcopy(a), c)
probe("M-01A 必选checksum标optional+双方缺失 → rejected",
      r["verdict"] == "rejected" and r.get("reject_reason") == "contract_invalid"
      and any(x["kind"] == "mandatory_field_marked_optional" for x in r.get("contract_errors", [])),
      {"verdict": r["verdict"], "errors": [x["kind"] for x in r.get("contract_errors", [])]})

# M-01 复现B
c = contract(); expected = c.pop('expected')
c.update(mode='cross_generation', expected_before=copy.deepcopy(expected), expected_after=copy.deepcopy(expected),
         optional_fields=['generation.analysis_revision'])
a = payload(); a.pop('analysis_revision')
r = e.compare_payloads(a, copy.deepcopy(a), c)
probe("M-01B cross_generation必选analysis_revision标optional+缺失 → rejected",
      r["verdict"] == "rejected" and any(x["kind"] == "mandatory_field_marked_optional" for x in r.get("contract_errors", [])),
      {"verdict": r["verdict"]})

# M-01 正控：解析字段标optional仍合法（既有行为保持）
c = contract(); c['optional_fields'] = ['generation.parse_run_id']; a = payload(); a['structured_ingest'].pop('parse_run_id')
r = e.compare_payloads(a, copy.deepcopy(a), c)
probe("M-01正控 解析字段optional缺键 → compared（合法旧代际行为保持）",
      r["verdict"] == "compared" and r["gates"]["before"]["optional_missing"] == ["generation.parse_run_id"],
      {"verdict": r["verdict"]})

# M-02 复现A：同关系 operand 证据清空
b = payload(); a = copy.deepcopy(b)
for container in [a['result']['meta'], a['review_report']]:
    f = container['fact_receipts'][0]; f['operand_fact_ids'] = []; f['operand_sources'] = {}; f['formula'] = None
r = e.compare_payloads(b, a, contract())
deg = [x for x in r.get("evidence_lost", []) if x["kind"] == "same_relation_evidence_degraded"]
probe("M-02A 同关系operand_fact_ids/sources/formula清空 → 登记+零回归False",
      deg and r.get("zero_regression_claim") is False
      and set(deg[0]["degraded_relations"][0]["degraded_fields"]) == {"operand_fact_ids", "operand_sources", "formula"},
      {"degraded": deg[:1], "zero": r.get("zero_regression_claim")})

# M-02 复现B：operand_sources 键删除
b = payload(); a = copy.deepcopy(b)
for container in [a['result']['meta'], a['review_report']]:
    container['fact_receipts'][0].pop('operand_sources')
r = e.compare_payloads(b, a, contract())
deg = [x for x in r.get("evidence_lost", []) if x["kind"] == "same_relation_evidence_degraded"]
probe("M-02B 同关系operand_sources键删除 → 登记+零回归False",
      deg and r.get("zero_regression_claim") is False
      and "operand_sources" in deg[0]["degraded_relations"][0]["degraded_fields"],
      {"degraded": deg[:1]})

# M-02 正控：新增诊断字段不误报（防117旧错误复活）
b = payload(); a = copy.deepcopy(b)
for container in [a['result']['meta'], a['review_report']]:
    container['fact_receipts'][0]['dh02_structured'] = {'new': '合法演进'}
    container['check_receipts'][0]['extra_diag'] = 'x'
r = e.compare_payloads(b, a, contract())
probe("M-02正控 同关系新增诊断字段 → 零丢失（不误报）",
      r["verdict"] == "compared" and not r.get("evidence_lost") and r.get("zero_regression_claim") is True,
      {"lost": r.get("evidence_lost")})

# M-03 复现A：all 新增 F2 而桶无
issue1 = {'id': 'F1', 'rule': 'R1', 'message': '发现问题一', 'severity': 'error', 'page': 3}
issue2 = {'id': 'F2', 'rule': 'R2', 'message': '仅all投影中的问题二', 'severity': 'error', 'page': 4}
b = payload(); b['result']['issues'] = {'error': [issue1], 'warn': [], 'info': [], 'all': [issue1]}
a = copy.deepcopy(b); a['result']['issues']['all'].append(issue2)
r = e.compare_payloads(b, a, contract())
probe("M-03A all新增F2而桶无 → 不得零回归（pending或登记变化）",
      r.get("zero_regression_claim") is not True
      and (r["verdict"] == "pending_review" or r.get("added_identities") or r.get("content_changed_identities")),
      {"verdict": r["verdict"], "reason": r.get("pending_reason"), "zero": r.get("zero_regression_claim")})

# M-03 复现B：损坏投影被其他空载体掩盖
b = payload(); b['result']['issues'] = {'error': [], 'warn': [], 'info': [], 'all': []}
a = copy.deepcopy(b); a['result']['issues'] = {'all': [issue2]}
r = e.compare_payloads(b, a, contract())
probe("M-03B 损坏投影+其他空载体 → pending_review+零回归False",
      r["verdict"] == "pending_review" and r.get("pending_reason") == "formal_carrier_damaged"
      and r.get("zero_regression_claim") is False,
      {"verdict": r["verdict"], "reason": r.get("pending_reason"), "damaged": r.get("damaged_carriers")})

# M-03 正控：合法旧格式缺失（rule_findings键缺失）不被当损坏
b = payload(); b['result'].pop('rule_findings'); b['review_report']['issues'] = [issue1]
r = e.compare_payloads(b, copy.deepcopy(b), contract())
probe("M-03正控 合法旧格式主载体缺失+review_report可读 → compared",
      r["verdict"] == "compared", {"verdict": r["verdict"]})

# ---- 真实39份回归：M-02/M-03 不得引入误报 ----
def load(p):
    with gzip.open(p, 'rt', encoding='utf-8') as f: return json.load(f)
def side_expected(p):
    ident = e.extract_payload_identity(p)
    return {'material': {'checksum_sha256': ident['material.checksum_sha256']['value']},
            'generation': {'analysis_revision': ident['generation.analysis_revision']['value']},
            'code_identity': {'rule_registry_version': ident['code.rule_registry_version']['value'],
                              'rule_policy_version': ident['code.rule_policy_version']['value']},
            'config_identity': {'use_local_rules': ident['config.use_local_rules']['value'],
                                'use_ai_assist': ident['config.use_ai_assist']['value'],
                                'mode': ident['config.mode']['value']}}
def contract_for(b, a):
    return {'contract_version': e.CONTRACT_VERSION, 'mode': 'cross_generation',
            'expected_before': side_expected(b), 'expected_after': side_expected(a),
            'parse_generation_basis': {'generation.parse_run_id': 'offline_replay_without_db',
                                       'generation.document_version_id': 'offline_replay_without_db'}}
new_lost = []; verdicts = {}; degraded_real = []
for md, fd in [('gold_main', 'gold_final'), ('indep_main', 'indep_final')]:
    for p in sorted((REV/md/'status').glob('*.gz')):
        sid = p.name.replace('.status.json.gz', '')
        b = load(p); a = load(REV/fd/'status'/p.name)
        r = e.compare_payloads(b, a, contract_for(b, a))
        verdicts[r['verdict']] = verdicts.get(r['verdict'], 0) + 1
        for x in r.get('evidence_lost', []):
            new_lost.append((sid, x['kind']))
            if x['kind'] == 'same_relation_evidence_degraded':
                degraded_real.append((sid, x))
probe("真实39份：证据丢失登记仍为0（M-02无新增误报）", not new_lost,
      {"new_lost": new_lost[:5], "verdicts": verdicts})
probe("真实39份：全部仍compared（M-03双向检查无新增误判）", verdicts.get('compared') == 39,
      {"verdicts": verdicts})

failed = [x['probe'] for x in R if not x['ok']]
(OUT/'boundary_fix_probes.json').write_text(json.dumps({'total': len(R), 'passed': len(R)-len(failed),
                                                      'failed': failed, 'probes': R}, ensure_ascii=False, indent=2))
print(f"\n=== {len(R)-len(failed)}/{len(R)} PASS ===")
raise SystemExit(1 if failed else 0)