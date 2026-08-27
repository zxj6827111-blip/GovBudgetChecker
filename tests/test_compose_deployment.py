"""Task 14.1 / 缺口 B-10：统一部署清单 + 环境变量三方对账。

整改前的事实：
- `docker-compose.yml` 只有 postgres，`docker-compose.ai.yml` 只有 ai-extractor /
  backend / frontend，**任何 compose 文件里都没有 worker 服务定义**，而 README 早已
  声明 `JOB_QUEUE_ROLE=api/worker` 分离模式 —— 按文档部署会得到一个"只入队没人消费"
  的系统。
- `docker-compose.ai.yml` 给 backend 传 `RULES_FILE_PATH=/app/engine/rules_v33.py`，
  但代码读的是 `RULES_FILE`（`api/config.py:9`），且应指向 YAML 规则集。
  这类"配了却无效"的变量必须有机器检查兜住。

断言意图（正反对照）：
1. 五个组件齐全；worker 有启动命令、没有端口。
2. backend 与 worker 必须共享同一份 UPLOAD_DIR 与同一组卷（队列 claim 锁是
   UPLOAD_DIR 下的文件锁，不共享就会重复消费）；
   **反例**：两者的队列角色必须不同，否则就不是 api/worker 分离。
3. 环境变量三方对账为 0 违规（回归线）。
4. 对账解析器自身的正反对照：能识别注释掉的可选项声明、能跳过整行注释的 compose 示例。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from scripts.check_env_consistency import (
    analyze,
    parse_compose_env,
    parse_env_declarations,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED_SERVICES = {"postgres", "ai-extractor", "backend", "worker", "frontend"}


def _load_compose() -> Dict[str, Any]:
    """合并两份 compose 的 services（模拟 `-f a -f b` 的浅合并）。

    这里不调用 docker CLI：CI 上不一定有 docker，且本测试要断言的是清单本身的结构，
    不是 docker 的插值行为（插值行为已在本地用 `docker compose config` 实测）。
    """
    merged: Dict[str, Any] = {}
    for rel in ("docker-compose.yml", "docker-compose.ai.yml"):
        data = yaml.safe_load((_REPO_ROOT / rel).read_text(encoding="utf-8")) or {}
        for name, spec in (data.get("services") or {}).items():
            merged[name] = spec
    return merged


def test_compose_defines_all_five_components() -> None:
    services = _load_compose()
    assert set(services) == _EXPECTED_SERVICES


def test_worker_service_runs_worker_module_without_ports() -> None:
    worker = _load_compose()["worker"]
    assert worker["command"] == ["python", "-m", "api.worker"]
    # worker 不该占端口：它没有 HTTP 面
    assert "ports" not in worker


def test_backend_and_worker_share_upload_dir_and_volumes() -> None:
    services = _load_compose()
    backend_env = services["backend"]["environment"]
    worker_env = services["worker"]["environment"]

    assert backend_env["UPLOAD_DIR"] == worker_env["UPLOAD_DIR"] == "/app/uploads"
    assert services["backend"]["volumes"] == services["worker"]["volumes"]
    assert any(str(item).endswith(":/app/uploads") for item in services["worker"]["volumes"])


def test_backend_and_worker_queue_roles_are_split() -> None:
    """反例断言：两个服务的队列角色必须不同，且 backend 不做 inline 兜底。"""
    services = _load_compose()
    backend_env = services["backend"]["environment"]
    worker_env = services["worker"]["environment"]

    assert backend_env["JOB_QUEUE_ROLE"] == "api"
    assert worker_env["JOB_QUEUE_ROLE"] == "worker"
    assert backend_env["JOB_QUEUE_ROLE"] != worker_env["JOB_QUEUE_ROLE"]
    assert str(backend_env["JOB_QUEUE_INLINE_FALLBACK"]).lower() == "false"

    # 除了队列角色，两者环境必须完全一致（同一个锚点），否则容易漂移出"只有 backend
    # 配了 DATABASE_URL"这类事故
    diff = {
        key
        for key in set(backend_env) | set(worker_env)
        if backend_env.get(key) != worker_env.get(key)
    }
    assert diff == {"JOB_QUEUE_ROLE", "JOB_QUEUE_INLINE_FALLBACK"}


def test_compose_does_not_set_rules_file_path_alias() -> None:
    """历史遗留：RULES_FILE_PATH 从来没有读取方，必须换成代码真正读的 RULES_FILE。"""
    services = _load_compose()
    for name in ("backend", "worker"):
        env = services[name]["environment"]
        assert "RULES_FILE_PATH" not in env
        assert env["RULES_FILE"].endswith(".yaml")


def test_env_vars_are_consistent_across_code_example_and_compose() -> None:
    """回归线：新增死变量或漏声明变量会让这条测试和 CI 一起变红。"""
    report = analyze()
    violations = report["violations"]
    assert violations == [], "\n".join(str(item["detail"]) for item in violations)


# ---------------------------------------------------------------------------
# 对账解析器自身的正反对照
# ---------------------------------------------------------------------------
def test_parse_env_declarations_counts_commented_optionals() -> None:
    text = "\n".join(
        [
            "ACTIVE_VAR=1",
            "# OPTIONAL_VAR=2",
            "#   SPACED_VAR = 3",
            "# 这是一句纯说明，不是声明",
            "lowercase_var=4",
        ]
    )
    assert parse_env_declarations(text) == {"ACTIVE_VAR", "OPTIONAL_VAR", "SPACED_VAR"}


def test_parse_compose_env_skips_commented_lines() -> None:
    text = "\n".join(
        [
            "    SET_VAR: ${REFERENCED_VAR:-fallback}",
            "    # COMMENTED_VAR: ${ALSO_COMMENTED}",
            "      - LIST_VAR=${LIST_REF}",
        ]
    )
    parsed = parse_compose_env(text)

    assert {"SET_VAR", "REFERENCED_VAR", "LIST_VAR", "LIST_REF"} <= set(parsed)
    # 反例：注释掉的示例配置不算"部署以为配了"
    assert "COMMENTED_VAR" not in parsed
    assert "ALSO_COMMENTED" not in parsed


@pytest.mark.parametrize("rel", ["docker-compose.yml", "docker-compose.ai.yml"])
def test_compose_files_are_valid_yaml(rel: str) -> None:
    data = yaml.safe_load((_REPO_ROOT / rel).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("services")
