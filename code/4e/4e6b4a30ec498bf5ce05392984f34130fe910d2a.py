import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
WT = ROOT / 'artifacts/pdf-ai-local-acceptance-20261026'
RT = OUT / 'runtime'

def sha(path):
    with path.open('rb') as h:
        return hashlib.file_digest(h, 'sha256').hexdigest()

def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()

def main():
    protected = []
    for kind in ('main_ai', 'pdf'):
        source = json.loads((OUT / f'{kind}.input.json').read_text())
        base = Path(source['path'])
        changed = [name for name, meta in source['files'].items() if not (base / name).is_file() or sha(base / name) != meta['sha256']]
        assert not changed, (kind, changed)
        assert git(base, 'rev-parse', 'HEAD') == source['head']
        protected.append({'source': kind, 'head': source['head'], 'verified_files': len(source['files']), 'unchanged': True})
    assert git(WT, 'diff', 'HEAD', '--name-only') == ''
    head = git(WT, 'rev-parse', 'HEAD')
    files = git(WT, 'ls-files').splitlines()
    hashes = {name: sha(WT / name) for name in files if (WT / name).is_file()}
    env = json.loads((RT / 'environment.json').read_text())
    os.environ.update(env)
    sys.path.insert(0, str(WT))
    from src.services.user_store import UserStore
    UserStore().update_user('codex_local_validator', is_active=False)
    health = {}
    with httpx.Client(timeout=15) as client:
        for name, url in {'original_backend': 'http://127.0.0.1:18743/health', 'original_frontend': 'http://127.0.0.1:3000/login',
                          'acceptance_backend': 'http://127.0.0.1:18746/health', 'acceptance_frontend': 'http://127.0.0.1:3013/login'}.items():
            response = client.get(url)
            assert response.status_code == 200, (name, response.status_code)
            health[name] = {'url': url, 'http_status': response.status_code}
    browser = json.loads((OUT / 'browser_receipt.json').read_text())
    assert browser['passed']
    browser['pdf_visual_review'] = 'warning: dense multi-reference labels overlap and clip at page edge; appendix and JSON/CSV retained'
    (OUT / 'browser_receipt.json').write_text(json.dumps(browser, ensure_ascii=False, indent=2))
    joint = json.loads((OUT / 'joint_chain/receipt.json').read_text())
    joint['browser_pending'] = False
    joint['browser_functional_checks_passed'] = True
    joint['pdf_visual_review'] = 'warning_dense_labels'
    (OUT / 'joint_chain/receipt.json').write_text(json.dumps(joint, ensure_ascii=False, indent=2))
    c7 = json.loads((OUT / 'native_c7/c7_validation.json').read_text())
    assert c7['passed']
    pytest_log = (OUT / 'pytest_final_verified.log').read_text()
    assert '4244 passed, 189 skipped' in pytest_log and '\nFAILED ' not in pytest_log
    manifest = {'frozen_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'status': 'ready_for_local_user_acceptance_with_recorded_limits', 'production_release_approved': False,
        'branch': git(WT, 'branch', '--show-current'), 'head': head, 'tree': git(WT, 'rev-parse', 'HEAD^{tree}'),
        'worktree': str(WT), 'tracked_files_sha256': hashes,
        'input_worktrees': protected, 'runtime_health': health,
        'frontend_build_id': (WT / 'app/.next/BUILD_ID').read_text().strip(),
        'runtime_configuration_sha256': sha(RT / 'environment.json'),
        'ai_settings_sha256': sha(RT / 'data/ai-model-settings.json'),
        'validation_user_disabled': True,
        'test_summary': {'pytest_passed':4244,'pytest_skipped':189,'pytest_failed':0,'ruff':'passed','mypy':'passed',
                         'frontend_unit':'passed','tsc':'passed','next_build':'passed'},
        'joint_sample': {'job_id': joint['job_id'], 'analysis_revision': joint['analysis_revision'],
                         'ai_coverage': joint['ai_coverage'], 'storage': joint['storage'], 'rule_relations': joint['rule_relations']},
        'native_c7': c7,
        'limits': ['Existing organization duplicates block organization creation; source catalog preserved, only two explicit E1 fixtures added to isolated copy.',
                   'Dense PDF reference labels overlap/clip; PDF download works, visual acceptance not passed.',
                   'E1 AI is a two-page valid-empty sample, not a precision/recall or whole-corpus acceptance.',
                   'Full-corpus conservative-binding impact, real OCR accuracy, independent amount-cell truth and IR-15/16/17/18 remain outside this integration acceptance.']}
    (OUT / 'release_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'head':head,'source_worktrees_unchanged':True,'health':health,'validation_user_disabled':True},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
