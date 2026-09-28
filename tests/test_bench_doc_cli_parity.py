"""文档 ↔ 命令行接口对齐守护。

来由（真缺陷，两例）：签字单曾让用户执行 `--subset budget-unit`（工具白名单当时没有该值，
照单执行直接失败）；登记示例又**完全没带 `--depth`**（工具支持、`by_depth` 分层依赖它，
漏了会让 §4.4 的标注深度分层静默失效）。这两处都不是代码 bug，而是「文档写了、工具不认」
或「工具支持、文档没接」——没有任何既有测试能发现。

本测试把对账表 `docs/B1_DELIVERY_AUDIT_20260928.md` 里给出的命令逐条与工具真实 `--help`
比对：**文档里出现的每个选项都必须能在对应脚本的 help 里找到**。跑 `--help` 即退出，
不产生副作用、不依赖语料。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC = _REPO_ROOT / "docs" / "B1_DELIVERY_AUDIT_20260928.md"

#: 文档里允许出现但不由 argparse 定义的占位（如 <材料.pdf> 这类位置参数写法）
_ALLOWED_EXTRA = {"--pdf"}


def _documented_options() -> dict:
    text = _DOC.read_text(encoding="utf-8")
    found: dict = {}
    for match in re.finditer(r"python scripts/(\w+)\.py((?:[^\n]*\\\n)*[^\n]*)", text):
        script, args = match.group(1), match.group(2)
        found.setdefault(script, set()).update(re.findall(r"--[a-z-]+", args))
    return found


def _script_help(script: str) -> str:
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / f"{script}.py"), "--help"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), check=False,
    )
    return result.stdout + result.stderr


@pytest.mark.skipif(not _DOC.exists(), reason="对账表不存在")
def test_documented_cli_options_exist_in_tools():
    documented = _documented_options()
    assert documented, "对账表里应至少给出一个可执行命令"
    for script, options in documented.items():
        help_text = _script_help(script)
        assert "usage:" in help_text, f"scripts/{script}.py --help 无输出，脚本可能不可用"
        # 必须做**整词**匹配：`--dept` 是 `--depth` 的子串，用 `in` 会被前缀蒙过
        # （本测试首版即栽在这里——变异验证时写错选项却没转红）。
        unknown = sorted(
            o for o in options - _ALLOWED_EXTRA
            if not re.search(re.escape(o) + r"(?![A-Za-z-])", help_text)
        )
        assert not unknown, (
            f"对账表让用户用 {unknown} 调 scripts/{script}.py，但工具不识别——"
            "照文档执行会失败（文档与工具漂移）"
        )


@pytest.mark.skipif(not _DOC.exists(), reason="对账表不存在")
def test_documented_commands_cover_all_four_tools():
    """四个工具都要在文档里有可执行用法——工具存在但没写进流程，等于没交付。"""
    documented = set(_documented_options())
    expected = {"bench_register", "run_benchmark", "eval_benchmark", "bench_fpfn_sheet"}
    missing = expected - documented
    assert not missing, f"这些工具在文档里没有可执行用法: {sorted(missing)}"
