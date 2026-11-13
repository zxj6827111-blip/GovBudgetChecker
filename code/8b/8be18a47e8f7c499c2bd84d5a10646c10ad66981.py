"""IR3-GAP-02 第二轮候选审计：只读重放封存输入、历史门禁、旧证据封印与新候选身份。

原 audit_candidate.py 保留原字节（其身份断言绑定 9e66 候选，不能对新 HEAD 运行）。
本脚本沿用其检查口径：候选 6bddb8af，基点 9e66caae；封存输入只读重放，不改历史目录。
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1]
MAIN = Path('/Volumes/workSSD/software development/GovBudgetChecker')
C = Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
OLD64 = MAIN / 'outputs/independent_ir3_9631ea55_20261106'
OLD8 = MAIN / 'outputs/independent_ir3_d71c9ad7_20261106'
PREV_EV = MAIN / 'outputs/independent_ir3_9e66caae_20261106'
D = C / 'outputs/ir3_gap02_fix_20261106'
CAP = C / 'outputs/ir3_residual_fix_20261106'
sys.path.insert(0, str(C))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
from scripts.verify_pending_review import code_fingerprint

HEAD = '6bddb8afa2188185e30ff3beee1d4759e3280910'
BASE = '9e66caae33edadd7a59e0b651f3be10b31fc3029'


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=C)


def check_hashes(root, files):
    return [p for p, s in files.items() if not (root / p).is_file() or h.sha(root / p) != s]


def verify_sums(root):
    bad = []
    count = 0
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        recorded, name = line.split('  ', 1)
        target = root / name
        count += 1
        if not target.is_file() or h.sha(target) != recorded:
            bad.append(name)
    return {'root': str(root), 'entries': count, 'mismatches': bad}


# 1. 候选身份与补丁一致性。
head = git('rev-parse', 'HEAD').decode().strip()
assert head == HEAD, head
identity = {
    'candidate_sha': head,
    'base_sha': BASE,
    'working_tree': git('status', '--porcelain=v1').decode(),
    'main_sha': git('rev-parse', 'main').decode().strip(),
    'changed_files': git('diff', '--name-only', BASE, head).decode().splitlines(),
    'patch_matches': git('diff', '--binary', BASE, head) == (OUT / 'source_changes.patch').read_bytes(),
}

# 2. 旧交付与旧独立证据封印不变。
manifest = h.read(D / 'review_manifest.json')['files']
protected = h.read(D / 'protected_input_hashes.json')
integrity = {}
for name, r in protected.items():
    root = Path(r['root'])
    actual = {str(p.relative_to(root)): h.sha(p) for p in root.rglob('*') if p.is_file()}
    integrity[name] = {'files': len(actual), 'changed': check_hashes(root, r['files']),
                       'added': sorted(actual.keys() - r['files'].keys())}
integrity['gap02_delivery_manifest'] = {
    'files': len(manifest), 'mismatches': check_hashes(D, manifest)}
integrity['independent_evidence_seals'] = [
    verify_sums(OLD64), verify_sums(OLD8), verify_sums(PREV_EV)]

# 3. 封存 64+8 组输入只读重放；当前离线验证器仅替换 fact_source 模块。
modules = {}
sources = {}
for label, rev in [('963_module', '9631ea55decc8e5b94531e483a5d907842be292a'),
                   ('d71_module', 'd71c9ad7eb671ff956b2fcf21d814a5e9c0cc640'),
                   ('9e66_module', BASE)]:
    source = git('show', rev + ':scripts/effect_fact_sources.py')
    mod = types.ModuleType('scripts.effect_fact_sources')
    exec(compile(source, rev, 'exec'), mod.__dict__)
    modules[label] = mod
    sources[label] = {'commit': rev, 'file_sha256': hashlib.sha256(source).hexdigest()}
modules['candidate_module'] = current
sources['candidate_module'] = {'commit': head, 'file_sha256': h.sha(C / 'scripts/effect_fact_sources.py')}


def replay(rows):
    results = []
    try:
        for name, folder, want in rows:
            evp = folder / 'external-evidence.json'
            ev = h.load_evidence(evp, h.sha(evp))
            versions = {}
            for label, module in modules.items():
                sys.modules['scripts.effect_fact_sources'] = module
                r = h.compare_collections(folder / 'before', folder / 'after', folder / 'kit', ev)
                versions[label] = {'overall': r['overall'], 'problems': r['problems'], 'pending': r['pending']}
            results.append({'case': name, 'expected': want,
                            'expectation_met': versions['candidate_module']['overall'] == want,
                            'unchanged_original_directory': str(folder),
                            'versions': versions})
    finally:
        sys.modules['scripts.effect_fact_sources'] = current
    return results


old64 = []
for n in ['effect_boundary_controls', 'extra_effect', 'neighbor_effect']:
    old64.extend((r['case'], OLD64 / 'control_cases' / r['case'], r['expected'])
                 for r in h.read(OLD64 / f'receipts/{n}.json'))
replay64 = replay(old64)
replay8 = replay(
    [(r['case'], OLD8 / 'masking_cases' / r['case'], r['expected'])
     for r in h.read(OLD8 / 'receipts/masking_boundary.json')]
    + [('legitimate_hyphen_row_label_positive', OLD8 / 'control_cases/legitimate_hyphen_row_label_positive', 'PASS'),
       ('exact_same_pdf_row_mask', OLD8 / 'masking_cases/exact_same_pdf_row_mask', 'FAIL')])
for name, rows in [('same_original_inputs64.json', replay64), ('same_original_masking_inputs8.json', replay8)]:
    dump(name, {'scope': 'Read-only replay of original sealed inputs; current offline verifier with only fact_source module exchanged; no historical full-service run.',
                'sources': sources, 'cases': rows})

# 4. 历史门禁：仅离线比较，9631 保存分析不重采。
proof = MAIN / 'outputs/unified_independent_review_r2_20261106/r05_independent_evidence_v2.json'
ev = h.load_evidence(proof, 'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092')
gates = []
for name, before, after, evidence in [
        ('original_to_963_unapproved', C / 'outputs/unified_remediation_20261106/final_d1', CAP / 'final_candidate', {}),
        ('R05_original_binding_only', C / 'outputs/unified_remediation_20261106/final_d1', C / 'outputs/unified_review_remediation_20261106/final_collection', ev),
        ('regression_342_to_963', C / 'outputs/unified_review_remediation_r3_20261106/final_candidate', CAP / 'final_candidate', {}),
        ('regression_R2_to_963', C / 'outputs/unified_review_remediation_20261106/final_collection', CAP / 'final_candidate', {})]:
    r = h.compare_collections(before, after, CAP / 'kit_candidate', evidence)
    r['identity'] = {'gate_code': head, 'before': str(before), 'after': str(after),
                     'after_meta_sha256': h.sha(after / 'meta.json'), 'new_analysis': False}
    dump(name + '.json', r)
    gates.append({'name': name, 'overall': r['overall'], 'refused': len(r['problems']),
                  'pending': len(r['pending'])})

# 5. 9631 原代际上下文保留核验 + 指纹差分（不虚构新采集）。
contexts = []
meta = h.read(CAP / 'final_candidate/meta.json')
previous = meta['code']['files']
now = code_fingerprint(C)['files']
delta = {p: {'previous': previous.get(p), 'current': now.get(p)}
         for p in now.keys() | previous.keys() if now.get(p) != previous.get(p)}
for r in h.read(D / 'analysis_unchanged.json')['contexts31']:
    p = Path(r['source'])
    fc = h.read(p)
    contexts.append({**r, 'hash_matches': h.sha(p) == r['sha256'],
                     'fact_count_matches': len(fc['facts']) == r['facts'],
                     'context_keys': list(fc)})
analysis = {'new_analysis_run': False, 'retained_generation': '9631ea55decc8e5b94531e483a5d907842be292a',
            'fingerprint_delta': delta, 'profile': meta['profile'], 'contexts': contexts}
dump('analysis_generation_check.json', analysis)

# 6. 三份 UNAPPROVED 清单逐字保留。
pending = []
for name, r in h.read(D / 'pending_registries.json').items():
    pending.append({'name': name, 'source': r['source'], 'sha256': r['sha256'],
                    'matches': h.sha(Path(r['source'])) == h.sha(Path(r['copy'])) == r['sha256'],
                    'count': r['count'], 'approval': 'UNAPPROVED'})

# 7. 全量与聚焦测试记录。
xml = ET.parse(OUT / 'full_suite.xml').getroot()
cases = xml.findall('.//testcase')
skips = [{'test': e.get('classname') + '::' + e.get('name'), 'message': e.find('skipped').get('message'),
          'detail': e.find('skipped').text} for e in cases if e.find('skipped') is not None]
prior_skips = h.read(D / 'skipped196.json')['cases']
tests = {'full_suite': {'cases': len(cases),
                        'passed': sum(not any(e.find(t) is not None for t in ['skipped', 'failure', 'error']) for e in cases),
                        'skipped': len(skips), 'failures': len(xml.findall('.//failure')),
                        'errors': len(xml.findall('.//error')),
                        'skip_list_matches_prior_196': skips == prior_skips,
                        'new_boundary_tests_not_skipped': all(
                            'test_effect_row_label_boundary' not in s['test'] for s in skips)},
         'focused321_log_sha256': h.sha(OUT / 'focused321.log'),
         'mypy_log_sha256': h.sha(OUT / 'mypy.log'),
         'ruff_log_sha256': h.sha(OUT / 'ruff.log')}

summary = {'head': head, 'base': BASE, 'identity': identity, 'protected': integrity,
           'same_original64': [len(replay64), sum(r['expectation_met'] for r in replay64)],
           'same_original_masking8': [len(replay8), sum(r['expectation_met'] for r in replay8)],
           'fingerprint_delta': list(delta), 'contexts': len(contexts),
           'contexts_all_hash_matched': all(c['hash_matches'] and c['fact_count_matches'] for c in contexts),
           'pending_registries': pending, 'gates': gates, 'tests': tests}
dump('identity_and_integrity.json', summary)
dump('delivery_input_snapshot.json', {str(p.relative_to(OUT)): h.sha(p) for p in OUT.rglob('*') if p.is_file()})
print(json.dumps({k: summary[k] for k in ['head', 'same_original64', 'same_original_masking8', 'gates',
                                          'contexts', 'contexts_all_hash_matched', 'fingerprint_delta',
                                          'pending_registries']},
                 ensure_ascii=False, indent=2))
