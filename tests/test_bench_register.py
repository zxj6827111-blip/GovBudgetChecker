"""bench_register 单元测试（WP4-I S1 工具 1）。

覆盖：DOC-ID 递增、manifest 行字段、SHA 绑定、**重复 sha256 拒绝登记**
（幂等纪律）、非法参数拒绝。文本层探测委托 replay_golden_corpus.load_page_texts
（其 PDF 解析已有上游覆盖），这里 monkeypatch 成固定文本以免测试依赖真 PDF。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import bench_register


@pytest.fixture()
def bench_env(tmp_path, monkeypatch):
    corpus_dir = tmp_path / "corpus"
    manifest_path = corpus_dir / "manifest.csv"
    monkeypatch.setattr(
        bench_register, "load_page_texts", lambda _pdf: ["文本层内容 " * 30, ""]
    )
    return corpus_dir, manifest_path


def _make_pdf(tmp_path: Path, content: bytes, name: str) -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(content)
    return pdf


def test_register_creates_dir_manifest_and_copy(bench_env, tmp_path):
    corpus_dir, manifest_path = bench_env
    pdf = _make_pdf(tmp_path, b"%PDF-1.4 fake-bytes-A", "a.pdf")

    row = bench_register.register_pdf(
        pdf,
        report_kind_true="final",
        subset="final-main",
        source_url="https://example.gov/a.pdf",
        corpus_dir=corpus_dir,
        manifest_path=manifest_path,
    )

    assert row["doc_id"] == "DOC-B1-001"
    doc_dir = corpus_dir / "DOC-B1-001"
    assert (doc_dir / "sample.pdf").read_bytes() == b"%PDF-1.4 fake-bytes-A"
    assert row["sha256"] == bench_register.sha256_file(doc_dir / "sample.pdf")
    assert row["report_kind_true"] == "final"
    assert row["subset"] == "final-main"
    assert row["pages"] == 2  # monkeypatch 的文本页数
    assert row["text_chars"] > 0

    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["doc_id"] for r in rows] == ["DOC-B1-001"]
    assert rows[0]["sha256"] == row["sha256"]
    assert rows[0]["source_url"] == "https://example.gov/a.pdf"


def test_register_rejects_duplicate_sha256(bench_env, tmp_path):
    corpus_dir, manifest_path = bench_env
    pdf_a = _make_pdf(tmp_path, b"%PDF-1.4 same-content", "a.pdf")
    pdf_b = _make_pdf(tmp_path, b"%PDF-1.4 same-content", "b.pdf")

    bench_register.register_pdf(
        pdf_a,
        report_kind_true="final",
        subset="final-main",
        corpus_dir=corpus_dir,
        manifest_path=manifest_path,
    )
    with pytest.raises(SystemExit, match="重复登记拒绝"):
        bench_register.register_pdf(
            pdf_b,
            report_kind_true="final",
            subset="final-main",
            corpus_dir=corpus_dir,
            manifest_path=manifest_path,
        )


def test_next_doc_id_increments_from_manifest_and_dirs(bench_env, tmp_path):
    corpus_dir, manifest_path = bench_env
    pdf = _make_pdf(tmp_path, b"%PDF-1.4 x", "x.pdf")
    bench_register.register_pdf(
        pdf,
        report_kind_true="budget",
        subset="budget-main",
        corpus_dir=corpus_dir,
        manifest_path=manifest_path,
    )
    assert bench_register.next_doc_id(manifest_path) == "DOC-B1-002"
    # 目录存在但 manifest 缺行（异常态）时编号也不回退
    (corpus_dir / "DOC-B1-007").mkdir()
    assert bench_register.next_doc_id(manifest_path) == "DOC-B1-008"


def test_register_rejects_invalid_params(bench_env, tmp_path):
    corpus_dir, manifest_path = bench_env
    pdf = _make_pdf(tmp_path, b"%PDF-1.4 y", "y.pdf")
    with pytest.raises(SystemExit, match="report-kind-true"):
        bench_register.register_pdf(
            pdf,
            report_kind_true="决算",  # 非法值
            subset="final-main",
            corpus_dir=corpus_dir,
            manifest_path=manifest_path,
        )
    with pytest.raises(SystemExit, match="subset"):
        bench_register.register_pdf(
            pdf,
            report_kind_true="final",
            subset="随便写的",
            corpus_dir=corpus_dir,
            manifest_path=manifest_path,
        )
    with pytest.raises(SystemExit, match="不存在或不是 PDF"):
        bench_register.register_pdf(
            tmp_path / "missing.pdf",
            report_kind_true="final",
            subset="final-main",
            corpus_dir=corpus_dir,
            manifest_path=manifest_path,
        )
    # 两个非法路径都不应产生任何登记痕迹
    assert not manifest_path.exists()
    assert list(corpus_dir.glob("DOC-B1-*")) == []


def test_manifest_load_roundtrip(bench_env, tmp_path):
    corpus_dir, manifest_path = bench_env
    pdf = _make_pdf(tmp_path, b"%PDF-1.4 z", "z.pdf")
    bench_register.register_pdf(
        pdf,
        report_kind_true="final",
        subset="clean-contrast",
        region="probe",
        depth="L2",
        notes="探针材料",
        corpus_dir=corpus_dir,
        manifest_path=manifest_path,
    )
    rows = bench_register.load_manifest(manifest_path)
    assert rows[0]["region"] == "probe"
    assert rows[0]["depth"] == "L2"
    assert json.dumps(rows[0], ensure_ascii=False)  # 行内容 JSON 可序列化


def test_engine_fingerprint_is_stable_and_rule_sensitive():
    """引擎指纹（§6.7）：同规则集稳定，规则集一变必须变。

    这是「换了 checker 要能归因」的最小保证——若规则改了而指纹不变，报告
    就无法说明「这次指标变化是不是换了引擎」。
    """
    first = bench_register.engine_fingerprint()
    second = bench_register.engine_fingerprint()
    assert first["rules_sha256"] == second["rules_sha256"]
    assert first["rules_count"] > 0
    assert set(first) == {
        "rules_sha256", "rule_inventory_sha256", "rules_count", "git_head", "git_dirty",
    }
    assert len(first["rules_sha256"]) == 64 and len(first["rule_inventory_sha256"]) == 64
    # 两个哈希分工不同：源码哈希抓**行为**变化，清单哈希抓**规则码/严重度**漂移，
    # 二者不应偶然相等（相等说明其中一个退化成了另一个）
    assert first["rules_sha256"] != first["rule_inventory_sha256"]

    # 变异：把某条规则的严重度改掉，**清单指纹**必须随之改变
    from src.engine import rules_v33

    class _Mutated:
        def __init__(self, code, severity):
            self.code = code
            self.severity = severity

    original = list(rules_v33.ALL_RULES)
    assert original, "规则注册表不应为空"
    head = original[0]
    mutated = [_Mutated(getattr(head, "code", ""), "篡改严重度")] + original[1:]
    try:
        rules_v33.ALL_RULES = mutated
        after = bench_register.engine_fingerprint()
    finally:
        rules_v33.ALL_RULES = original
    assert after["rule_inventory_sha256"] != first["rule_inventory_sha256"]
    assert bench_register.engine_fingerprint()["rule_inventory_sha256"] == first["rule_inventory_sha256"]
