"""金标基线硬依赖的交付完整性回归（未跟踪文件审计 2026-11-12 §3.1/§10.1）。

两个已跟踪入口依赖四份从未入库的文件，干净检出即坏，而既有测试测不到：
- ``scripts/gold_baseline_eval_fp_batch1.py`` 的 ``main()``：先按默认路径用 importlib
  加载 ``gold_baseline_match.py``，再 ``sys.path`` 插入后 ``import gold_baseline_decisions_r2``
  （后者模块级再导入 v1 底本 ``gold_baseline_decisions``）；
- ``scripts/verify_pending_review.py`` 的 ``manifest`` 子命令：``--gold-csv`` 默认指向
  ``docs/GOLD_STANDARD_SAMPLE_20261101.csv``，``manifest_main()`` 第一步就无条件 open()。

既有 ``test_gold_baseline_eval_guard.py`` 只做纯函数测试、``test_verify_pending_review.py``
自带夹具，都不触发这些惰性加载，于是 CI 全绿掩盖了缺文件。本文件按真实调用路径加载
依赖，并要求四份文件位于 Git 索引，防止"本地能跑、检出即坏"再次回归。

gold/ 标注层与 outputs/ 运行产物按审计 §七 属后续阶段，不在本轮范围：因此这里不调用
需要它们的 ``load_gold()``/``manifest_main()`` 完整流程，只走干净检出下会失败的那几步。
"""
from __future__ import annotations

import csv
import importlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MATCH_SCRIPT = REPO / "scripts" / "gold_baseline_match.py"
DECISIONS_V1 = REPO / "scripts" / "gold_baseline_decisions.py"
DECISIONS_R2 = REPO / "scripts" / "gold_baseline_decisions_r2.py"
GOLD_SAMPLE_CSV = REPO / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
HARD_DEPS = (MATCH_SCRIPT, DECISIONS_V1, DECISIONS_R2, GOLD_SAMPLE_CSV)

EXPECTED_CSV_HEADER = ["sample_id", "year", "kind", "department", "file", "path", "pages", "text_layer", "sha256"]
ALLOWED_FINALS = {"TP", "FP_GOLD", "ADJ_TRUE", "ADJ_FALSE", "INDET",
                  "HIT", "NOISE", "TRUE_NOT_IN_GOLD", "OUT_OF_SCOPE"}
#: 冻结清单：gold/FROZEN.csv 对应的 33 份人工标注（gold_verify.py --all 实测 33/33 通过）。
FROZEN_SAMPLE_COUNT = 33


