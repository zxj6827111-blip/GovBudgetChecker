"""benchmark 确定性守护（WP4-I §6.7：纯规则模式 + 固定代码版本 ⇒ 同输入同输出）。

为什么需要这条守护：`docs/baselines/bench1_metrics_*.json` 的**跨节点对照**与后续
回归比较都建立在「同输入同输出」之上。若某天有人引入依赖字典/集合迭代顺序（进程级
随机哈希种子会让顺序在进程间变化）或依赖当前时间的逻辑，指标会在两次运行之间漂移，
而所有既有对照都会变成噪声——这类缺陷不会有别的测试抓到。

**必须在独立进程里比**：同进程内两次运行的集合顺序天然一致，测不出哈希种子依赖。
本测试用两个 `PYTHONHASHSEED`（0 与 1）各起一个子进程重放同一份冻结夹具，逐字节比较
finding 序列——这正是「跨进程可复现」的判据。

实测（2026-09-28）：两次独立进程运行完整 benchmark，7 份材料的 findings（含顺序）、
六态摘要、义务账本、文种、sha256、引擎指纹逐项一致；两次运行唯一不同的是时间戳目录名
与 `generated_at`（设计如此）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_page_data.json"

#: 子进程脚本：重放冻结夹具并把 finding 序列规范化后打到 stdout
_CHILD = """
import json, sys
sys.path.insert(0, {root!r})
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes

fixture = json.loads(open({fixture!r}, encoding="utf-8").read())
doc = build_document(
    path="DOC-20260905-001.pdf",
    page_texts=fixture["page_texts"],
    page_tables=json.loads(json.dumps(fixture["page_tables"])),
    filesize=0,
)
issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False)
payload = {{
    "findings": [
        [str(getattr(i, "rule", "")), str(getattr(i, "severity", "")),
         str(getattr(i, "message", "")),
         (getattr(i, "location", {{}}) or {{}}).get("page"),
         str(getattr(i, "evidence_text", "") or "")]
        for i in issues
    ],
    "summary": summarize_rule_outcomes(outcomes),
    "kind": str(getattr(doc, "report_kind", "")),
}}
sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""


def _replay_with_hash_seed(seed: str) -> dict:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    result = subprocess.run(
        [sys.executable, "-c", _CHILD.format(root=str(_REPO_ROOT), fixture=str(_FIXTURE))],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        cwd=str(_REPO_ROOT),
    )
    return json.loads(result.stdout)


def test_rules_are_deterministic_across_processes():
    """两个不同 PYTHONHASHSEED 的独立进程必须产出逐项一致的 finding 序列。"""
    seed0 = _replay_with_hash_seed("0")
    seed1 = _replay_with_hash_seed("1")

    assert seed0["findings"], "夹具产出为空——这条守护会变成空转"
    assert seed0["findings"] == seed1["findings"], (
        "finding 集合或顺序随 PYTHONHASHSEED 变化——说明引擎依赖了集合/字典迭代顺序，"
        "跨节点对照与回归比较会失真"
    )
    assert seed0["summary"] == seed1["summary"], "六态摘要随哈希种子变化"
    assert seed0["kind"] == seed1["kind"]
    assert seed0["summary"]["total_rules"] > 0
