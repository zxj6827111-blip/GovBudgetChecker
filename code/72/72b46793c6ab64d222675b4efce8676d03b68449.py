"""新会话复验：重新执行公开入口、冻结真值、测试和可清理隔离服务。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
PY = ROOT / '.venv/bin/python'
ENV = dict(os.environ, AUDIT_LOG_PATH=str(BASE / 'test_audit.jsonl'), PYTHONUNBUFFERED='1')
STEPS = []


def save():
    (BASE / 'execution.json').write_text(json.dumps(STEPS, ensure_ascii=False, indent=2))


def run(name, args, allow_failure=False):
    record = dict(step=name, started_at=datetime.now(timezone.utc).isoformat(), command=list(map(str, args)), status='running')
    STEPS.append(record)
    save()
    print('START', name, flush=True)
    with (BASE / f'{name}.log').open('w') as log:
        result = subprocess.run(list(map(str, args)), cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT)
    record.update(exit_code=result.returncode, status='passed' if result.returncode == 0 else 'failed', finished_at=datetime.now(timezone.utc).isoformat())
    save()
    print('END', name, result.returncode, flush=True)
    if result.returncode and not allow_failure:
        raise SystemExit(result.returncode)


run('new10', [PY, BASE / 'run_batch.py', 'new10', '--batch', 'new10'])
run('native_identity', [PY, BASE / 'compare_native_identity.py', 'new10', '--before', 'baseline', '--verify-pdf-characters'])
run('rules', [PY, BASE / 'verify_rule_results.py', 'new10'])
run('old15', [PY, BASE / 'run_batch.py', 'old15', '--batch', 'old15'])
run('known19', [PY, BASE / 'verify_known19.py'])
run('focused_pytest', [PY, '-m', 'pytest', '-q',
    'tests/remediation/test_native_tail_repair_v25.py',
    'tests/remediation/test_native_identity_repair_v25.py',
    'tests/remediation/test_native_rule_repair_v25.py',
    'tests/remediation/test_native_structure_recovery.py',
    'tests/remediation/test_native_structure_pdf_pipeline.py',
    'tests/remediation/test_native_fiscal_coverage.py',
    'tests/remediation/test_table_reading_recovery.py',
    'tests/remediation/test_zc01.py',
    'tests/remediation/test_native_pdf_core.py'])
# 全套允许记录既有失败后继续真实服务复验；最终报告单独裁定失败身份。
run('full_pytest', [PY, '-m', 'pytest', '-q'], allow_failure=True)
run('live_service', [PY, BASE / 'run_live_service.py', 'service'])
run('service_readback', [PY, BASE / 'verify_service_readback.py', 'service', 'new10'])

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

before = json.loads((BASE / 'protection_before.json').read_text())
drift = [name for name, digest in before['code'].items() if sha(ROOT / name) != digest]
protected_drift = [name for name, digest in before['protected'].items() if sha(Path(name)) != digest]
inputs = json.loads((BASE / 'input_verification.json').read_text())
input_drift = [d['path'] for d in inputs if sha(Path(d['path'])) != d['sha256']]
result = dict(source_test_fixture_drift=drift, protected_evidence_drift=protected_drift, input_pdf_drift=input_drift,
              existing_services_restarted=False, production_deployed=False)
(BASE / 'protection_after.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
assert not (drift or protected_drift or input_drift)
print('REACCEPTANCE EXECUTION COMPLETE', flush=True)
