"""规则集与引擎版本只读端点测试（UI 重建第四批 Task 8.3 的后端补充）。

断言意图（每组正反对照）：
1. 正例：指向仓库真实 rules/v3_3.yaml 时，版本/条目数来自真实文件解析
   （v3_3_all_in_one / 15 条），引擎版本与 provenance.ENGINE_VERSION 同源；
2. 反例（防硬编码）：指向只有 2 条规则的临时 YAML 时，条目数必须变成 2
   ——如果端点是硬编码的，这个断言会失败；
3. 反例：RULES_FILE 指向不存在的文件 → available=false、版本/条目数为
   None，绝不落占位值，也不抛 500；
4. 反例：非管理员访问 → 401/403；
5. entries 端点分页与字段概要（只暴露 rule_id/title/severity/doc_scope）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import rules as rules_route
from src.utils.provenance import ENGINE_VERSION

_REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_RULES_FILE = _REPO_ROOT / "rules" / "v3_3.yaml"


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    """独立挂载 rules 路由，并默认以管理员身份放行。"""
    monkeypatch.setattr(
        rules_route,
        "require_admin",
        lambda _request: (None, "tok", {"username": "admin", "is_admin": True}),
    )
    app = FastAPI()
    app.include_router(rules_route.router)
    return TestClient(app)


def _deny_admin(monkeypatch) -> None:
    def _raise(_request):
        raise HTTPException(status_code=401, detail="session token required")

    monkeypatch.setattr(rules_route, "require_admin", _raise)


def _write_minimal_rules_file(tmp_path: Path, *, rule_count: int, version: str) -> Path:
    """构造最小可解析的规则 YAML，rule_count 精确可控（防硬编码对照用）。"""
    document = {
        "version": version,
        "rules": [
            {
                "rule_id": f"T-{index:03d}",
                "title": f"测试规则 {index}",
                "severity": "medium",
                "doc_scope": ["预算", "决算"],
            }
            for index in range(1, rule_count + 1)
        ],
    }
    target = tmp_path / "rules_minimal.yaml"
    target.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# 1. 真实规则文件（正例）
# ---------------------------------------------------------------------------
def test_version_endpoint_reads_real_repository_rules_file(client, monkeypatch):
    monkeypatch.setenv("RULES_FILE", str(REAL_RULES_FILE))

    response = client.get("/api/rules/version")

    assert response.status_code == 200
    body: Dict[str, Any] = response.json()
    assert body["available"] is True
    assert body["ruleset_version"] == "v3_3_all_in_one"
    assert body["metadata_version"] == "v3_3_r2"
    # 与直接解析真实文件的结果一致，而不是任何手写在代码里的数字
    document = yaml.safe_load(REAL_RULES_FILE.read_text(encoding="utf-8"))
    assert body["rule_entry_count"] == len(document["rules"])
    assert body["rule_entry_count"] == 15
    assert body["engine_version"] == ENGINE_VERSION
    assert body["rules_file"] == REAL_RULES_FILE.as_posix()


def test_entries_endpoint_returns_rule_summaries(client, monkeypatch):
    monkeypatch.setenv("RULES_FILE", str(REAL_RULES_FILE))

    response = client.get("/api/rules/entries", params={"limit": 5, "offset": 0})

    assert response.status_code == 200
    body: Dict[str, Any] = response.json()
    assert body["available"] is True
    assert body["total"] == 15
    assert len(body["items"]) == 5
    first = body["items"][0]
    assert first["rule_id"] == "R001"
    # 概要字段固定四项，不透出 detection 正则等内部细节
    assert set(first.keys()) == {"rule_id", "title", "severity", "doc_scope"}


# ---------------------------------------------------------------------------
# 2. 防硬编码（反例：换一个文件，数字必须跟着变）
# ---------------------------------------------------------------------------
def test_rule_entry_count_follows_the_configured_file_not_hardcoded(
    client, monkeypatch, tmp_path
):
    """指向只有 2 条规则的临时 YAML，条目数必须是 2——证明读的是文件。"""
    minimal = _write_minimal_rules_file(tmp_path, rule_count=2, version="test_minimal")
    monkeypatch.setenv("RULES_FILE", str(minimal))

    version_body = client.get("/api/rules/version").json()
    assert version_body["rule_entry_count"] == 2
    assert version_body["ruleset_version"] == "test_minimal"

    entries_body = client.get("/api/rules/entries").json()
    assert entries_body["total"] == 2
    assert [item["rule_id"] for item in entries_body["items"]] == ["T-001", "T-002"]


def test_entries_endpoint_pagination(client, monkeypatch, tmp_path):
    minimal = _write_minimal_rules_file(tmp_path, rule_count=3, version="test_minimal")
    monkeypatch.setenv("RULES_FILE", str(minimal))

    body = client.get("/api/rules/entries", params={"limit": 2, "offset": 1}).json()

    assert body["total"] == 3
    assert [item["rule_id"] for item in body["items"]] == ["T-002", "T-003"]


# ---------------------------------------------------------------------------
# 3. 文件不可读（反例：如实返回 null，不落占位值）
# ---------------------------------------------------------------------------
def test_version_endpoint_reports_missing_file_as_unavailable(client, monkeypatch, tmp_path):
    missing = tmp_path / "not-exists.yaml"
    monkeypatch.setenv("RULES_FILE", str(missing))

    response = client.get("/api/rules/version")

    assert response.status_code == 200
    body: Dict[str, Any] = response.json()
    assert body["available"] is False
    assert body["ruleset_version"] is None
    assert body["metadata_version"] is None
    assert body["rule_entry_count"] is None
    # 引擎版本来自 provenance 模块（与规则文件无关），必须仍然有真实值
    assert body["engine_version"] == ENGINE_VERSION
    assert body["unavailable_reason"]


def test_entries_endpoint_missing_file_returns_empty_items(client, monkeypatch, tmp_path):
    monkeypatch.setenv("RULES_FILE", str(tmp_path / "not-exists.yaml"))

    response = client.get("/api/rules/entries")

    assert response.status_code == 200
    body: Dict[str, Any] = response.json()
    assert body["available"] is False
    assert body["items"] == []
    assert body["total"] is None  # "不知道有多少条"，不是 0 条


def test_version_endpoint_invalid_yaml_is_unavailable(client, monkeypatch, tmp_path):
    """YAML 语法坏掉的文件同样必须优雅降级，不抛 500。"""
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [unclosed", encoding="utf-8")
    monkeypatch.setenv("RULES_FILE", str(broken))

    response = client.get("/api/rules/version")

    assert response.status_code == 200
    assert response.json()["available"] is False


# ---------------------------------------------------------------------------
# 4. 鉴权（反例：非管理员被拒）
# ---------------------------------------------------------------------------
def test_version_endpoint_rejects_non_admin(monkeypatch):
    _deny_admin(monkeypatch)
    app = FastAPI()
    app.include_router(rules_route.router)
    client = TestClient(app)

    assert client.get("/api/rules/version").status_code in {401, 403}
    assert client.get("/api/rules/entries").status_code in {401, 403}


def test_rules_file_resolution_defaults_to_v3_3(monkeypatch, tmp_path):
    """未配置 RULES_FILE 时默认指向 rules/v3_3.yaml（与 /ready 同源）。"""
    monkeypatch.chdir(tmp_path)  # 不受仓库工作目录影响，仅验证相对路径形态
    monkeypatch.delenv("RULES_FILE", raising=False)

    rules_file = rules_route._resolve_rules_file()

    assert rules_file.as_posix() == "rules/v3_3.yaml"
