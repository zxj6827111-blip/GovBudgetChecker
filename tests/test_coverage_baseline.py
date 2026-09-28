"""覆盖基线脚本与清单指纹测试。

批次 A 的验收要求是"基线可复现，计数和证据没有歧义"。可复现的前提是
**基线里的每个数字都能被独立重算**，所以这里既校验形状（关键字段不为空、
应检查事项不为 0），也校验指纹真的跟着清单内容走——指纹若恒定不变，
它就无法承担"两条结论是否同一版检查要求"的判断。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine import check_obligations  # noqa: E402
from src.engine.check_obligations import (  # noqa: E402
    catalog_fingerprint,
    catalog_gaps,
    registered_rule_ids,
)

SCRIPT = ROOT / "scripts" / "check_coverage_baseline.py"


def _load_baseline_module():
    """按文件路径加载脚本模块（scripts/ 不是包）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_coverage_baseline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_baseline_has_no_shape_problems():
    baseline = _load_baseline_module().build_baseline()
    assert _load_baseline_module()._assert_baseline_shape(baseline) == []
    assert baseline["obligation_catalog_fingerprint"]
    assert baseline["catalog_self_check"] == []
    assert set(baseline["kinds"]) == {"budget", "final"}


@pytest.mark.parametrize("kind", ["budget", "final"])
def test_baseline_kind_counts_are_self_consistent(kind):
    baseline = _load_baseline_module().build_baseline()
    payload = baseline["kinds"][kind]
    coverage = payload["coverage"]

    assert payload["rule_registry"]["count"] == len(registered_rule_ids(kind))
    assert coverage["applicable_total"] > 0
    # 逐实例数量必须与汇总计数一致，否则"计数"与"证据"就是两套东西
    assert len(coverage["instances"]) == (
        coverage["applicable_total"] + coverage["not_applicable_total"]
    )
    assert sum(coverage["by_reason"].values()) == coverage["unresolved_total"]
    assert coverage["blocking_total"] <= coverage["unresolved_total"]
    # 无材料基线下的"制度性上限"必须与缺口数对得上
    ceiling = payload["structural_ceiling"]
    assert ceiling["gap_obligation_total"] == coverage["by_reason"].get(
        "not_implemented", 0
    )
    assert ceiling["ceiling_rate"] == pytest.approx(
        (coverage["applicable_total"] - ceiling["gap_obligation_total"])
        / coverage["applicable_total"],
        abs=1e-4,
    )


def test_baseline_gap_list_matches_catalog():
    baseline = _load_baseline_module().build_baseline()
    gaps = baseline["unimplemented_checks"]
    # WP4-H 收口后清单内已无 pending 义务（gaps 为空是合法状态）——
    # 不变式是"基线缺口列表与清单 pending 集合完全一致"，而非非空。
    assert {item["obligation_id"] for item in gaps} == {
        item.obligation_id
        for item in check_obligations.OBLIGATION_CATALOG
        if item.pending_checkers
    }
    # 每一条缺口都要能说清"缺什么"，否则用户无法判断这条要求是否已被覆盖
    for item in gaps:
        assert item["pending_checkers"]
        assert item["gap_note"]
        assert item["basis"]


def test_fingerprint_is_stable_and_follows_catalog_content(monkeypatch):
    first = catalog_fingerprint()
    assert first == catalog_fingerprint(), "同一进程内指纹必须稳定"

    target = check_obligations.OBLIGATION_CATALOG[0]
    mutated = replace(target, checkers_by_kind={"final": ("V33-001", "V33-999")})
    monkeypatch.setattr(
        check_obligations,
        "OBLIGATION_CATALOG",
        (mutated, *check_obligations.OBLIGATION_CATALOG[1:]),
        raising=True,
    )
    assert catalog_fingerprint() != first, "checker 变化必须改变指纹"


def test_catalog_gaps_are_ordered_and_complete():
    gaps = catalog_gaps()
    assert len(gaps) == len(
        [item for item in check_obligations.OBLIGATION_CATALOG if item.pending_checkers]
    )
    for item in gaps:
        assert item["group_title"]
        assert item["title"]


def test_cli_gap_gate_blocks_unexpected_capability_change():
    """缺口数变化必须让门禁失败。

    这条守住"能力悄悄变化"：新实现一条检查（缺口数减少）或新增一条要求
    （缺口数增加）都应该被人显式确认，而不是让完成率自己漂移。
    """
    env = dict(os.environ)
    env["GOVBUDGET_AUTH_ENABLED"] = "false"
    env["GOVBUDGET_API_KEY"] = "dev"

    expected = len(catalog_gaps())

    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "--assert-gaps", str(expected)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr

    mismatch = subprocess.run(
        [sys.executable, str(SCRIPT), "--assert-gaps", str(expected + 1)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert mismatch.returncode == 1
    assert "门禁失败" in mismatch.stderr


def test_cli_json_output_is_machine_readable(tmp_path):
    env = dict(os.environ)
    env["GOVBUDGET_AUTH_ENABLED"] = "false"
    env["GOVBUDGET_API_KEY"] = "dev"
    target = tmp_path / "baseline.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--write", str(target)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["obligation_catalog_fingerprint"]
    assert json.loads(target.read_text(encoding="utf-8")) == payload
