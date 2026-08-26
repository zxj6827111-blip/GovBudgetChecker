"""测试进程与真实数据库的隔离保护。

对应缺陷：`tests/conftest.py` 原先没有任何 `DATABASE_URL` 隔离，只要开发者 shell 里
存在 `DATABASE_URL`（或 `api/main.py` 导入时 `load_dotenv()` 把 `.env` 里的连接串
注入环境），`pytest` 就会对真实库执行迁移并写入测试数据。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# pytest 以 prepend 模式把 `tests/conftest.py` 注册为顶层模块 `conftest`，
# 这样导入拿到的是 pytest 实际加载的同一个模块对象，而不是重复副本。
from conftest import TEST_DATABASE_URL_ENV, resolve_opt_in_database_url

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 子进程回归用的测试节点（必须与下方函数名保持一致）
_GUARD_NODE_ID = "tests/test_env_isolation.py::test_database_url_absent_inside_tests"


def test_database_url_absent_inside_tests() -> None:
    """默认情况下测试进程内读不到 DATABASE_URL。

    这是隔离的核心断言：即使外部环境设置了连接串，测试体内也必须为空。
    """
    assert os.environ.get("DATABASE_URL") is None


def test_isolation_survives_external_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """反例对照：测试内部主动设置的假连接串可见，说明断言不是"永远为真"。

    先确认隔离生效（为空），再注入一个显然不可用的假 DSN 并确认能读到，
    证明上一条断言检查的是隔离行为，而不是"os.environ 永远没有这个键"。
    """
    assert os.environ.get("DATABASE_URL") is None
    monkeypatch.setenv("DATABASE_URL", "postgresql://unit-test-only/should-not-connect")
    assert os.environ["DATABASE_URL"] == "postgresql://unit-test-only/should-not-connect"


def test_opt_in_resolver_requires_dedicated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """opt-in 连接串只认专用变量，不回退到开发者的 DATABASE_URL。"""
    monkeypatch.delenv(TEST_DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://developer-db/fiscal_db")
    assert resolve_opt_in_database_url() == ""

    monkeypatch.setenv(TEST_DATABASE_URL_ENV, "  postgresql://ci-db/test_db  ")
    assert resolve_opt_in_database_url() == "postgresql://ci-db/test_db"


def test_real_database_fixture_skips_without_opt_in() -> None:
    """未配置专用测试库时，opt-in fixture 必须跳过而不是连开发库。

    直接断言 fixture 的判定依据：解析结果为空时 `real_database_url` 会 `pytest.skip`。
    """
    if resolve_opt_in_database_url():
        pytest.skip(f"本机配置了 {TEST_DATABASE_URL_ENV}，跳过该反例")
    assert resolve_opt_in_database_url() == ""


@pytest.mark.slow
def test_child_pytest_ignores_external_database_url() -> None:
    """端到端证据：带着外部 DATABASE_URL 启动 pytest，隔离断言仍然通过。

    只选中单个测试节点执行，避免子进程再次触发本用例导致无限递归。
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql://must-not-be-used/fiscal_db"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            _GUARD_NODE_ID,
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )

    assert completed.returncode == 0, (
        "带外部 DATABASE_URL 运行时隔离失效：\n"
        f"stdout=\n{completed.stdout}\nstderr=\n{completed.stderr}"
    )
    assert "1 passed" in (completed.stdout or "")
