import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
SOURCE = Path.cwd()
OUT = Path('/private/tmp/gbc-r7-results-20261111')
CODE = '7a4e6de1084d0d9af7ed341173f597fd2564e32f'
DOCS = 'eea723b234acfffd6a8afa3bbb5324826f66fbaa'
sys.path.insert(0, str(SOURCE))
from src.schemas.parse_artifact import ParseArtifact
from scripts import d1_table_reconciliation as d1

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])

def sha(content):
    return hashlib.sha256(content).hexdigest()

all_results = {}
for prefix in ['6000a11c', '075e31ba', '8d2bbe5f', '89427638']:
    artifact = ParseArtifact.from_dict(json.loads((OUT / (prefix + '_artifact.json')).read_text()))
    pdf = next((ROOT / 'uploads').glob(prefix + '*/*.pdf'))
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=artifact):
        value = d1.reconcile_auto(str(pdf))
    all_results[prefix] = value
    print('D1', prefix, {k: value[k] for k in ['instances', 'pass', 'gap']}, flush=True)
save('d1_four_originals.json', all_results)

replay = []
for prefix in ['6000a11c', '0b507c', '0a8a27', '34bb0eeb', '075e31ba', '8d2bbe5f', '89427638']:
    current = json.loads((OUT / (prefix + '_new_result.json')).read_text())
    previous = json.loads((Path('/private/tmp/gbc-r6-results-20261111') / (prefix + '_new_result.json')).read_text())
    def fingerprints(data):
        return Counter((x.get('rule_id'), (x.get('location') or {}).get('page'), x.get('message')) for x in data['payload']['issues']['all'])
    issues = current['payload']['issues']['all']
    row = {'prefix': prefix, 'source': current['source'], 'sha256': current['source_hash'],
           'source_unchanged_after': sha(Path(current['source']).read_bytes()) == current['source_hash'],
           'source_equals_previous': current['source_hash'] == previous['source_hash'],
           'findings': len(issues),
           'verified': sum((x.get('evidence_verification') or {}).get('status') == 'verified' for x in issues),
           'issue_fingerprints_equal_previous': fingerprints(current) == fingerprints(previous),
           'rule_execution_summary': current['payload'].get('rule_execution_summary', {})}
    replay.append(row)
    print('REPLAY', prefix, row['findings'], row['verified'], row['issue_fingerprints_equal_previous'], flush=True)
save('original_replay_comparison.json', replay)

receipt = json.loads((SOURCE / 'docs/D4_真实链路验收回执_R6_20261111.json').read_text())
keys = json.loads((SOURCE / 'docs/R6_关键源码sha256_20261111.json').read_text())
def validate_map(mapping):
    return {name: {'declared': digest, 'actual_archive': sha((SOURCE / name).read_bytes()),
                   'actual_code_commit': sha(git('show', CODE + ':' + name)),
                   'ok': sha((SOURCE / name).read_bytes()) == digest == sha(git('show', CODE + ':' + name))}
            for name, digest in mapping.items()}
print('D4 top keys', list(receipt), flush=True)
receipt_map = receipt['frozen']['key_source_sha256']
receipt_head = receipt['frozen']['git_head']
changed = git('diff', '--name-only', '-z', '6380eeed', CODE).decode().strip('\0').split('\0')
changed_code = [x for x in changed if not x.startswith('docs/')]
unchanged = {}
for path in ['scripts/d4_real_link_acceptance.py', 'api/main.py', 'api/runtime.py',
             'api/routes/jobs.py', 'api/routes/reports.py', 'src/services/report_confirmation_gate.py',
             'src/services/pdf_parser.py', 'src/services/evidence_guard.py', 'src/engine/issue_evidence.py',
             'src/engine/pipeline.py', 'api/routes/analyze.py', 'api/routes/reviews.py',
             'src/services/rule_source_binding.py']:
    if (SOURCE / path).is_file():
        unchanged[path] = {'sha256': sha((SOURCE / path).read_bytes()),
                           'same_as_prior_independent_8e948c7a': (SOURCE / path).read_bytes() == git('show', '8e948c7a:' + path)}
audit = {'code_commit': CODE, 'docs_commit': DOCS,
         'independently_rerun_this_round': False,
         'scope': 'Read-only receipt/hash audit. SELF_PASS is not a current-round independent D4 acceptance.',
         'prior_independent_review': str(ROOT / 'outputs/independent_8e948c7a_20261111'),
         'change_baseline': '6380eeed',
         'receipt_run_id': receipt['run_id'], 'receipt_git_head': receipt_head,
         'receipt_head_matches_code': receipt_head == CODE,
         'receipt_original_hash_matches_replay': receipt['frozen']['original_sha256'] == replay[0]['sha256'],
         'receipt_step_count': len(receipt['steps']), 'receipt_all_steps_self_pass': all(s['ok'] for s in receipt['steps']),
         'receipt_cleanup': receipt.get('cleanup'),
         'receipt_source_hash_validation': validate_map(receipt_map),
         'key_source_hash_validation': validate_map(keys['source_sha256']),
         'all_non_doc_changes_since_previous_independent_review': changed_code,
         'unchanged_files': unchanged}
save('d4_receipt_and_source_audit.json', audit)
print('D4 receipt head', receipt_head, 'steps', len(receipt['steps']), 'hashes', len(receipt_map), flush=True)

status = git('status', '--short', '--untracked-files=no').decode()
save('workspace_integrity.json', {'baseline_tracked_status': (OUT / 'baseline_root_status.txt').read_text(),
                                'after_tracked_status': status,
                                'tracked_status_unchanged': status == (OUT / 'baseline_root_status.txt').read_text(),
                                'source_archive_head': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
                                'source_tracked_status': subprocess.check_output(['git', 'status', '--short', '--untracked-files=no']).decode()})
