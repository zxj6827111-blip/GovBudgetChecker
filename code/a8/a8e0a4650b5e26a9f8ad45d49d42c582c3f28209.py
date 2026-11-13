from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED='1', DATABASE_URL='', GOVBUDGET_TEST_DATABASE_URL='')
from scripts.run_zc07_acceptance import _evaluate_export_agreement


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


views = {}
checks = []
run_summary = {}
for label, base in [('claimed_round13', ROOT / 'outputs/remediation_20261006_round13/services_final'), ('fresh_round14', OUT / 'live_services')]:
    receipt = json.loads((base / 'service_receipt.json').read_text())
    source = {p: sha(ROOT / p) == h for p, h in receipt['code_fingerprints'].items()}
    artifact = {p: sha(base / p) == h for p, h in receipt['artifact_sha256'].items()}
    assert all(source.values()) and all(artifact.values())
    assert receipt['errors'] == []
    assert receipt['status'] == ('passed' if label == 'claimed_round13' else 'failed')
    assert len(receipt['scenarios']) == receipt['expected_scenario_count'] == 7
    cleanup = {k: receipt[k] for k in ['postgres_removed', 'temporary_workspace_removed', 'processes_stopped', 'logs_closed', 'source_unchanged_during_run']}
    assert all(cleanup.values())
    assert not Path(receipt['temporary_workspace']).exists()
    pg = subprocess.run(['docker', 'inspect', receipt['postgres']['isolation']], capture_output=True, text=True)
    assert pg.returncode != 0 and 'no such' in pg.stderr.lower()
    if label == 'fresh_round14':
        for key in ['api_pid', 'worker_pid']:
            try:
                os.kill(receipt['processes'][key], 0)
            except ProcessLookupError:
                continue
            raise AssertionError('process still present: ' + key)
    run_summary[label] = {'status': receipt['status'], 'elapsed_seconds': receipt['elapsed_seconds'], 'cleanup': cleanup, 'source_checks': len(source), 'artifact_checks': len(artifact), 'source_snapshot_unchanged': True}
    for scenario in receipt['scenarios']:
        name = scenario['scenario']
        folder = base / name
        report = json.loads((folder / 'report.json').read_text())
        status = json.loads((folder / 'status.json').read_text())
        failed_scan = label == 'fresh_round14' and name == 'original_scanned_budget'
        verified = {
            'source_bytes': sha(folder / 'source.pdf') == scenario['source_sha256'],
            'SQL_file_hash': scenario['database']['job']['file_hash'] == scenario['source_sha256'],
            'SQL_job_uuid': scenario['database']['job']['job_uuid'] == scenario['job_id'],
            'current_result': scenario['database']['analysis_results'] == 1,
            'revision': report['analysis_revision'] == scenario['analysis_revision'],
            'terminal': status['status'] == scenario['terminal_status'] == ('error' if failed_scan else 'review_required'),
            'worker_completed': bool(scenario['worker_completion_events']) or failed_scan,
            'export_bytes': all(sha(folder / n) == h for n, h in scenario['exports'].items()),
            'repeat_bytes': all(scenario['repeated_downloads_identical'].values()),
            'engineering_status_matches': scenario['service_validation_passed'] == (not failed_scan),
        }
        original_paths = [str(folder / ('report.' + ext)) for ext in ['json', 'csv', 'docx', 'pdf']]
        views[label + '_' + name] = _evaluate_export_agreement({'documents': [{'doc_id': name, 'export_paths': original_paths}]}, ROOT)
        if 'correction' in scenario:
            correction = scenario['correction']
            corrected = folder / 'corrected'
            next_report = json.loads((corrected / 'report.json').read_text())
            context = json.loads((corrected / 'fact_context.json').read_text())
            target = next(f for f in context['facts'] if f['fact_uid'] == correction['target_id'])
            verified.update({
                'corrected_bytes': all(sha(corrected / n) == h for n, h in correction['exports'].items()),
                'corrected_revision': next_report['analysis_revision'] == correction['new_revision'] != correction['old_revision'],
                'persisted_overlay': float(target['raw_value']) == float(correction['corrected_raw_value']),
                'SQL_revision_increment': correction['database_after']['job']['analysis_revision'] > scenario['database']['job']['analysis_revision'],
                'history_bytes': all(correction['history_identical'].values()),
                'corrected_worker_event': len(correction['worker_completion_events_after']) > len(scenario['worker_completion_events']),
            })
            paths = [str(corrected / ('report.' + ext)) for ext in ['json', 'csv', 'docx', 'pdf']]
            views[label + '_' + name + '_corrected'] = _evaluate_export_agreement({'documents': [{'doc_id': name + '_corrected', 'export_paths': paths}]}, ROOT)
        assert all(verified.values()), (name, verified)
        checks.append({'run': label, 'scenario': name, 'checks': verified, 'source_sha256': scenario['source_sha256'], 'synthetic': scenario['synthetic'], 'derived': scenario.get('derived'), 'formal_sample_eligible': scenario.get('formal_sample_eligible')})

computed = [v for v in views.values() if v['status'] == 'computed']
assert len(computed) == 19 and len(views) == 20
assert all(v['export_agreement_rate'] == 1 for v in computed)
assert views['fresh_round14_original_scanned_budget']['status'] == 'not_computable'
save('live_service_integrity.json', {'scope': 'development service evidence only; no independently frozen business scores', 'runs': run_summary, 'scenarios': checks})
save('production_export_gate.json', {'scope': 'seven original and three corrected views per run; engineering consistency, not frozen truth', 'bundles': views})
print(json.dumps({'run_count': len(run_summary), 'scenario_views': len(checks), 'four_format_bundles': len(views), 'all_passed': False, 'computed_consistent_bundles': len(computed), 'failed_scan_job': True, 'fresh_elapsed_seconds': run_summary['fresh_round14']['elapsed_seconds']}, ensure_ascii=False))
