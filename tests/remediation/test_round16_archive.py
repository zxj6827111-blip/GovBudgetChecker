"""Archive bundles must preserve the reviewed revision and require confirmation."""
import asyncio
import hashlib
import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import runtime
from api.main import app
from api.routes import reports
from src.services import issue_workflow_store as workflow

USER = {"username": "test-admin", "is_admin": True}


def seed(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    (tmp_path / "archive-job").mkdir()
    payload = {
        "job_id": "archive-job", "status": "review_required", "analysis_revision": "rev-first",
        "checksum": "a" * 64, "filename": "source.pdf", "created_by": "test-admin",
        "result": {"issues": {"all": [{"id": "finding", "rule_id": "R-TEST", "severity": "high", "message": "first reviewed finding", "location": {"page": 1}}]}},
    }
    runtime.write_json_file(tmp_path / "archive-job/status.json", payload)
    return payload


def confirm_and_package():
    async def run():
        await workflow.update_issue(USER, job_id="archive-job", issue_id="finding", status="confirmed", note=None)
        return (await workflow.create_package(USER, name="reviewed bundle", job_ids=["archive-job"], issue_keys=["archive-job::finding"]))["package"]
    return asyncio.run(run())


def test_pending_findings_cannot_be_marked_in_package(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        asyncio.run(workflow.create_package(USER, name="pending", job_ids=["archive-job"], issue_keys=["archive-job::finding"]))
    assert error.value.status_code == 409
    assert error.value.detail["error"] == "package_requires_confirmed_findings"
    assert workflow.get_visible_state(USER)["packages"] == []


def fake_pdf_export(tmp_path, monkeypatch):
    def create(job_id, payload, issues):
        result = tmp_path / ("export-" + payload["analysis_revision"] + ".pdf")
        result.write_text(json.dumps({"revision": payload["analysis_revision"], "messages": [row["message"] for row in payload["result"]["issues"]["all"]]}, ensure_ascii=False))
        return result
    monkeypatch.setattr(reports, "_create_annotated_pdf", create)


def download(client, package):
    response = client.post("/api/reports/download-batch", json={"job_ids": package["job_ids"], "package_id": package["id"]})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next(name for name in archive.namelist() if name.endswith('.pdf'))
        return json.loads(archive.read(name)), json.loads(archive.read('manifest.json'))


def test_archive_download_survives_reanalysis_and_reload(tmp_path, monkeypatch):
    original = seed(tmp_path, monkeypatch)
    package = confirm_and_package()
    fake_pdf_export(tmp_path, monkeypatch)
    with TestClient(app) as client:
        first, _ = download(client, package)
        original["analysis_revision"] = "rev-second"
        original["result"]["issues"]["all"][0]["message"] = "new unreviewed finding"
        runtime.write_json_file(tmp_path / "archive-job/status.json", original)
        # Read the durable package again, as a fresh request after restart would.
        restored = workflow.get_visible_state(USER)["packages"][0]
        second, manifest = download(client, restored)
    assert second == first == {"revision": "rev-first", "messages": ["first reviewed finding"]}
    assert manifest["package_id"] == package["id"]
    assert manifest["exported"][0]["analysis_revision"] == "rev-first"


def test_tampered_archived_payload_fails_closed(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    package = confirm_and_package()
    fake_pdf_export(tmp_path, monkeypatch)
    state = runtime.read_json_file(tmp_path / '.issue_workflow.json', default={})
    frozen = state['packages'][0]['report_snapshots']['archive-job']
    frozen['payload']['analysis_revision'] = 'forged-revision'
    runtime.write_json_file(tmp_path / '.issue_workflow.json', state)
    with TestClient(app) as client:
        response = client.post('/api/reports/download-batch', json={'job_ids': package['job_ids'], 'package_id': package['id']})
    assert response.status_code == 409
    assert response.json()['detail']['error'] == 'package_snapshot_invalid'


def test_legacy_package_does_not_silently_export_current_revision(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    package = confirm_and_package()
    state = runtime.read_json_file(tmp_path / '.issue_workflow.json', default={})
    state['packages'][0].pop('report_snapshots', None)
    runtime.write_json_file(tmp_path / '.issue_workflow.json', state)
    with TestClient(app) as client:
        response = client.post('/api/reports/download-batch', json={'job_ids': package['job_ids'], 'package_id': package['id']})
    assert response.status_code == 409
    assert response.json()['detail']['error'] == 'package_snapshot_missing'


def test_package_download_cannot_add_unselected_job(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    package = confirm_and_package()
    with TestClient(app) as client:
        response = client.post('/api/reports/download-batch', json={'job_ids': ['archive-job','other-job'], 'package_id': package['id']})
    assert response.status_code == 409


def test_stale_workflow_decision_is_pending_in_ui_but_audit_is_preserved(tmp_path, monkeypatch):
    payload = seed(tmp_path, monkeypatch)
    confirm_and_package()
    payload['analysis_revision'] = 'rev-second'
    payload['result']['issues']['all'][0]['message'] = 'new finding reusing an old ID'
    runtime.write_json_file(tmp_path / 'archive-job/status.json', payload)
    assert workflow.get_visible_state(USER)['issues']['archive-job::finding']['status'] == 'pending'
    state = runtime.read_json_file(tmp_path / '.issue_workflow.json', default={})
    assert state['issues']['archive-job::finding']['status'] == 'in_package'
    assert state['decision_events'][-1]['analysis_revision'] == 'rev-first'


def test_real_pdf_archive_keeps_revision_and_source_bytes_after_reanalysis(tmp_path, monkeypatch):
    import pymupdf

    payload = seed(tmp_path, monkeypatch)
    source = tmp_path / 'archive-job/source.pdf'
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), 'Original disclosed amount 746.62')
        document.save(source)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    payload['checksum'] = source_sha
    runtime.write_json_file(tmp_path / 'archive-job/status.json', payload)
    package = confirm_and_package()
    with TestClient(app) as client:
        def pdf_bytes():
            r = client.post('/api/reports/download-batch', json={'job_ids': package['job_ids'], 'package_id': package['id']})
            assert r.status_code == 200, r.text
            with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
                return archive.read(next(name for name in archive.namelist() if name.endswith('.pdf')))
        first = pdf_bytes()
        payload['analysis_revision'] = 'rev-second'
        payload['result']['issues']['all'][0]['message'] = 'second unreviewed issue'
        runtime.write_json_file(tmp_path / 'archive-job/status.json', payload)
        second = pdf_bytes()
    assert first == second
    with pymupdf.open(stream=second, filetype='pdf') as document:
        text = ''.join(page.get_text() for page in document)
        assert 'rev-first' in text and 'first reviewed finding' in text
        assert 'rev-second' not in text and 'second unreviewed issue' not in text
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
