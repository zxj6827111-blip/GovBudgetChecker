"""只读冻结采集，复制后变异；默认在新目录重放，禁止覆盖已有审核证据。"""
from pathlib import Path
import argparse
import gzip
import json
import os
import shutil
import subprocess

REPO = Path('/Volumes/workSSD/software development/GovBudgetChecker')
EVIDENCE = Path('/Volumes/workSSD/software development/gbc-worktrees/joint-execution-20261103-evidence')
DEFAULT_TOOL = Path('/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103/scripts/batch2_gold_compare.py')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--tool', type=Path, default=DEFAULT_TOOL)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    base = EVIDENCE / 's4b/gold/final'
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1')
    cmd = [str(REPO/'.venv/bin/python'), str(args.tool), '--mode', 'stage', '--stage', 'S4',
           '--baseline-run', str(EVIDENCE/'s5b/gold/run2'),
           '--baseline-analysis', str(EVIDENCE/'s5b/gold/run2_analysis'),
           '--targets', str(EVIDENCE/'s4b/targets_residual.csv'),
           '--contract', str(EVIDENCE/'s4b/contract_residual.json')]
    results = []
    for label in ['normal', 'unknown_pre_group', 'new_target_downgraded_to_P']:
        run = base
        if label != 'normal':
            run = args.out/'mutants'/label
            for sub in ['status', 'todo']:
                shutil.copytree(base/sub, run/sub)
            sid = 'GS-2022-01' if label == 'unknown_pre_group' else 'GS-2022-04'
            path = run/'status'/f'{sid}.status.json.gz'
            with gzip.open(path, 'rt') as f:
                status = json.load(f)
            if label == 'unknown_pre_group':
                issue = status['review_report']['issues'][0]
                ident, rule = issue['id'], issue.get('rule_id') or 'CMM-013'
                def mutate(v):
                    if isinstance(v, dict):
                        if v.get('id') == ident and isinstance(v.get('location'), dict):
                            v['location']['pre_group_issue_id'] = rule + '-999999'
                        for value in v.values():
                            mutate(value)
                    elif isinstance(v, list):
                        for value in v:
                            mutate(value)
                mutate(status)
            else:
                issues = status['review_report']['issues']
                issue = next(v for v in issues if v['id'] == 'V33-227-9')
                issues.remove(issue)
                issue['evidence_status'] = 'incomplete_rule_warning'
                issue['evidence_verification'] = {
                    'status': 'failed', 'source_identity_complete': False,
                    'reasons': ['independent reject probe'],
                }
                status['review_report']['manual_review'].append({
                    'id': issue['id'], 'record_type': 'finding_review_candidate',
                    'code': 'V33-227', 'display_title': issue['title'], 'finding': issue,
                })
                for entries in status.get('result', {}).get('issues', {}).values():
                    if isinstance(entries, list):
                        for obj in entries:
                            if isinstance(obj, dict) and obj.get('id') == 'V33-227-9':
                                obj['evidence_status'] = issue['evidence_status']
                                obj['evidence_verification'] = issue['evidence_verification']
            with gzip.open(path, 'wt') as f:
                json.dump(status, f, ensure_ascii=False)
        dest = args.out/'compare'/label
        proc = subprocess.run(cmd+['--run', str(run), '--out', str(dest)],
                              env=env, capture_output=True, text=True)
        dest.mkdir(parents=True, exist_ok=True)
        (dest/'stdout.log').write_text(proc.stdout)
        (dest/'stderr.log').write_text(proc.stderr)
        report = json.loads((dest/'batch2_gold_compare.json').read_text())
        results.append({'probe': label, 'exit': proc.returncode, 'overall': report['overall'],
                        'checks': {c['id']: c['verdict'] for c in report['checks']},
                        'satisfied_miss_targets': report['satisfied_miss_targets']})
    (args.out/'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
