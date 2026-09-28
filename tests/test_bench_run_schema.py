"""run_benchmark / eval_benchmark 集成测试（WP4-I S1 工具 2+3）。

覆盖方案 §5 的两条硬要求：
- 产物 schema 与现有 evaluator 兼容（doc_id/sha256/findings 顶层键 +
  legacy 子结构，可直接被 evaluate() 消费）；
- 工具 3 对无 golden 的材料自动跳过、对有 golden 的材料产出聚合指标。

用干净 checkout 回退路径（无 PDF + golden 同源 fixture）驱动，全程离线。
"""

from __future__ import annotations

import csv
import pytest
import json
from pathlib import Path

from scripts import run_benchmark
from scripts.eval_benchmark import aggregate, build_doc_entries
from scripts.replay_golden_corpus import sha256_file  # noqa: F401  (绑定口径的引用)

#: golden 样张（生态环境局 31 页决算）的源 PDF SHA——fixture 回退的同源证明
GOLDEN_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"
DOC_ID = "DOC-20260905-001"


def _make_corpus(tmp_path: Path) -> tuple:
    corpus = tmp_path / "corpus"
    doc_dir = corpus / DOC_ID
    doc_dir.mkdir(parents=True)
    # 评测侧 SHA 同源绑定：golden.json 声明源 PDF SHA（fixture 回退据此校验）
    (doc_dir / "golden.json").write_text(
        json.dumps({"doc_id": DOC_ID, "sha256": GOLDEN_SHA, "labels": []}),
        encoding="utf-8",
    )
    with (corpus / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(bench_register_fields())
        writer.writerow(
            [DOC_ID, GOLDEN_SHA, "final", "final-main", "anchor", "", "", "31", "9999",
             "2026-09-28 00:00:00", ""]
        )
    return corpus


def bench_register_fields():
    from scripts.bench_register import MANIFEST_FIELDS

    return MANIFEST_FIELDS


def test_run_benchmark_schema_and_eval_consumability(tmp_path):
    corpus = _make_corpus(tmp_path)
    out_root = tmp_path / "benchmark"

    rc = run_benchmark.main(
        ["--corpus", str(corpus), "--out-root", str(out_root), "--doc", DOC_ID]
    )
    assert rc == 0

    run_dirs = [p for p in out_root.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    replay_path = run_dirs[0] / f"{DOC_ID}.json"
    payload = json.loads(replay_path.read_text(encoding="utf-8"))

    # 方案 §5 工具 2 的顶层兼容键
    assert payload["doc_id"] == DOC_ID
    assert payload["sha256"] == GOLDEN_SHA
    assert isinstance(payload["findings"], list) and payload["findings"]
    # evaluator 兼容子结构（evaluate() 消费 replay["legacy"]）
    legacy = payload["legacy"]
    assert legacy["finding_total"] == len(payload["findings"])
    assert legacy["rule_counts"]
    assert set(legacy["findings"][0].keys()) >= {
        "rule", "severity", "message", "page", "evidence_text", "section_id",
    }
    # 每条 finding 的 rule_execution_summary 六态齐全
    summary = payload["rule_execution_summary"]
    for key in ("pass", "fail", "not_applicable", "insufficient_data",
                "parse_error", "execution_error"):
        assert key in summary
    # 文种解析：golden 样张是决算 → final；义务账本可用
    assert payload["report_kind_resolved"] == "final"
    ledger = payload["obligation_ledger"]
    assert ledger["applicable_total"] > 0
    assert ledger["by_group"]

    # run manifest 记录失败面（本例无失败）
    run_manifest = json.loads((run_dirs[0] / "_run_manifest.json").read_text(encoding="utf-8"))
    assert run_manifest["failures"] == []
    assert run_manifest["docs"][0]["doc_id"] == DOC_ID

    # —— 工具 3 消费：有 golden → 评测报告；指标结构完整 ——
    entries = build_doc_entries(run_dirs[0], corpus, "auto")
    assert len(entries) == 1
    assert entries[0]["eval_report"] is not None
    eval_report = entries[0]["eval_report"]
    assert eval_report["doc_id"] == DOC_ID
    # 空 golden（labels=[]）下所有 finding 都是 FP，但命中判定链路完整
    assert eval_report["fp"] == len(payload["findings"])
    assert eval_report["precision"] is not None or eval_report["tp"] == 0

    report = aggregate(entries, {"final": ["V33-101"], "budget": [], "common": []})
    assert report["docs_evaluated"] == 1
    assert report["totals"]["fp"] == len(payload["findings"])


def test_run_benchmark_missing_doc_fails_closed(tmp_path):
    corpus = _make_corpus(tmp_path)
    out_root = tmp_path / "benchmark2"
    # 未登记的 DOC-ID 必须明确失败（SystemExit→CLI 非零码），不允许空跑
    with pytest.raises(SystemExit) as excinfo:
        run_benchmark.main(
            ["--corpus", str(corpus), "--out-root", str(out_root), "--doc", "DOC-B1-999"]
        )
    assert "未匹配到任何登记行" in str(excinfo.value)
