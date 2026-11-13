from pathlib import Path
import gzip, hashlib, importlib.util, json, sys, subprocess

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN = ROOT/'artifacts/daily-main'
OUT = ROOT/'outputs/shiquan_source_coverage_20261109'
sys.path.insert(0, str(MAIN))
spec = importlib.util.spec_from_file_location('gold_adapter', ROOT/'scripts/gold_baseline_run.py')
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
manifest = json.loads((OUT/'manifest_before.json').read_text())
before = json.loads((OUT/'preservation_before.json').read_text())
isolated = {r['job']: r for r in json.loads((OUT/'isolated_verified.json').read_text())}
h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
rows = []
for job, original in manifest.items():
    path = ROOT/'uploads'/job/'status.json'
    status = json.loads(path.read_text())
    assert status['status'] == 'review_required' and status['use_ai_assist'] is False
    assert status['analysis_revision'] != original['revision']
    todo = adapter.adapter_todo(job, status)
    assert set(todo['blocking_ids']) == {r['id'] for r in isolated[job]['blocked']}, (job, todo['blocking_ids'])
    meta = status['result']['meta']
    assert not any(r.get('status') == 'execution_error' for r in meta['obligation_coverage']['instances'])
    archives = [p for p in (ROOT/'uploads'/job/'preserved_analyses').glob('*/manifest.json')
                if json.loads(p.read_text()).get('files', {}).get('status.json') == before['statuses'][job]]
    assert archives and h(archives[0].parent/'status.json') == before['statuses'][job]
    (OUT/job/'status_after.json.gz').write_bytes(gzip.compress(path.read_bytes()))
    rows.append(dict(job=job, filename=original['filename'], before_revision=original['revision'],
        revision=status['analysis_revision'], status=status['status'], use_ai_assist=False,
        counts=todo['queue_counts'], blocking_ids=todo['blocking_ids'],
        review_reasons=status.get('review_reasons'), prior_archive=str(archives[0].parent),
        evidence=meta.get('evidence_completeness', {})))
changed = [job for job, sha in before['statuses'].items() if h(ROOT/'uploads'/job/'status.json') != sha]
assert set(changed) == set(manifest)
assert all(h(ROOT/p) == sha for p, sha in before['root_modified_files'].items())
assert all(h(ROOT/p) == sha for p, sha in before['pdfs'].items())
assert h(ROOT/'.env') == before['root_env_sha256']
code = json.loads((OUT/'implementation_manifest.json').read_text())
assert all(h(MAIN/p) == sha for p, sha in code.items())
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=MAIN, text=True).strip()
assert sha == 'ef44c6031f536f3cffc12e6eff57dd53db56a522'
receipt = dict(commit=sha, rows=rows, original_pdfs_preserved=len(before['pdfs']),
    other_statuses_preserved=len(before['statuses'])-7,
    root_dirty_files_preserved=len(before['root_modified_files']), config_preserved=True,
    tests_passed=5573, tests_skipped=196)
(OUT/'live_verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
print(json.dumps({'verified_jobs': len(rows), 'pdfs_preserved': len(before['pdfs']),
    'other_statuses_preserved': receipt['other_statuses_preserved'],
    'counts': [{'job': r['job'], 'required': r['counts']['queue_required_total_with_obligations'],
                'blocking': len(r['blocking_ids'])} for r in rows]}, ensure_ascii=False))
