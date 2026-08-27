"""Task 3：最近活动流只读接口测试。

对照任务书测试要求：
- 鉴权（无凭据 401）；
- 分页（limit/offset 正确切片，且倒序——最新事件在前）；
- 脱敏（构造含敏感值的审计记录，断言响应不含原值）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.routes.activity as activity_route
from src.services.audit_log import append_audit_event, read_audit_events


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    """独立挂载 activity 路由，避免拉起整个 app 的 lifespan 与队列
    （与 tests/test_metrics.py 的 `_client` 是同一种取舍）。

    注意：这里不再重复设置 `AUDIT_LOG_PATH`——调用方（各测试函数）已经在
    调用本函数之前就设置好了该环境变量并据此写入事件，这里如果再设一次
    默认路径会把调用方设置的路径覆盖掉，导致读取的文件与写入的文件不是同一个
    （曾实测踩过这个坑：写入用了测试自己指定的路径，读取却读到了这里硬编码的
    另一个路径，结果 total 永远是 0）。
    """
    app = FastAPI()
    app.include_router(activity_route.router)
    return TestClient(app)


def _deny_admin(monkeypatch) -> None:
    def _raise(_request):
        raise HTTPException(status_code=401, detail="session token required")

    monkeypatch.setattr(activity_route, "require_admin", _raise)


def _allow_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        activity_route,
        "require_admin",
        lambda _request: (None, "tok", {"username": "admin", "is_admin": True}),
    )


def _write_events(count: int) -> None:
    for index in range(count):
        append_audit_event(
            action=f"jobs.action_{index}",
            actor="e2e-admin",
            result="success",
            resource_type="job",
            resource_id=f"job-{index}",
            details={"index": index},
        )


# ---------------------------------------------------------------------------
# 鉴权（正反对照）
# ---------------------------------------------------------------------------


def test_activity_endpoint_rejects_unauthenticated_request(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    _write_events(3)
    _deny_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert response.status_code == 401, "REGRESSION: 无凭据访问活动流必须被拒绝（401）"


def test_activity_endpoint_allows_admin_session(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    _write_events(3)
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# 分页与倒序（最新事件排在最前）
# ---------------------------------------------------------------------------


def test_activity_endpoint_pagination_and_reverse_chronological_order(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    _write_events(5)  # action_0 .. action_4，按写入顺序，action_4 最新
    _allow_admin(monkeypatch)
    client = _client(monkeypatch, tmp_path)

    first_page = client.get("/api/activity?limit=2&offset=0").json()
    assert first_page["total"] == 5
    assert [item["action"] for item in first_page["items"]] == [
        "jobs.action_4",
        "jobs.action_3",
    ], "REGRESSION: 活动流必须按时间倒序返回，最新事件排在最前"

    second_page = client.get("/api/activity?limit=2&offset=2").json()
    assert [item["action"] for item in second_page["items"]] == [
        "jobs.action_2",
        "jobs.action_1",
    ]

    third_page = client.get("/api/activity?limit=2&offset=4").json()
    assert [item["action"] for item in third_page["items"]] == ["jobs.action_0"], (
        "第三页只剩 1 条（越界部分不应该报错，也不应该补出不存在的记录）"
    )


def test_activity_endpoint_out_of_range_offset_returns_empty_not_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    _write_events(2)
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity?limit=10&offset=100")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 2, "越界分页不应该改变 total 计数"


def test_activity_endpoint_returns_empty_when_log_file_does_not_exist(monkeypatch, tmp_path):
    """反例：审计日志文件从未被创建过时（全新部署、从未有过管理员操作），
    接口必须返回结构完整的空结果，不能 500。"""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "never-created.jsonl"))
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


# ---------------------------------------------------------------------------
# 脱敏（核心反例）：构造含敏感值的审计记录，断言响应不含原值
# ---------------------------------------------------------------------------


SECRET_MATERIAL_TEXT = "合计35.20万元其中因公出国境费10.00万元（送检材料原文片段）"
SECRET_API_KEY = "gbk-live-0123456789abcdef"


def test_activity_endpoint_redacts_sensitive_material_text_in_details(monkeypatch, tmp_path):
    """反例：details 里携带材料原文风格字段（evidence_text）时，响应体绝不能
    包含原文，只能包含长度与哈希指纹——这是与写日志时同一套脱敏判定规则
    （is_sensitive_log_key 命中 `evidence_text`）。"""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    append_audit_event(
        action="jobs.reanalyze",
        actor="e2e-admin",
        result="success",
        resource_type="job",
        resource_id="job-secret",
        details={"evidence_text": SECRET_MATERIAL_TEXT, "job_id": "job-secret"},
    )
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert response.status_code == 200
    raw_body_text = response.text
    assert SECRET_MATERIAL_TEXT not in raw_body_text, (
        "REGRESSION: 活动流接口把材料原文原样返回了，这是本任务书明确禁止的泄漏路径"
    )

    item = response.json()["items"][0]
    assert "evidence_text" not in item["details"], "敏感键本身不应该原样出现在响应里"
    assert item["details"]["evidence_text_len"] == len(SECRET_MATERIAL_TEXT)
    assert isinstance(item["details"]["evidence_text_sha256"], str)
    # 非敏感字段应该原样保留，证明脱敏是精确打击而不是把整条记录清空
    assert item["details"]["job_id"] == "job-secret"


def test_activity_endpoint_redacts_credential_like_fields_in_details(monkeypatch, tmp_path):
    """反例变体：凭据类字段（api_key）同样必须被脱敏，不止材料原文类字段。"""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    append_audit_event(
        action="config.update",
        actor="e2e-admin",
        result="success",
        resource_type="system-settings",
        details={"api_key": SECRET_API_KEY, "setting_name": "ai_extractor_url"},
    )
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert SECRET_API_KEY not in response.text, (
        "REGRESSION: 凭据类字段（api_key）必须被脱敏，绝不能原样出现在活动流响应里"
    )
    item = response.json()["items"][0]
    assert "api_key" not in item["details"]
    assert item["details"]["setting_name"] == "ai_extractor_url"


def test_activity_endpoint_redacts_nested_details_recursively(monkeypatch, tmp_path):
    """反例：脱敏必须递归处理嵌套结构，不能只脱敏顶层键
    （对应 redact_log_fields 对 Mapping/list 的递归处理能力）。"""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    append_audit_event(
        action="jobs.batch_reanalyze",
        actor="e2e-admin",
        result="success",
        resource_type="job_batch",
        details={
            "failed_jobs": [
                {"job_id": "job-1", "error_text": SECRET_MATERIAL_TEXT},
            ]
        },
    )
    _allow_admin(monkeypatch)

    response = _client(monkeypatch, tmp_path).get("/api/activity")

    assert SECRET_MATERIAL_TEXT not in response.text, (
        "REGRESSION: 嵌套在 list[dict] 内部的敏感字段也必须被脱敏，不能只处理顶层"
    )


# ---------------------------------------------------------------------------
# read_audit_events 本身的正反对照（不经过 API 层，直接测读取函数）
# ---------------------------------------------------------------------------


def test_read_audit_events_skips_corrupted_lines_without_raising(monkeypatch, tmp_path):
    """反例：文件中混入一行无法解析的损坏内容时，读取函数不应该整体报错，
    应跳过该行继续解析其余合法记录。"""
    log_path = tmp_path / "audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    valid_record: Dict[str, Any] = {
        "ts": 1700000000.0,
        "action": "jobs.delete",
        "actor": "admin",
        "result": "success",
        "resource_type": "job",
        "resource_id": "job-1",
        "resource_name": "",
        "details": {},
    }
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(valid_record, ensure_ascii=False) + "\n")
        fh.write("this is not valid json{{{\n")
        fh.write(json.dumps(valid_record, ensure_ascii=False) + "\n")

    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_path))

    result = read_audit_events(limit=10, offset=0)

    assert result["total"] == 2, "损坏行应被跳过，不计入 total，也不能让函数抛异常"


@pytest.mark.parametrize("scan_limit", [1, 2])
def test_read_audit_events_scan_limit_only_covers_most_recent_lines(monkeypatch, tmp_path, scan_limit):
    """max_scan_lines 只保留文件末尾若干行参与解析，验证确实是"只看最近的"，
    不是"看最早的"（写反了会导致活动流显示的是最古老的记录而不是最新的）。"""
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_path))
    _write_events(5)

    result = read_audit_events(limit=10, offset=0, max_scan_lines=scan_limit)

    assert result["total"] == scan_limit
    scanned_actions = [item["action"] for item in result["items"]]
    # 被保留的应该是最新的几条（action_4 是最新写入的）
    assert scanned_actions[0] == "jobs.action_4"
