"""replay_golden_corpus 输入加载与缓存校验测试（review 🟡1 + P1-6 补完）。

- fixture 回退 fail-closed：无 golden.json / doc_id 不符 / SHA 不一致
  都必须明确拒绝，不允许静默拿样张数据评测别的 doc；
- 历史回放缓存恢复：job 目录 PDF 被替换后旧缓存条目必须失效重跑。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from scripts.replay_golden_corpus import (
    _cached_result_valid,
    _load_corpus_inputs,
    _write_checkpoint_atomic,
    _write_json_exclusive,
)


# ---------------------------------------------------------------------------
# _load_corpus_inputs：fixture 回退 fail-closed（review 🟡1）
# ---------------------------------------------------------------------------


def _write_golden(doc_dir: Path, sha: str = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"):
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "golden.json").write_text(
        json.dumps({"doc_id": doc_dir.name, "sha256": sha}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_fixture_fallback_requires_golden_json(tmp_path):
    """无 PDF 且无 golden.json → 拒绝回退（此前静默拿样张数据）。"""
    doc_dir = tmp_path / "DOC-NO-GOLDEN"
    doc_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="golden.json"):
        _load_corpus_inputs(doc_dir)


def test_fixture_fallback_rejects_other_doc_id(tmp_path):
    """fixture 内嵌 doc_id 与 doc_dir 不符 → 拒绝（不能评测别的 doc）。"""
    doc_dir = tmp_path / "DOC-OTHER-999"
    _write_golden(doc_dir)
    with pytest.raises(RuntimeError, match="doc_id"):
        _load_corpus_inputs(doc_dir)


def test_fixture_fallback_rejects_sha_mismatch(tmp_path):
    """golden 声明 SHA 与 fixture 不一致 → 拒绝。"""
    doc_dir = tmp_path / "DOC-20260905-001"
    _write_golden(doc_dir, sha="0" * 64)
    with pytest.raises(RuntimeError, match="SHA"):
        _load_corpus_inputs(doc_dir)


def test_fixture_fallback_happy_path(tmp_path):
    """同源证明齐备（golden SHA 与 fixture 一致、doc_id 相符）→ 回退成功。"""
    doc_dir = tmp_path / "DOC-20260905-001"
    _write_golden(doc_dir)
    pdf_name, page_texts, page_tables = _load_corpus_inputs(doc_dir)
    assert pdf_name == "(fixture)"
    assert len(page_texts) == len(page_tables) == 31


# ---------------------------------------------------------------------------
# _cached_result_valid：恢复时 PDF SHA 校验（R6 P1-6 补完）
# ---------------------------------------------------------------------------


def test_cached_result_valid_accepts_unchanged_pdf(tmp_path, monkeypatch):
    """PDF 未变：缓存条目有效，可续跑。"""
    import scripts.replay_golden_corpus as mod

    job_dir = tmp_path / "job-a"
    job_dir.mkdir()
    pdf = job_dir / "doc.pdf"
    pdf.write_bytes(b"same-bytes-001")
    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)
    result = {
        "job_id": "job-a",
        "pdf": "doc.pdf",
        "sha256": mod.sha256_file(pdf),
    }
    assert _cached_result_valid(result) is True


def test_cached_result_valid_rejects_replaced_pdf(tmp_path, monkeypatch):
    """同一 job_id 的 PDF 被替换 → 旧缓存失效，必须重跑。"""
    import scripts.replay_golden_corpus as mod

    job_dir = tmp_path / "job-a"
    job_dir.mkdir()
    pdf = job_dir / "doc.pdf"
    pdf.write_bytes(b"same-bytes-001")
    old_sha = mod.sha256_file(pdf)
    # 模拟后续上传替换了同一 job 的 PDF
    pdf.write_bytes(b"replaced-bytes-002")
    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)
    result = {"job_id": "job-a", "pdf": "doc.pdf", "sha256": old_sha}
    assert _cached_result_valid(result) is False


def test_cached_result_valid_rejects_skipped_entries(tmp_path, monkeypatch):
    """skipped/failed 条目不带 sha256 → 不视为可续缓存。"""
    import scripts.replay_golden_corpus as mod

    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)
    assert _cached_result_valid({"job_id": "job-b", "pdf": "x.pdf", "skipped": "no_pdf"}) is False


def test_replay_artifact_writer_never_overwrites_existing_path(tmp_path):
    """同名回放产物已存在时必须改用新文件，不能覆盖旧证据。"""
    target = tmp_path / "DOC-legacy-20260909.json"
    target.write_text("old", encoding="utf-8")

    created = _write_json_exclusive(target, {"version": "new"})

    assert created != target
    assert target.read_text(encoding="utf-8") == "old"
    assert created.exists()
    assert '"version": "new"' in created.read_text(encoding="utf-8")


def test_checkpoint_writer_leaves_complete_json(tmp_path):
    """checkpoint 先完整写入再替换，读取时不能看到半个 JSON。"""
    target = tmp_path / "historical-partial-legacy.json"
    _write_checkpoint_atomic(target, {"results": [{"job_id": "job-a"}]})

    assert json.loads(target.read_text(encoding="utf-8"))["results"][0]["job_id"] == "job-a"
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# R7 P1-4：structured 历史对比限定在 STRUCTURED_MIGRATED_RULES
# ---------------------------------------------------------------------------


def test_structured_delta_restricted_to_migrated_rules():
    """structured 差异对比只认九条迁移集，未迁移规则进 coverage_gap。

    旧行为用「旧全量规则集合 ∪ 新九条规则」计算差异：V33-235/CMM-004
    等未迁移规则的旧计数全部算成 removed（历史实测 removed=131 假象）。
    """
    from scripts.replay_golden_corpus import (
        _migration_scope,
        _restricted_rule_delta,
    )

    old_counts = {
        "V33-235": 21,
        "CMM-004": 10,
        "V33-115": 3,
        "V33-120": 1,
        "V33-220": 2,
    }
    new_counts = {"V33-115": 3, "V33-120": 0, "V33-220": 2}
    scope = _migration_scope("structured")
    assert scope and "V33-115" in scope, "迁移集域应含 V33-115"
    assert "V33-235" not in scope and "CMM-004" not in scope

    delta, gap, drift = _restricted_rule_delta(old_counts, new_counts, scope)
    assert delta == {"V33-120": {"old": 1, "new": 0}}, (
        "未迁移规则不得进入 delta"
    )
    assert gap == {"V33-235": 21, "CMM-004": 10}, (
        "未迁移规则旧计数单独进 coverage_gap"
    )
    assert drift == [], "无域外新规则时漂移留痕为空"
    changed = sum(abs(v["new"] - v["old"]) for v in delta.values())
    assert changed == 1, "removed 口径只统计迁移集内变化"


def test_structured_parsing_consumers_stay_inside_the_migrated_set():
    """structured 解析覆盖率的分子必须是「runner 真执行 ∩ 真消费 parsed_tables」。

    replay 直接把 STRUCTURED_PARSING_CONSUMERS 算进 structured_coverage；
    登记一条 runner 并不执行的规则会把分子抬高，还会把
    「登记在适配器但输入仍为 legacy」的条数少报 1（WP4-A R2 评审指出的失真）。
    WP4-A 的 V33-CROSS-SAN-GONG-ECON 在生产 legacy 主路径中内部自建
    parsed_tables，WP5 迁移进 runner 之前不得计入 structured coverage。
    """
    from src.engine.structured_rules import (
        STRUCTURED_MIGRATED_RULES,
        STRUCTURED_PARSING_CONSUMERS,
    )

    assert set(STRUCTURED_PARSING_CONSUMERS) <= set(STRUCTURED_MIGRATED_RULES), (
        "解析消费集合不能超出 runner 实际执行集，否则 coverage 虚高"
    )
    assert "V33-CROSS-SAN-GONG-ECON" not in STRUCTURED_PARSING_CONSUMERS, (
        "WP4-A 规则未接入 structured runner，WP5 前不得计入覆盖率分子"
    )


def test_structured_delta_surfaces_out_of_scope_new_rules():
    """适配器漂移（新规则真实执行但未登记迁移集）不得静默丢弃。

    R7 /review：域外新计数若被 scope 过滤静默丢弃，历史对比会漏掉
    已执行规则的变更。漂移规则必须显式进入 delta 并留痕 out_of_scope_new。
    """
    from scripts.replay_golden_corpus import (
        _migration_scope,
        _restricted_rule_delta,
    )

    old_counts = {"V33-115": 3}
    new_counts = {"V33-115": 3, "V33-999": 5}
    scope = _migration_scope("structured")
    delta, gap, drift = _restricted_rule_delta(old_counts, new_counts, scope)
    assert delta == {"V33-999": {"old": 0, "new": 5}}, (
        "漂移规则必须显式进入 delta"
    )
    assert gap == {}
    assert drift == ["V33-999"], "漂移规则必须留痕"


def test_legacy_delta_keeps_full_rule_scope():
    """legacy 模式不限定规则域（历史回归对比需要全量规则集）。"""
    from scripts.replay_golden_corpus import (
        _migration_scope,
        _restricted_rule_delta,
    )

    old_counts = {"V33-235": 21, "V33-115": 3}
    new_counts = {"V33-115": 0}
    assert _migration_scope("legacy") is None
    delta, gap, drift = _restricted_rule_delta(old_counts, new_counts, None)
    assert set(delta) == {"V33-235", "V33-115"}
    assert gap == {}
    assert drift == []


# ---------------------------------------------------------------------------
# R8 P1：replay_historical 全链路聚合（漂移规则不崩溃）
# ---------------------------------------------------------------------------


def test_historical_aggregation_survives_drift_rule(tmp_path, monkeypatch):
    """replay_historical 全链路：漂移规则进聚合不崩溃（R8 P1 复现防护）。

    此前只测 _restricted_rule_delta；聚合层 scope 限定后未同步漂移域，
    per_rule_delta 含 V33-999 而 agg 无该键 → docs_changed 累加 KeyError
    （独立反例稳定复现）。修复后：漂移规则显式进入 rule_aggregate，
    并留痕 adapter_scope_drift。
    """
    import scripts.replay_golden_corpus as mod

    job_dir = tmp_path / "job-drift"
    job_dir.mkdir()
    (job_dir / "doc.pdf").write_bytes(b"pdf-bytes")
    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)

    def fake_replay(job_path, parse_mode="legacy"):
        assert job_path.name == "job-drift"
        assert parse_mode == "structured"
        return {
            "job_id": "job-drift",
            "pdf": "doc.pdf",
            "report_kind_old": "final",
            "report_kind_new": "final",
            "has_baseline": True,
            "old_total": 3,
            "new_total": 8,
            "old_counts": {"V33-115": 3},
            "new_counts": {"V33-115": 3, "V33-999": 5},
            "per_rule_delta": {"V33-999": {"old": 0, "new": 5}},
            "coverage_gap": {},
            "out_of_scope_new": ["V33-999"],
            "changed_total": 5,
        }

    monkeypatch.setattr(mod, "replay_historical_doc", fake_replay)
    report = mod.replay_historical(
        parse_mode="structured", limit=None, resume=False, workers=1
    )
    assert report["adapter_scope_drift"] == {"V33-999": 1}
    entry = report["rule_aggregate"]["V33-999"]
    assert entry["docs_changed"] == 1 and entry["new"] == 5, (
        "漂移规则必须进入聚合（此前 KeyError 崩溃）"
    )


def test_historical_drift_counted_for_no_baseline_and_gap_excludes_executed(
    tmp_path, monkeypatch
):
    """R9 P1 反例：无基线任务的漂移必须留痕、已执行域外规则不进 coverage_gap。

    构造两份 structured 结果：job-a 有基线（V33-999 old=2 → 旧行为会
    同时进 delta 与 coverage_gap 的冲突形态）、job-b 无基线（真实执行
    V33-999 但不进 processed）——此前漂移汇总只遍历 processed（job-b
    的漂移被漏报）、coverage_gap 含已执行规则（job-a 冲突双计）。
    """
    import scripts.replay_golden_corpus as mod

    for name in ("job-a-r9", "job-b-r9"):
        job_dir = tmp_path / name
        job_dir.mkdir()
        (job_dir / "doc.pdf").write_bytes(b"pdf-bytes")
    monkeypatch.setattr(mod, "UPLOADS_DIR", tmp_path)

    def fake_replay(job_path, parse_mode="legacy"):
        if job_path.name == "job-a-r9":
            return {
                "job_id": "job-a-r9",
                "pdf": "doc.pdf",
                "report_kind_old": "final",
                "report_kind_new": "final",
                "has_baseline": True,
                "old_total": 5,
                "new_total": 8,
                "old_counts": {"V33-999": 2},
                "new_counts": {"V33-999": 5},
                "per_rule_delta": {"V33-999": {"old": 2, "new": 5}},
                # 已执行的域外规则不再出现在 coverage_gap
                "coverage_gap": {},
                "out_of_scope_new": ["V33-999"],
                "changed_total": 3,
            }
        return {
            "job_id": "job-b-r9",
            "pdf": "doc.pdf",
            "report_kind_old": "final",
            "report_kind_new": "final",
            "has_baseline": False,  # 无历史基线：真实执行但不进 processed
            "old_total": 0,
            "new_total": 3,
            "old_counts": {},
            "new_counts": {"V33-999": 3},
            "per_rule_delta": {},
            "coverage_gap": {},
            "out_of_scope_new": ["V33-999"],
            "changed_total": 0,
        }

    monkeypatch.setattr(mod, "replay_historical_doc", fake_replay)
    report = mod.replay_historical(
        parse_mode="structured", limit=None, resume=False, workers=1
    )
    # 无基线任务的漂移必须计入
    assert report["adapter_scope_drift"] == {"V33-999": 2}, (
        f"漂移汇总必须含无基线任务: {report['adapter_scope_drift']}"
    )
    # 已执行域外规则的旧计数不得进入 coverage_gap
    assert report["coverage_gap_rule_aggregate"] == {}, (
        f"已执行域外规则不得进 coverage_gap: {report['coverage_gap_rule_aggregate']}"
    )
