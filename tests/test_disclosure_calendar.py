"""年度公开时限的存储与接口契约。

这组用例守的是三件事：客户给的日期能填进来看得见；没填的年度必须是"未知"
而不是任何默认月日（旧版本曾硬编码 8/13、8/28 当参考截止日，会凭空造出逾期）；
一次保存要么全成要么全不动，不能留下半新半旧的年度。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.routes.disclosure_calendar as route
from src.services import disclosure_calendar as calendar

YEAR_ROWS = [
    {"year": 2025, "kind": "决算", "level": "部门", "deadline": "2026-08-13", "source": "沪普财〔2026〕12号"},
    {"year": 2025, "kind": "决算", "level": "单位", "deadline": "2026-08-28", "source": "沪普财〔2026〕12号"},
    {"year": 2026, "kind": "预算", "level": "部门", "deadline": "2026-02-15", "source": ""},
    {"year": 2026, "kind": "预算", "level": "单位", "deadline": "2026-03-02", "source": ""},
]


def _client(monkeypatch, tmp_path, *, admin_ok=True):
    monkeypatch.setenv("DISCLOSURE_CALENDAR_PATH", str(tmp_path / "disclosure-calendar.json"))
    monkeypatch.setattr(route, "require_login", lambda request: ({}, {}, {"username": "t"}))
    if admin_ok:
        monkeypatch.setattr(route, "require_admin", lambda request: ({}, {}, {"username": "t"}))
    else:
        def _denied(request):
            raise HTTPException(403, "需要管理员")
        monkeypatch.setattr(route, "require_admin", _denied)
    monkeypatch.setattr(route, "append_audit_event", lambda **kw: None)
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


def test_save_then_read_back(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    res = client.post("/api/admin/disclosure-calendar", json={"entries": YEAR_ROWS})
    assert res.status_code == 200, res.text
    assert len(res.json()["saved"]) == 4
    assert calendar.resolve("决算", 2025, "单位") == "2026-08-28"
    assert calendar.resolve("预算", 2026, "部门") == "2026-02-15"
    got = client.get("/api/disclosure-calendar").json()
    assert got["by_year"]["2025"]["决算"]["单位"]["deadline"] == "2026-08-28"
    assert got["by_year"]["2025"]["决算"]["单位"]["source"] == "沪普财〔2026〕12号"
    assert set(got["kinds"]) == {"预算", "决算"} and set(got["levels"]) == {"部门", "单位"}


def test_unfilled_year_is_unknown_not_a_default(monkeypatch, tmp_path):
    """反向断言：绝不允许回落到旧的固定月日，否则清单会凭空判逾期。"""
    _client(monkeypatch, tmp_path)
    assert calendar.resolve("决算", 2024, "部门") == ""
    assert calendar.resolve("决算", 2024, "单位") == ""
    assert calendar.resolve("预算", 2099, "单位") == ""


def test_invalid_date_writes_nothing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    bad = [YEAR_ROWS[0], {**YEAR_ROWS[1], "deadline": "2026-13-45"}]
    res = client.post("/api/admin/disclosure-calendar", json={"entries": bad})
    assert res.status_code == 422, res.text
    assert calendar.load_rows() == [], "校验失败时不得写入任何一条"


def test_blank_deadline_revokes_confirmation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/admin/disclosure-calendar", json={"entries": [YEAR_ROWS[0]]})
    assert calendar.resolve("决算", 2025, "部门") == "2026-08-13"
    revoke = {**YEAR_ROWS[0], "deadline": ""}
    client.post("/api/admin/disclosure-calendar", json={"entries": [revoke]})
    assert calendar.resolve("决算", 2025, "部门") == ""
    assert len(calendar.load_rows()) == 1, "同键应覆盖而非追加"


def test_resave_same_year_updates_instead_of_duplicating(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/admin/disclosure-calendar", json={"entries": YEAR_ROWS})
    later = [{**YEAR_ROWS[1], "deadline": "2026-09-05", "source": "补通知"}]
    client.post("/api/admin/disclosure-calendar", json={"entries": later})
    assert len(calendar.load_rows()) == 4
    assert calendar.resolve("决算", 2025, "单位") == "2026-09-05"


def test_chinese_date_form_is_normalised(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    res = client.post("/api/admin/disclosure-calendar",
                      json={"entries": [{**YEAR_ROWS[2], "deadline": "2026年2月15日"}]})
    assert res.status_code == 200
    assert calendar.resolve("预算", 2026, "部门") == "2026-02-15"


def test_non_admin_cannot_write(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, admin_ok=False)
    res = client.post("/api/admin/disclosure-calendar", json={"entries": YEAR_ROWS})
    assert res.status_code == 403
    assert calendar.load_rows() == []


def test_kind_and_level_are_enforced(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for bad in ({"year": 2025, "kind": "政府采购", "level": "部门", "deadline": ""},
                {"year": 2025, "kind": "决算", "level": "本级", "deadline": ""},
                {"year": 1999, "kind": "决算", "level": "部门", "deadline": ""}):
        res = client.post("/api/admin/disclosure-calendar", json={"entries": [bad]})
        assert res.status_code == 422, bad
    assert calendar.load_rows() == []
