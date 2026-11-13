"""只读复验第三轮整改证据；不改验收预期、原件和历史输出。"""
from pathlib import Path
import copy
import gzip
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).parent
OLD = OUT.parent / 'codex_qoder_verification_20261026_round3'
from scripts.verify_intake_remediation import _bound_ok, _reason_codes
from tests.test_native_geometry_candidate import _income_candidate, _income_original_page
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import _amount_coverage_preference, resolve_native_table_candidates


def rows(path):
    with gzip.open(path, 'rt') as stream:
        return {r['file']: r for line in stream if (r := json.loads(line))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


comparison = {}
for stage in ('intake', 'parse', 'e2e', 't0'):
    before = rows(OLD / 'live_current' / (stage + '.jsonl.gz'))
    after = rows(OUT / 'live_current' / (stage + '.jsonl.gz'))
    assert before.keys() == after.keys()
    differences = []
    for name, row in after.items():
        assert not row.get('error'), row
        keys = sorted(k for k in row.keys() | before[name].keys()
                      if k not in {'parse_seconds', 'seconds'} and row.get(k) != before[name].get(k))
        if keys:
            differences.append({'file': name, 'fields': keys})
    comparison[stage] = {'count': len(after), 'errors': [], 'differences_from_round3': differences}
    if stage != 'parse':
        assert not differences, comparison[stage]
assert len(comparison['parse']['differences_from_round3']) == 1
assert '材料工程学校' in comparison['parse']['differences_from_round3'][0]['file']
(OUT / 'live_comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2))

cases_path = ROOT / 'scripts/intake_remediation_cases.json'
cases = json.loads(cases_path.read_text())
case = next(c for c in cases['parse'] if '材料工程学校' in c.get('file', ''))
material = rows(OUT / 'live_current/parse.jsonl.gz')[case['file']]
c7 = {'case': case, 'admitted': material['admitted'], 'pending': material['pending'],
      'fact_count': material['fact_count'], 'page14_matrix': next(t for t in material['fragments'] if t[0] == 14),
      'source_sha256': material['sha256'],
      'passed': material['admitted'] >= case['admitted_min'] and material['pending'] <= case['pending_max']}
assert c7['passed'] and c7['pending'] == 3
assert c7['page14_matrix'][2:4] == [5, 25]
(OUT / 'c7_material_school.json').write_text(json.dumps(c7, ensure_ascii=False, indent=2))

boundaries = []
for delta in (0.0, .1):
    for reverse in (False, True):
        full = _income_candidate(drop_last=False, blank_last_cell=True)
        lost = _income_candidate(drop_last=True, blank_last_cell=False, profile='snap6')
        page = _income_original_page(full)
        lost.bbox = (*lost.bbox[:3], lost.bbox[3] + delta)
        first, second = (lost, full) if reverse else (full, lost)
        preference = _amount_coverage_preference(first, second, page)
        chosen = resolve_native_table_candidates(PDFParser(None), [first], [second], page=page)[0]
        assert chosen.fragment_uid == 'full'
        assert preference is not None
        boundaries.append({'bbox_delta': delta, 'reverse': reverse, 'selected': chosen.fragment_uid,
                           'coverage_comparison_executed': True})
(OUT / 'boundary_probes.json').write_text(json.dumps(boundaries, ensure_ascii=False, indent=2))

identity = {}
for name, expected in (('result', 'failed'), ('valid', 'applied'), ('missing', 'failed')):
    proof = json.loads((OUT / f'identity_pipeline_{name}.json').read_text())
    assert not proof['pipeline_error']
    overlay = proof['fact_overlay']
    assert overlay['status'] == expected
    if expected == 'failed':
        assert 'correction_overlay_incomplete' in {r['code'] for r in proof['review_reasons']}
    identity[name] = {'status': overlay['status'], 'identity': overlay['table_identity_overlay']}
(OUT / 'identity_summary.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2))

archive = ROOT / 'outputs/intake_remediation_20261024/cases_archive/intake_remediation_cases_2026-10-25-v1.3.json'
assert sha(archive) == 'd184f0f118dae3d4deadb9487295fce9ea08aa571d83d40a2a37c016957e31a0'
assert sha(cases_path) == '922c25337bb03ed8b320358823df7e5382298679f33402f3853ab50b9e903680'
row = next(r for r in rows(OUT / 'live_current/intake.jsonl.gz').values() if '发展研究中心（本级）' in r['file'])
expect = next(c for c in cases['org_binding']['binding_boundary_cases'] if c['file'] == row['file'])
old_expect = next(c for c in json.loads(archive.read_text())['org_binding']['binding_boundary_cases'] if c['file'] == row['file'])
wrong_reason = copy.deepcopy(row)
wrong_reason['binding_decision']['reasons'] = [{'code': 'insufficient_evidence'}]
wrong_parent = copy.deepcopy(row)
wrong_parent['bound'] = expect['not'][0]
r4 = {'current_sha256': sha(cases_path), 'archive_sha256': sha(archive),
      'v14_ok': bool(_bound_ok(row, expect)), 'v13_ok': bool(_bound_ok(row, old_expect)),
      'wrong_reason_ok': bool(_bound_ok(wrong_reason, expect)), 'wrong_parent_ok': bool(_bound_ok(wrong_parent, expect)),
      'reason_codes': _reason_codes(row)}
assert r4['v14_ok'] and not any(r4[k] for k in ('v13_ok', 'wrong_reason_ok', 'wrong_parent_ok'))
(OUT / 'r4_integrity.json').write_text(json.dumps(r4, ensure_ascii=False, indent=2))

before = json.loads((OUT / 'source_before.json').read_text())
changed = [name for name, digest in before.items() if sha(ROOT / name) != digest]
expected_changes = {'src/services/pdf_parser.py', 'src/services/source_reconstruction.py',
                    'src/services/manual_cell_overlay.py', 'tests/test_native_geometry_candidate.py',
                    'tests/remediation/test_manual_cell_fill.py'}
assert set(changed) == expected_changes, changed
frozen = json.loads((OLD / 'source_before.json').read_text())['files']
frozen_changes = [name for name, digest in frozen.items() if sha(ROOT / name) != digest]
assert set(frozen_changes) == expected_changes, frozen_changes
integrity = {'changed_since_start': changed, 'changed_since_round3_freeze': frozen_changes,
             'current_case_sha256': sha(cases_path), 'archive_sha256': sha(archive),
             'organization_sha256': sha(ROOT / 'outputs/intake_remediation_20261024/org_cleanup/cleaned/organizations.json')}
old_meta = json.loads((OLD / 'live_current/meta.json').read_text())
assert integrity['organization_sha256'] == old_meta['org_directory_sha256']
integrity['production_organization_sha256'] = sha(ROOT.parents[2] / 'data/organizations.json')
assert integrity['production_organization_sha256'] == 'd1e32bbd84777407896cce884f4c08b93a21f53c209f9bca946f15a0d9f202cf'
(OUT / 'integrity.json').write_text(json.dumps(integrity, ensure_ascii=False, indent=2))
print(json.dumps({'c7': c7['passed'], 'boundary_probes': len(boundaries), 'identity': {k: v['status'] for k, v in identity.items()},
                  'r4': r4['v14_ok'], 'changed_files': changed}, ensure_ascii=False))
