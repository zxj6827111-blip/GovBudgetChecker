"""语料入库纪律回归：corpus/ 下的原始 PDF 永不入库，人工标注文件可入库。

来由（真缺陷，不是假想）：DOC-B1-### 的忽略规则最初把内容忽略模式写成
``corpus/DOC-B1-/*``——目录段 ``DOC-B1-`` 后直接跟 ``/``，等于要求目录名
恰好是 "DOC-B1-"，对 ``corpus/DOC-B1-001/`` 永不匹配。实测 ``git add
corpus/`` 会直接列出一批 ``sample.pdf``，与清单写死的「PDF 永不入 git」
纪律冲突（政府公开材料是百 KB 级二进制，入库既污染仓库也不该随代码
分发）。目录段补上通配符 ``corpus/DOC-B1-*/*`` 后恢复正确语义。
本测试用真实 ``git check-ignore`` 跑规则，防止该写法回归（已做变异验证：
改回缺 ``*`` 的写法有 6 条转红）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 未被枚举的新材料类型也必须落在忽略面内（防止只拦 .pdf 的窄规则）
_MUST_BE_IGNORED = [
    "corpus/DOC-B1-001/sample.pdf",
    "corpus/DOC-B1-030/sample.pdf",
    "corpus/DOC-B1-001/source.pdf",
    "corpus/DOC-B1-001/dump.json",
    "corpus/DOC-B1-001/notes.txt",
    "corpus/DOC-B1-001/subdir/anything.bin",
    "corpus/DOC-20260905-001/sample.pdf",
]

#: 标注与台账必须可入库（干净 checkout 才能复现 GATE 与登记台账）
_MUST_BE_TRACKABLE = [
    "corpus/manifest.csv",
    "corpus/DOC-B1-001/golden.json",
    "corpus/DOC-B1-001/ANNOTATIONS.md",
    "corpus/DOC-20260905-001/golden.json",
    "corpus/DOC-20260905-001/ANNOTATIONS.md",
]


def _git_check_ignore(path: str) -> bool:
    """返回该路径是否被 .gitignore 忽略（与 git add 的判定同源）。"""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"git check-ignore 不可用（exit={result.returncode}）")
    return result.returncode == 0


def _require_git_repo() -> None:
    if shutil.which("git") is None:
        pytest.skip("环境无 git")
    probe = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("当前不是 git 工作区")


@pytest.mark.parametrize("path", _MUST_BE_IGNORED)
def test_material_files_never_entered_into_git(path: str) -> None:
    _require_git_repo()
    assert _git_check_ignore(path), (
        f"{path} 未被忽略——corpus/ 下的原始材料可能被 git add 误提交"
        "（检查 .gitignore 的 corpus/DOC-B1-*/* 内容忽略模式是否漏了目录段通配符）"
    )


@pytest.mark.parametrize("path", _MUST_BE_TRACKABLE)
def test_annotation_files_remain_trackable(path: str) -> None:
    _require_git_repo()
    assert not _git_check_ignore(path), (
        f"{path} 被忽略了——干净 checkout 将失去评测标注/登记台账，"
        "golden GATE 无法复现"
    )
