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

    delta, gap = _restricted_rule_delta(old_counts, new_counts, scope)
    assert delta == {"V33-120": {"old": 1, "new": 0}}, (
        "未迁移规则不得进入 delta"
    )
    assert gap == {"V33-235": 21, "CMM-004": 10}, (
        "未迁移规则旧计数单独进 coverage_gap"
    )
    changed = sum(abs(v["new"] - v["old"]) for v in delta.values())
    assert changed == 1, "removed 口径只统计迁移集内变化"


def test_legacy_delta_keeps_full_rule_scope():
    """legacy 模式不限定规则域（历史回归对比需要全量规则集）。"""
    from scripts.replay_golden_corpus import (
        _migration_scope,
        _restricted_rule_delta,
    )

    old_counts = {"V33-235": 21, "V33-115": 3}
    new_counts = {"V33-115": 0}
    assert _migration_scope("legacy") is None
    delta, gap = _restricted_rule_delta(old_counts, new_counts, None)
    assert set(delta) == {"V33-235", "V33-115"}
    assert gap == {}
