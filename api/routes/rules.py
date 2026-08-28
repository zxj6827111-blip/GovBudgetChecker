"""规则集与引擎版本只读端点（UI 重建第四批 Task 8.3）。

背景
----
M2 建成了 finding 级版本留痕（``src/utils/provenance.py``，写入
``result.meta.versions``），但"当前生效的规则集是什么版本、引擎是什么版本"
一直没有 API 出口——`/api/config` 只含 AI 与队列开关，前端「规则与版本」页
无处取数。本路由补上这个只读出口。

鉴权
----
`require_admin`（与 `/api/metrics`、`/api/activity` 同一先例）。
规则文件路径与内部规则条目属于部署细节，不向普通审核员暴露。

红线（任务书明确要求）
----------------------
1. **必须读真实文件与真实版本，禁止硬编码**。所有字段来自
   ``RULES_FILE`` 指向的 YAML 与 ``provenance.ENGINE_VERSION`` 的真实解析结果；
2. 读不到就如实返回 ``null``（版本）/ ``None``（条目数）+ ``available: false``
   与 ``unavailable_reason``，不得用占位版本号或 0 条冒充；
3. 只读，不提供任何修改规则的入口。

路径解析口径与 ``api/routes/health.py`` 的 ``/ready`` 一致：``RULES_FILE``
按进程工作目录解析相对路径，两处必须同源，否则会出现"/ready 说规则文件
存在、本端点却读不到"的分裂。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Query, Request

from api.auth_utils import require_admin
from src.utils.provenance import ENGINE_VERSION

router = APIRouter()

#: 默认规则文件，与 api/config.py 的 DEFAULT_RULES_FILE / health.py 的 /ready 同源
_DEFAULT_RULES_FILE = "rules/v3_3.yaml"


def _resolve_rules_file() -> Path:
    """解析当前生效的规则文件路径（与 /ready 的解析方式保持一致）。"""
    return Path(os.getenv("RULES_FILE", _DEFAULT_RULES_FILE))


def _load_rules_document(rules_file: Path) -> Optional[Dict[str, Any]]:
    """读取规则 YAML；文件缺失/解析失败/非映射结构时返回 None，不抛错。"""
    try:
        payload = yaml.safe_load(rules_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_rules_list(document: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    rules = document.get("rules")
    if not isinstance(rules, list):
        return None
    return [item for item in rules if isinstance(item, dict)]


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


@router.get("/api/rules/version")
async def get_rules_version(request: Request) -> Dict[str, Any]:
    """当前生效规则集（路径/版本/条目数）与引擎版本。

    所有字段来自真实文件解析；文件不可读时 ``available: false`` 且版本字段
    为 null，前端显示"未识别到"，绝不落占位值。
    """
    require_admin(request)

    rules_file = _resolve_rules_file()
    document = _load_rules_document(rules_file)

    if document is None:
        return {
            "available": False,
            "unavailable_reason": (
                f"规则文件不可读或不是有效的规则集 YAML：{rules_file.as_posix()}"
            ),
            "rules_file": rules_file.as_posix(),
            "ruleset_version": None,
            "metadata_version": None,
            "rule_entry_count": None,
            "engine_version": ENGINE_VERSION,
        }

    rules_list = _read_rules_list(document)
    metadata = document.get("metadata")

    return {
        "available": True,
        "unavailable_reason": None,
        "rules_file": rules_file.as_posix(),
        # YAML 顶层 version（如 v3_3_all_in_one / budget_v3_3_draft）
        "ruleset_version": _optional_text(document.get("version")),
        # metadata.version（规则文件的修订号，如 v3_3_r2），没有则为 null
        "metadata_version": (
            _optional_text(metadata.get("version")) if isinstance(metadata, dict) else None
        ),
        "rule_entry_count": len(rules_list) if rules_list is not None else None,
        "engine_version": ENGINE_VERSION,
    }


@router.get("/api/rules/entries")
async def get_rules_entries(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """规则条目概要列表（rule_id / title / severity / doc_scope）。

    只暴露概要字段，不透出 detection 正则、示例等内部细节——
    这个端点的消费方是「规则与版本」页的展示，不是规则调试。
    文件不可读时 total 为 None（"不知道有多少条"），items 为空数组。
    """
    require_admin(request)

    rules_file = _resolve_rules_file()
    document = _load_rules_document(rules_file)

    if document is None:
        return {
            "available": False,
            "unavailable_reason": (
                f"规则文件不可读或不是有效的规则集 YAML：{rules_file.as_posix()}"
            ),
            "rules_file": rules_file.as_posix(),
            "items": [],
            "total": None,
            "limit": limit,
            "offset": offset,
        }

    rules_list = _read_rules_list(document) or []

    def _entry_summary(rule: Dict[str, Any]) -> Dict[str, Any]:
        doc_scope = rule.get("doc_scope")
        return {
            "rule_id": _optional_text(rule.get("rule_id")) or "未识别到",
            "title": _optional_text(rule.get("title")) or "未识别到",
            "severity": _optional_text(rule.get("severity")) or "未识别到",
            "doc_scope": [str(item) for item in doc_scope] if isinstance(doc_scope, list) else [],
        }

    return {
        "available": True,
        "unavailable_reason": None,
        "rules_file": rules_file.as_posix(),
        "items": [_entry_summary(rule) for rule in rules_list[offset : offset + limit]],
        "total": len(rules_list),
        "limit": limit,
        "offset": offset,
    }
