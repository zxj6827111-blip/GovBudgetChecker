"""分析结果的版本留痕（缺口 P2-02）。

目标：任意一条 finding 都能回答"是哪个规则版本、哪个模型、哪版提示词、哪版引擎产出的"，
使历史结果可复现、可归因。

原则：
- 只写真实可解析到的值，解析不到就留 ``None``，绝不写 ``"unknown"`` 之类的占位值
  （与 M1 "年份识别不到就返回 None、不兜底 2000" 的口径保持一致）；
- 提示词版本用模板内容哈希派生，改了提示词版本号自动变化，避免忘记手工升版。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: 传统（legacy）流水线固定使用 ``src/engine/rules_v33`` 及其配套规则模块，
#: 没有可配置的规则版本入口，这里显式记录其对应的规则集版本。
DEFAULT_RULE_SET_VERSION = "v3_3"

#: finding 上承载版本留痕的字段名，供汇总与测试共用
FINDING_VERSION_FIELDS = (
    "rule_version",
    "model_version",
    "prompt_version",
    "engine_version",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_declared_project_version() -> Optional[str]:
    """读取本仓库声明的版本号。

    优先用已安装包的元数据；仓库以源码方式运行（未 pip install）时回退解析
    ``pyproject.toml``。两者都拿不到就返回 None，由调用方如实留空。
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return str(version("govbudgetchecker")).strip() or None
        except PackageNotFoundError:
            pass
    except Exception:
        pass

    try:
        import tomllib

        pyproject = _PROJECT_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        declared = str(data.get("project", {}).get("version") or "").strip()
        return declared or None
    except Exception:
        return None


#: 引擎版本：解析一次并缓存，避免每条 finding 都读文件
ENGINE_VERSION: Optional[str] = _read_declared_project_version()


def prompt_version_from_template(prompt_id: str, template: str) -> str:
    """由提示词模板内容派生版本号。

    形如 ``full_report_audit_direct@sha1:1a2b3c4d``。用内容哈希而不是手写版本号，
    是为了保证提示词一改动、留痕版本必然跟着变。

    注意：传入的必须是"模板"（不含待审文档正文），否则每份文档都会算出不同版本。
    """
    digest = hashlib.sha1(template.encode("utf-8")).hexdigest()[:8]
    return f"{prompt_id}@sha1:{digest}"


def build_model_version(provider: Any, model: Any) -> Optional[str]:
    """把提供商与模型名拼成模型版本标识。

    两者都缺失时返回 None——宁可留空，也不要写出一个看起来有效的假标识。
    """
    provider_text = str(provider or "").strip()
    model_text = str(model or "").strip()
    if provider_text and model_text:
        return f"{provider_text}/{model_text}"
    return model_text or provider_text or None


def build_finding_provenance(
    *,
    model_version: Optional[str] = None,
    prompt_version: Optional[str] = None,
    source_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 AI 原始问题上携带的版本留痕块。

    AI 侧的模型/提示词信息只有调用点知道，需要顺着原始问题字典透传到 IssueItem，
    这里统一结构，避免各调用点各写一套 key。
    """
    return {
        "model_version": model_version,
        "prompt_version": prompt_version,
        "source_channel": source_channel,
    }


def read_finding_provenance(raw_issue: Any) -> Dict[str, Optional[str]]:
    """从 AI 原始问题字典中读回版本留痕；缺失时各字段为 None。"""
    block = raw_issue.get("provenance") if isinstance(raw_issue, dict) else None
    if not isinstance(block, dict):
        block = {}
    result: Dict[str, Optional[str]] = {}
    for key in ("model_version", "prompt_version", "source_channel"):
        value = str(block.get(key) or "").strip()
        result[key] = value or None
    return result


def summarize_finding_versions(findings: Iterable[Any]) -> Dict[str, Any]:
    """汇总一批 finding 用到的版本，写入结果 ``meta.versions``。

    每个字段给出去重后的有序列表，便于一眼看出"这次结果混用了几个模型/提示词"；
    另外单独给出当前进程的 ``engine_version``。
    """
    buckets: Dict[str, set] = {field: set() for field in FINDING_VERSION_FIELDS}
    for finding in findings:
        if isinstance(finding, dict):
            getter = finding.get
        else:
            def getter(key: str, _obj: Any = finding) -> Any:
                return getattr(_obj, key, None)
        for field in FINDING_VERSION_FIELDS:
            value = str(getter(field) or "").strip()
            if value:
                buckets[field].add(value)

    summary: Dict[str, Any] = {"engine_version": ENGINE_VERSION}
    for field in FINDING_VERSION_FIELDS:
        values: List[str] = sorted(buckets[field])
        summary[f"{field}s"] = values
    return summary
