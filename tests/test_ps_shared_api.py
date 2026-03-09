import json
import os

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import runtime
from api.routes import ps_shared as ps_shared_routes


def test_job_structured_ingest_endpoint_returns_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    job_dir = tmp_path / "job-structured"
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(
        json.dumps({"job_id": "job-structured", "status": "done"}, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime.write_structured_ingest_payload(
        job_dir,
        {
            "job_id": "job-structured",
            "status": "done",
            "document_version_id": 88,
            "recognized_tables": 9,
            "facts_count": 160,
            "review_item_count": 0,
            "review_items": [],
            "ps_sync": {
                "report_id": "report-x",
                "table_data_count": 9,
                "line_item_count": 160,
            },
        },
    )

    client = TestClient(app)
    response = client.get("/api/jobs/job-structured/structured-ingest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_version_id"] == 88
    assert payload["facts_count"] == 160
    assert payload["ps_sync"]["report_id"] == "report-x"


def test_ps_reports_route_accepts_scope_filters(monkeypatch):
    captured = {}

    class DummyService:
        async def list_reports(self, **kwargs):
            captured.update(kwargs)
            return {
                "items": [
                    {
                        "report_id": "report-1",
                        "year": 2026,
                        "report_type": "BUDGET",
                        "file_name": "sample.pdf",
                    }
                ],
                "total": 1,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
            }

    async def _fake_execute(callback):
        return await callback(DummyService())

    monkeypatch.setattr(ps_shared_routes, "_execute_ps_query", _fake_execute)

    client = TestClient(app)
    response = client.get(
        "/api/ps/reports?department_id=dept-1&unit_id=unit-1&year=2026&report_type=budget&keyword=街道&limit=5&offset=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["report_id"] == "report-1"
    assert captured == {
        "department_id": "dept-1",
        "unit_id": "unit-1",
        "year": 2026,
        "report_type": "budget",
        "keyword": "街道",
        "limit": 5,
        "offset": 10,
    }


def test_ps_report_detail_route_returns_not_found(monkeypatch):
    async def _fake_execute(_callback):
        return None

    monkeypatch.setattr(ps_shared_routes, "_execute_ps_query", _fake_execute)

    client = TestClient(app)
    response = client.get("/api/ps/reports/report-missing")

    assert response.status_code == 404


def test_ps_report_tables_route_supports_table_key_and_data_toggle(monkeypatch):
    captured = {}

    class DummyService:
        async def list_report_tables(self, report_id, *, table_key=None, include_data=True):
            captured.update(
                {
                    "report_id": report_id,
                    "table_key": table_key,
                    "include_data": include_data,
                }
            )
            return {
                "report_id": report_id,
                "table_key": table_key,
                "include_data": include_data,
                "items": [{"table_key": "FIN_01_income_expenditure_total"}],
                "total": 1,
            }

    async def _fake_execute(callback):
        return await callback(DummyService())

    monkeypatch.setattr(ps_shared_routes, "_execute_ps_query", _fake_execute)

    client = TestClient(app)
    response = client.get(
        "/api/ps/reports/report-1/tables?table_key=FIN_01_income_expenditure_total&include_data=false"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_id"] == "report-1"
    assert payload["include_data"] is False
    assert captured == {
        "report_id": "report-1",
        "table_key": "FIN_01_income_expenditure_total",
        "include_data": False,
    }


def test_ps_report_line_items_route_supports_pagination(monkeypatch):
    captured = {}

    class DummyService:
        async def list_report_line_items(self, report_id, *, table_key=None, limit=500, offset=0):
            captured.update(
                {
                    "report_id": report_id,
                    "table_key": table_key,
                    "limit": limit,
                    "offset": offset,
                }
            )
            return {
                "report_id": report_id,
                "table_key": table_key,
                "items": [{"row_index": 1, "item_name": "社会保障和就业支出"}],
                "total": 1,
                "limit": limit,
                "offset": offset,
            }

    async def _fake_execute(callback):
        return await callback(DummyService())

    monkeypatch.setattr(ps_shared_routes, "_execute_ps_query", _fake_execute)

    client = TestClient(app)
    response = client.get(
        "/api/ps/reports/report-2/line-items?table_key=FIN_05_general_public_expenditure&limit=20&offset=40"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["row_index"] == 1
    assert captured == {
        "report_id": "report-2",
        "table_key": "FIN_05_general_public_expenditure",
        "limit": 20,
        "offset": 40,
    }
