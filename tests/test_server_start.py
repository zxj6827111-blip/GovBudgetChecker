"""The server entry point must distinguish config checks from actual readiness."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def server_entry(tmp_path):
    project = tmp_path / 'project with spaces'
    (project / 'scripts').mkdir(parents=True)
    (project / 'models').mkdir()
    shutil.copy2(ROOT / 'scripts/start-server.sh', project / 'scripts/start-server.sh')
    for name in ('docker-compose.yml', 'docker-compose.ai.yml', '.env'):
        (project / name).write_text('# fixture\n')
    for name in ('MANIFEST.sha256', 'MODELS.json'):
        (project / 'models' / name).write_text('fixture')
    binary = tmp_path / 'bin'
    binary.mkdir()
    fake = binary / 'docker'
    fake.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['SERVER_TEST_COMMAND_LOG'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('SERVER_TEST_FAIL') in sys.argv[1:]:
    sys.exit(1)
''')
    fake.chmod(0o755)
    log = tmp_path / 'commands.jsonl'
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'SERVER_TEST_COMMAND_LOG': str(log)}

    def run(*args, fail=None):
        command_env = {**env}
        if fail:
            command_env['SERVER_TEST_FAIL'] = fail
        result = subprocess.run(['bash', str(project / 'scripts/start-server.sh'), *args],
                                cwd=tmp_path, env=command_env, capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return project, run


def test_check_mode_does_not_start_or_exec_services(server_entry):
    _, run = server_entry
    result, calls = run('--check')
    assert result.returncode == 0, result.stderr
    assert any('config' in call and '--quiet' in call for call in calls)
    assert not any('up' in call or 'exec' in call for call in calls)


def test_default_waits_for_entire_stack_and_verifies_ocr(server_entry):
    project, run = server_entry
    result, calls = run()
    assert result.returncode == 0, result.stderr
    start = next(call for call in calls if 'up' in call)
    assert all(option in start for option in ('--build', '--wait', '--wait-timeout'))
    assert start.count('-f') == 1
    assert str(project / 'docker-compose.yml') in start
    probe = next(call for call in calls if 'exec' in call)
    assert '-T' in probe and 'backend' in probe
    assert 'OCR_MODEL_FINGERPRINT' in probe[-1]
    assert 'model_fingerprint' in probe[-1]
    assert '服务已就绪' in result.stdout


def test_optional_extractor_and_existing_images_are_explicit(server_entry):
    _, run = server_entry
    result, calls = run('--ai', '--no-build')
    assert result.returncode == 0, result.stderr
    start = next(call for call in calls if 'up' in call)
    assert start.count('-f') == 2
    assert '--no-build' in start and '--build' not in start


def test_custom_env_path_with_spaces_is_preserved(server_entry):
    project, run = server_entry
    config = project / 'production config.env'
    config.write_text('# fixture\n')
    result, calls = run('--env-file', str(config), '--check')
    assert result.returncode == 0, result.stderr
    validation = next(call for call in calls if 'config' in call)
    assert validation[validation.index('--env-file') + 1] == str(config)


@pytest.mark.parametrize('failed_command', ['info', 'config', 'up', 'exec'])
def test_dependency_or_ocr_failure_does_not_report_success(server_entry, failed_command):
    _, run = server_entry
    result, _ = run(fail=failed_command)
    assert result.returncode != 0
    assert '服务已就绪' not in result.stdout


@pytest.mark.parametrize('missing', ['.env', 'models/MANIFEST.sha256'])
def test_missing_configuration_or_model_bundle_stops_before_up(server_entry, missing):
    project, run = server_entry
    (project / missing).unlink()
    result, calls = run()
    assert result.returncode != 0
    assert not any('up' in call for call in calls)


def test_unknown_arguments_do_not_start_services(server_entry):
    _, run = server_entry
    result, calls = run('--unsupported')
    assert result.returncode == 2
    assert calls == []


def test_stack_waits_for_ocr_and_database_and_probes_queue_readiness():
    base = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())['services']
    ai = yaml.safe_load((ROOT / 'docker-compose.ai.yml').read_text())['services']
    for name in ('backend', 'worker'):
        for dependency in ('postgres', 'ocr'):
            assert base[name]['depends_on'][dependency]['condition'] == 'service_healthy'
    for config in (base, ai):
        assert '/ready' in config['backend']['healthcheck']['test'][-1]
    assert '/api/health' in base['frontend']['healthcheck']['test'][-1]
    assert base['frontend']['depends_on']['backend']['condition'] == 'service_healthy'
    assert base['postgres']['ports'] == ['127.0.0.1:5432:5432']
    assert base['ocr']['volumes'] == ['./models:/models:ro']
