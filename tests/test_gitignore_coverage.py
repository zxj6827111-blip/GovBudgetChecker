"""Task 14.2 / 缺口 B-11：仓库临时产物的 .gitignore 覆盖度。

整改前的事实：仓库根目录堆了 19 个临时目录（`pip_tmp_*`、`pytest_basetemp_*`、
`.pytest-acceptance*` 等）与十几个临时文件。它们大多已被忽略，但存在漏网模式
（例如无后缀的 `.pytest-acceptance/`、`outputs/`、`tmp_*.json`），
下一次跑测试或导出又会把它们变成"待提交"噪声，进而增加误提交风险。

断言意图（正反对照）：
- 正例：所有已知临时产物模式都必须被 `.gitignore` 覆盖；
- **反例**：源码、测试、文档、部署清单、`.env.example` 绝不能被忽略
  （否则就是 .gitignore 写得太宽，会出现"改了没提交上去"的事故，
  这一档比漏忽略更危险）。

实现用 `git check-ignore`：直接问 git，避免自己实现 gitignore 语义时把测试写成
与实现互为镜像的空洞断言。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

IGNORED_PATHS: List[str] = [
    "pip_tmp_20991231235959/anything",
    "piptmpdl_20991231235959/anything",
    "pytest_basetemp_probe_20991231/anything",
    "pytest_tmp_probe_20991231/anything",
    ".pytest-acceptance/anything",
    ".pytest-acceptance-verify/anything",
    ".codex_pip_tmp/anything",
    "local.db",
    "debug.log",
    "status.txt",
    "tmp-cookies.txt",
    "tmp_wanli_budget_check.json",
    "tmp_any_probe.json",
    "audit-8000.out",
    "audit-8000.err",
    "start-8000.out",
    "start-8000.err",
    "start-3000.out",
    "api/dev-8000.out",
    "api/dev-8000.err",
    "api/database.db",
    "outputs/replay_report.json",
    "test-results/anything",
]

MUST_NOT_BE_IGNORED: List[str] = [
    "api/main.py",
    "api/worker.py",
    "src/utils/logging_config.py",
    "tests/test_gitignore_coverage.py",
    "tests/test_log_message_safety.py",
    "scripts/check_env_consistency.py",
    "scripts/check_log_message_safety.py",
    "docs/RELEASE_RUNBOOK.md",
    "docker-compose.yml",
    "docker-compose.ai.yml",
    ".env.example",
    "rules/v3_3.yaml",
    "Makefile",
    ".github/workflows/ci.yml",
]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (_REPO_ROOT / ".git").exists(),
    reason="需要 git 与 git 工作树才能用 git check-ignore 判定",
)


def _is_ignored(path: str) -> bool:
    """`git check-ignore -q` 退出码：0=被忽略，1=未被忽略。"""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"git check-ignore 异常退出 {result.returncode}")
    return result.returncode == 0


@pytest.mark.parametrize("path", IGNORED_PATHS)
def test_temp_artifacts_are_ignored(path: str) -> None:
    assert _is_ignored(path), f"临时产物未被 .gitignore 覆盖：{path}"


@pytest.mark.parametrize("path", MUST_NOT_BE_IGNORED)
def test_source_files_are_not_ignored(path: str) -> None:
    assert not _is_ignored(path), f".gitignore 过宽，把需要版本控制的文件挡掉了：{path}"


def test_ignore_rules_cover_every_known_temp_family() -> None:
    """每个已知临时目录家族都要有对应规则。

    这里比对的是"家族前缀"而不是具体目录名：具体名字带时间戳，
    逐个登记必然过时，而前缀是稳定的。

    刻意不断言"根目录当前没有临时目录残留"——CI 上永远没有这些目录，
    那种断言在 CI 里是恒真的，等于没有测试。
    """
    families = {
        "pip_tmp_": "pip_tmp_20991231235959/x",
        "piptmpdl_": "piptmpdl_20991231235959/x",
        "pytest_basetemp_": "pytest_basetemp_x/x",
        "pytest_tmp_": "pytest_tmp_x/x",
        ".pytest-acceptance": ".pytest-acceptance/x",
        ".pytest-acceptance-": ".pytest-acceptance-anything/x",
        ".codex_pip_tmp": ".codex_pip_tmp/x",
    }
    uncovered = [family for family, sample in families.items() if not _is_ignored(sample)]
    assert uncovered == [], f"这些临时目录家族没有 .gitignore 规则：{uncovered}"