def _load_eval_entry():
    """按 test_gold_baseline_eval_guard.py 的既有方式加载评估入口模块。"""
    spec = importlib.util.spec_from_file_location(
        "gold_baseline_eval_fp_batch1", REPO / "scripts" / "gold_baseline_eval_fp_batch1.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


def _load_match_module_via_eval_entry():
    """走 eval 入口 main() 的真实加载路径（gold_baseline_eval_fp_batch1.py:483→:46）。

    ``--match-script`` 缺省值就是 scripts/gold_baseline_match.py；缺该文件时
    ``load_match_module`` 会 SystemExit，本测试随之失败。
    """
    entry = _load_eval_entry()
    assert entry.DEFAULT_MATCH == MATCH_SCRIPT
    return entry.load_match_module(entry.DEFAULT_MATCH)


def _import_decisions_via_eval_entry():
    """走 eval 入口 main() 的真实导入路径（gold_baseline_eval_fp_batch1.py:500-501）。

    sys.path 插入 scripts/ 后普通 import；r2 模块级 ``import gold_baseline_decisions``
    连带加载 v1 底本。结束时还原 sys.path 并移除本次导入的模块，保证用例互相独立、
    可重复。
    """
    scripts_dir = str(MATCH_SCRIPT.parent)
    sys.path.insert(0, scripts_dir)
    for name in ("gold_baseline_decisions_r2", "gold_baseline_decisions"):
        sys.modules.pop(name, None)
    try:
        r2 = importlib.import_module("gold_baseline_decisions_r2")
        v1 = importlib.import_module("gold_baseline_decisions")
        return r2, v1
    finally:
        for name in ("gold_baseline_decisions_r2", "gold_baseline_decisions"):
            sys.modules.pop(name, None)
        if scripts_dir in sys.path:
            sys.path.remove(scripts_dir)


def test_eval_entry_loads_match_and_decisions_from_default_paths():
    """干净检出下 eval 入口的两个惰性依赖必须能按默认路径加载。"""
    gm = _load_match_module_via_eval_entry()
    r2, v1 = _import_decisions_via_eval_entry()
    # 匹配/真值/指标模块就位，且其金标输入 CSV 常量指向本仓库这份文件（gold_baseline_match.py:33）
    assert gm.SAMPLE_CSV == GOLD_SAMPLE_CSV and gm.SAMPLE_CSV.exists()
    assert r2.D and v1.D
    assert all(row["final"] in ALLOWED_FINALS for row in r2.D.values())


def test_decisions_r2_layers_on_v1_base():
    """r2 只在 v1 底本上改判既有条目；抽检历史上已裁定的键防内容在传输中被改动。"""
    r2, v1 = _import_decisions_via_eval_entry()
    # r2 的 upd() 自带 key-in-D 断言：不新增、不删除条目
    assert set(r2.D) == set(v1.D)
    # r2 改判条目：v1 记 FP_GOLD（金标漏标），R9 轮金标采纳后连边改 TP
    key = "GS-2024-03|V33-227-9"
    assert v1.D[key]["final"] == "FP_GOLD" and v1.D[key]["amend"] is True
    assert r2.D[key]["final"] == "TP" and r2.D[key]["links"] == ["GS-2024-03|C|GS-N05|2"]
    key = "GS-2025-09|V33-105-7"
    assert v1.D[key]["final"] == "FP_GOLD" and r2.D[key]["final"] == "TP"
    # r2 新裁定只改原因标签：final 不变、cause 更新为 R004_RULED（R26 细化）
    key = "GS-2022-07|V33-TREND-COMPLETION-RATE-2"
    assert r2.D[key]["final"] == "FP_GOLD" and r2.D[key]["cause"] == "R004_RULED"
    assert v1.D[key]["final"] == "FP_GOLD" and v1.D[key]["cause"] == "R004"
    # v1 底本条目原样保留（未被 r2 触碰）
    key = "GS-2022-06|V33-249-9"
    assert r2.D[key] == v1.D[key] and r2.D[key]["final"] == "TP"


def test_gold_sample_csv_parses_like_gold_and_manifest_readers():
    """按 load_gold()/manifest_main() 的同一读法解析 CSV，并核对冻结清单的结构不变量。"""
    with GOLD_SAMPLE_CSV.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == EXPECTED_CSV_HEADER
        rows = list(reader)
    assert len(rows) == FROZEN_SAMPLE_COUNT
    ids = [row["sample_id"] for row in rows]
    assert len(set(ids)) == FROZEN_SAMPLE_COUNT
    assert all(re.fullmatch(r"GS-\d{4}-\d{2}", sid) for sid in ids)
    shas = [row["sha256"] for row in rows]
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in shas)
    assert len(set(shas)) == FROZEN_SAMPLE_COUNT
    assert {int(row["year"]) for row in rows} == {2022, 2023, 2024, 2025}
    # 人工裁决抽检引用的样本必须在样本注册表里（decisions ↔ CSV 一致）
    for sid in ("GS-2024-03", "GS-2025-09", "GS-2022-07", "GS-2022-06"):
        assert sid in set(ids)


def test_verify_pending_review_manifest_gold_csv_default_readable():
    """manifest 子命令的 --gold-csv 默认文件必须存在且按 manifest_main() 第一步可读。"""
    spec = importlib.util.spec_from_file_location(
        "verify_pending_review_delivery", REPO / "scripts" / "verify_pending_review.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    # main() 构造 manifest 子命令时的默认值表达式（verify_pending_review.py:2479）
    gold_csv = harness.REPO_ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
    assert gold_csv == GOLD_SAMPLE_CSV and gold_csv.exists()
    # manifest_main() 第一步：无条件 open + DictReader 取 sha256 列（干净检出下此处 FileNotFoundError）
    with gold_csv.open(encoding="utf-8-sig") as handle:
        gold = sorted({row["sha256"] for row in csv.DictReader(handle)})
    assert len(gold) == FROZEN_SAMPLE_COUNT
    # gold_exclusion.json 登记的 source_sha256 用同一辅助函数可计算
    assert harness.sha256_file(gold_csv)


def test_hard_dependencies_are_tracked_by_git():
    """干净检出的真正保证：四份硬依赖必须在 Git 索引里（缺一即为残缺交付）。"""
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=REPO, capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("非 Git 工作树（如导出归档）运行，跳过索引检查")
    for path in HARD_DEPS:
        rel = path.relative_to(REPO).as_posix()
        checked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                                 cwd=REPO, capture_output=True, text=True)
        assert checked.returncode == 0 and rel in checked.stdout.splitlines(), (
            f"{rel} 未纳入 Git 版本管理：干净检出后 eval 入口与 manifest 子命令将直接失败")


def test_eval_entry_reference_exceptions_default_present():
    """eval 入口另一个默认输入 --reference-exceptions（:485）必须随仓库存在。"""
    entry = _load_eval_entry()
    exceptions = entry.HERE / "fp_batch1_reference_exceptions.json"
    assert exceptions.exists(), "eval 入口的 --reference-exceptions 默认文件缺失，评估流程无法启动"
