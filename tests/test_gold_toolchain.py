"""金标工具链长期回归测试（金标工具链标准化 P6）。

测试分两类（任务书 P6 的 A/B 分界）：

A 类（CI 必跑，不依赖本地金标/PDF 数据）：
- 14 份工具模块能在干净检出环境加载。pdfplumber/pymupdf 依赖的模块用
  importorskip 防御性跳过（依赖未装的裸环境）；CI 装 requirements.txt
  （含 PyMuPDF>=1.24.0 与 pdfplumber>=0.11.0），这两类在 CI 上实测执行；
- 样本清单注册表（docs/GOLD_STANDARD_SAMPLE_20261101.csv）33 行、
  表头字段完整、sha256 为 64 位十六进制；
- 纯标准库工具的 CLI 参数能正确解析（gold_diff / gold_baseline_run）；
- gold_diff 对最小合法 annotation 自比输出"差异 0 条"（输出协议兼容）；
- 异常输入：gold_baseline_tables 未设 GOLD_BASELINE_REV → SystemExit 带
  明确消息；gold_verify 传入不存在的路径 → 非零退出（锁定现状）。

B 类（本地金标数据存在才跑，CI 干净检出自动 skip）：
- gold_verify --all → 33 份 FROZEN 全部 OK 且退出码 0（冻结校验回归线，
  保护"33 份冻结金标本机继续通过冻结校验"这一交付前提）。
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# 纯标准库模块：CI 装好 requirements-test.txt 即可加载
STDLIB_MODULES = [
    "gold_checks",
    "gold_diff",
    "gold_baseline_run",
    "gold_baseline_tables",
    "gold_baseline_match",
    "gold_baseline_decisions",
    "gold_baseline_decisions_r2",
    "gold_baseline_eval_fp_batch1",
]
# 依赖第三方 PDF 库的模块：requirements.txt 已声明（PyMuPDF>=1.24.0、
# pdfplumber>=0.11.0），CI 实测加载；importorskip 仅在依赖未装的裸环境防御性跳过
PDF_DEP_MODULES = {
    "pdfplumber": ["gold_extract", "gold_build", "gold_autofill", "gold_judge"],
    "pymupdf": ["gold_skeleton", "gold_verify"],
}

SAMPLE_CSV = REPO / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
GOLD_DIR = REPO / "gold"
SAMPLE_COUNT = 33


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )


def test_stdlib_toolchain_modules_load() -> None:
    """A类：纯标准库的 8 份工具模块都能加载（干净检出环境可用性）。"""
    for name in STDLIB_MODULES:
        assert (SCRIPTS / f"{name}.py").is_file(), f"缺少工具文件 {name}.py"
    sys.path.insert(0, str(SCRIPTS))
    try:
        for name in STDLIB_MODULES:
            __import__(name)
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize("lib,modules", sorted(PDF_DEP_MODULES.items()))
def test_pdf_dep_toolchain_modules_load(lib: str, modules: list[str]) -> None:
    """A类：依赖 PDF 库（requirements.txt 已声明）的 6 份模块实测加载。"""
    pytest.importorskip(lib)
    sys.path.insert(0, str(SCRIPTS))
    try:
        for name in modules:
            __import__(name)
    finally:
        sys.path.remove(str(SCRIPTS))


def test_sample_registry_complete() -> None:
    """A类：样本清单注册表 33 行、表头完整、sha256 格式合法。"""
    assert SAMPLE_CSV.is_file(), "样本清单 docs/GOLD_STANDARD_SAMPLE_20261101.csv 不在库内"
    with SAMPLE_CSV.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == SAMPLE_COUNT, f"样本清单应为 {SAMPLE_COUNT} 行，实际 {len(rows)}"
    for field in ("sample_id", "year", "kind", "department", "path", "sha256"):
        assert field in (rows[0] or {}), f"表头缺字段 {field}"
    ids = [r["sample_id"] for r in rows]
    assert len(set(ids)) == SAMPLE_COUNT, "sample_id 有重复"
    for r in rows:
        assert len(r["sha256"]) == 64 and all(c in "0123456789abcdef" for c in r["sha256"]), (
            f"{r['sample_id']} 的 sha256 不是 64 位十六进制"
        )


def test_cli_argparse_stdlib_tools() -> None:
    """A类：纯标准库工具的命令行参数能正确解析。"""
    for script, key_arg in (
        ("gold_diff.py", "--old"),
        ("gold_baseline_run.py", "prepare"),
    ):
        proc = _run(script, "--help")
        assert proc.returncode == 0, f"{script} --help 失败: {proc.stderr}"
        assert "usage" in proc.stdout, f"{script} --help 缺 usage 输出"
        assert key_arg in proc.stdout, f"{script} --help 未解析出关键参数 {key_arg}"


def test_gold_diff_selfcompare_zero(tmp_path: Path) -> None:
    """A类：gold_diff 对同一份最小合法 annotation 自比 → 差异 0 条。"""
    fixture = tmp_path / "annotation.json"
    fixture.write_text(
        '{"sample_id": "TEST-0000", "basic": {}, "checks": [], "free_findings": []}',
        encoding="utf-8",
    )
    out_md = tmp_path / "diff.md"
    out_json = tmp_path / "diff.json"
    proc = _run(
        "gold_diff.py",
        "--old", str(fixture),
        "--new", str(fixture),
        "--out", str(out_md),
        "--json", str(out_json),
    )
    assert proc.returncode == 0, proc.stderr
    assert "差异 0 条" in proc.stdout, proc.stdout
    assert out_md.is_file() and out_json.is_file(), "gold_diff 未写出比对产物"


def test_gold_baseline_tables_requires_rev() -> None:
    """A类：gold_baseline_tables 未设 GOLD_BASELINE_REV → 明确报错退出。"""
    import os

    env = {k: v for k, v in os.environ.items() if k != "GOLD_BASELINE_REV"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "gold_baseline_tables.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "GOLD_BASELINE_REV=r2" in (proc.stdout + proc.stderr), (
        "未设 rev 时缺少指向性报错消息"
    )


def test_gold_verify_missing_path_fails_cleanly() -> None:
    """A类：gold_verify 传入不存在的路径 → 非零退出（锁定现状，不静默通过）。"""
    pytest.importorskip("pymupdf")
    pytest.importorskip("pdfplumber")
    proc = _run("gold_verify.py", "gold/__NO_SUCH__/annotation.json")
    assert proc.returncode != 0, "对不存在的输入应非零退出"


@pytest.mark.skipif(
    not (GOLD_DIR / "FROZEN.csv").is_file(),
    reason="本地金标数据 gold/ 不入库，CI 干净检出自动跳过",
)
def test_frozen_33_all_pass() -> None:
    """B类：33 份冻结金标本机全部通过冻结校验（gold_verify --all）。"""
    pytest.importorskip("pymupdf")
    pytest.importorskip("pdfplumber")
    proc = _run("gold_verify.py", "--all")
    assert proc.returncode == 0, proc.stderr
    ok = [line for line in proc.stdout.splitlines() if line.startswith("FROZEN OK")]
    assert len(ok) == SAMPLE_COUNT, (
        f"FROZEN OK 应为 {SAMPLE_COUNT} 行，实际 {len(ok)}:\n{proc.stdout}"
    )


@pytest.mark.skipif(
    not (GOLD_DIR / "FROZEN.csv").is_file(),
    reason="本地金标数据 gold/ 不入库，CI 干净检出自动跳过",
)
def test_frozen_csv_matches_registry() -> None:
    """B类：FROZEN.csv 的 sample_id 集合与样本清单注册表一致。"""
    with (GOLD_DIR / "FROZEN.csv").open(encoding="utf-8") as f:
        frozen_ids = {r["sample_id"] for r in csv.DictReader(f)}
    with SAMPLE_CSV.open(encoding="utf-8-sig") as f:
        registry_ids = {r["sample_id"] for r in csv.DictReader(f)}
    assert frozen_ids == registry_ids, "冻结清单与样本清单的 sample_id 不一致"
