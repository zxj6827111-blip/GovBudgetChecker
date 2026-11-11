"""R3-02：导出逐成员、逐侧证据、逐成员版本验收。

Codex 第三轮反例：
- 同一 rule_id 的两个独立关系只导出第一条时，旧验收器按 rule_id 子串
  判定成员存在，因此漏掉第二条；
- 从真实 CSV 删除一条 issue 行（14→13）仍 ok=true；
- 短金额引文（13.24/81.11/19.56）被 8 字符门槛跳过。

这里用真实导出链路生成 CSV/DOCX/PDF，再验证：
1. 正常导出按成员身份逐项通过（同 rule_id 同页也能区分）；
2. 删除 CSV 的一条问题行必须被拒绝；
3. DOCX 中删掉一侧金额证据、改错页码/版本必须被拒绝；
4. 提示（tip）不计入正式问题成员，也不能补偿缺失问题。
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import runtime
from api.main import app
from scripts.d4_real_link_acceptance import verify_export_formats


def _create_pdf(path: Path, lines: list[str]) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    try:
        page = document.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 18
        document.save(path)
    finally:
        document.close()


def _issue(issue_id: str, page: int, quotes: list[tuple[int, str]]) -> dict:
    return {
        "id": issue_id,
        "source": "rule",
        "rule_id": "CROSS-TEST",
        "severity": "error",
        "title": "跨表关系金额不一致",
        "message": f"{issue_id} 跨表关系金额不一致，需核对两侧原格。",
        "location": {"page": page, "table": "FIN_07_three_public"},
        "evidence_status": "complete",
        "evidence_verification": {
            "status": "verified", "source_identity_complete": True,
        },
        "evidence": [
            {"page": quote_page, "quote": quote, "evidence_id": f"{issue_id}-{index}"}
            for index, (quote_page, quote) in enumerate(quotes, start=1)
        ],
    }


ISSUES = [
    _issue("relation-A", 22, [(22, "13.24"), (24, "0.00")]),
    _issue("relation-B", 22, [(22, "81.11"), (24, "19.56")]),
]
REVISION = "rev-r3-export-control"


@pytest.fixture()
def exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_id = "job-r3-export"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _create_pdf(job_dir / "source.pdf", ["CROSS TEST SOURCE"])
    tip = {
        "id": "TIP-1", "source": "rule", "rule_id": "TIP-RULE",
        "severity": "info", "title": "提示项", "message": "提示项",
        "location": {"page": 1},
        "evidence": [{"page": 1, "quote": "99.99"}],
    }
    payload = {
        "job_id": job_id,
        "status": "done",
        "saved_path": f"{job_id}/source.pdf",
        "analysis_revision": REVISION,
        "review_report": {
            "issues": ISSUES,
            "tips": [tip],
            "manual_review": [],
            "human_decisions": [],
        },
        "result": {"rule_findings": list(ISSUES) + [tip],
                   "ai_findings": [], "merged": {}},
    }
    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    client = TestClient(app)
    contents = {}
    for suffix in ("csv", "docx", "pdf"):
        response = client.get(f"/api/reports/download?job_id={job_id}&format={suffix}")
        assert response.status_code == 200, response.text
        contents[suffix] = response.content
    return {"contents": contents, "job_id": job_id, "payload": payload}


def test_normal_export_passes_per_member_and_per_side(exported) -> None:
    result = verify_export_formats(exported["contents"], ISSUES, REVISION)
    assert result["ok"] is True, json.dumps(result, ensure_ascii=False)
    for suffix in ("csv", "docx", "pdf"):
        assert result["formats"][suffix]["member_count"] == 2


def test_tip_is_not_counted_as_formal_issue(exported) -> None:
    result = verify_export_formats(exported["contents"], ISSUES, REVISION)
    assert result["formats"]["docx"]["extra_members"] == []
    text = exported["contents"]["docx"]
    with zipfile.ZipFile(io.BytesIO(text)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "TIP_ID|TIP-1|" in document
    assert "ISSUE_ID|TIP-1|" not in document


def test_csv_missing_issue_member_is_rejected(exported) -> None:
    content = exported["contents"]["csv"].decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    fields = reader.fieldnames
    rows = list(reader)
    kept = [row for row in rows
            if not (row["record_type"] == "issue" and row["issue_id"] == "relation-B")]
    assert len(kept) == len(rows) - 1
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(kept)
    contents = dict(exported["contents"])
    contents["csv"] = output.getvalue().encode("utf-8-sig")
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    assert "relation-B" in result["formats"]["csv"]["missing_members"]


def test_csv_evidence_side_removed_from_member_is_rejected(exported) -> None:
    content = exported["contents"]["csv"].decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    fields = reader.fieldnames
    rows = list(reader)
    for row in rows:
        if row["record_type"] == "issue" and row["issue_id"] == "relation-B":
            quotes = json.loads(row["evidence_quotes"])
            row["evidence_quotes"] = json.dumps(
                [item for item in quotes if item["quote"] != "19.56"],
                ensure_ascii=False)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    contents = dict(exported["contents"])
    contents["csv"] = output.getvalue().encode("utf-8-sig")
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    failed = result["formats"]["csv"]["failed_members"]
    assert any(item["issue_id"] == "relation-B"
               and "evidence_not_bound" in item["failures"] for item in failed)


def _rewrite_docx(content: bytes, transform) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        other = {name: archive.read(name) for name in archive.namelist()
                 if name != "word/document.xml"}
    mutated = transform(xml)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", mutated)
        for name, data in other.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_docx_one_side_evidence_removed_is_rejected(exported) -> None:
    mutated = _rewrite_docx(
        exported["contents"]["docx"],
        lambda xml: xml.replace(
            '<w:t xml:space="preserve">19.56</w:t>',
            '<w:t xml:space="preserve">19.99</w:t>'),
    )
    contents = dict(exported["contents"])
    contents["docx"] = mutated
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    assert any("evidence_not_bound" in item["failures"]
               for item in result["formats"]["docx"]["failed_members"])


def test_docx_wrong_page_binding_is_rejected(exported) -> None:
    mutated = _rewrite_docx(
        exported["contents"]["docx"],
        lambda xml: re.sub(
            r'(<w:t xml:space="preserve">证据页码</w:t></w:r></w:p></w:tc>'
            r'<w:tc><w:p><w:r><w:t xml:space="preserve">)22(</w:t>)',
            r'\g<1>23\g<2>', xml, count=1),
    )
    contents = dict(exported["contents"])
    contents["docx"] = mutated
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    assert any("page_not_bound" in item["failures"]
               for item in result["formats"]["docx"]["failed_members"])


def test_docx_wrong_revision_binding_is_rejected(exported) -> None:
    mutated = _rewrite_docx(
        exported["contents"]["docx"],
        lambda xml: xml.replace(REVISION, "rev-other"),
    )
    contents = dict(exported["contents"])
    contents["docx"] = mutated
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    assert any("revision_not_bound" in item["failures"]
               for item in result["formats"]["docx"]["failed_members"])


def test_docx_missing_issue_block_is_rejected(exported) -> None:
    def remove_relation_b(xml: str) -> str:
        return re.sub(
            r"ISSUE_ID\|relation-B\|.*?(?=ISSUE_ID\||TIP_ID\||DECISION_ID\||MANUAL_ID\||CHECK_ID\|)",
            "", xml, flags=re.S)

    mutated = _rewrite_docx(exported["contents"]["docx"], remove_relation_b)
    contents = dict(exported["contents"])
    contents["docx"] = mutated
    result = verify_export_formats(contents, ISSUES, REVISION)
    assert result["ok"] is False
    assert "relation-B" in result["formats"]["docx"]["missing_members"]


# ---------------------------------------------------------------------------
# R4-03：页码/分析版本按字段值等值比较，不是子串
# ---------------------------------------------------------------------------

REVISION_R4 = "rev-20261111T010000-12345678"
MEMBER_R4 = {"id": "A", "page": 2,
             "evidence": [{"page": 2, "quote": "13.24"}],
             "label": "原文摘录"}


def _r4_block(page_value: str, revision_value: str, evidence_page: int = 2) -> str:
    return (f"ISSUE_ID|A|\n页码：{page_value}\n分析版本：{revision_value}\n"
            f"原文摘录（第{evidence_page}页）：13.24")


@pytest.mark.parametrize("block", [
    _r4_block("2", REVISION_R4),
    # DOCX 风格（无冒号、字段相连）同样按值等值比较。
    "ISSUE_ID|A|证据页码2定位等级/坐标exact_cell分析版本"
    f"{REVISION_R4}建议处理方式请复核。原文摘录（第2页）13.24",
])
def test_field_exact_match_passes(block) -> None:
    from scripts.d4_real_link_acceptance import _verify_text_issue_block

    assert _verify_text_issue_block(block, MEMBER_R4, REVISION_R4)["ok"] is True


@pytest.mark.parametrize("block", [
    _r4_block("22", REVISION_R4),          # 页码 2→22
    _r4_block("222", REVISION_R4),         # 页码 22→222（真实导出控制）
    _r4_block("2", REVISION_R4 + "-old"),  # 版本追加 -old
    _r4_block("2", REVISION_R4[:-2]),      # 版本被截断
    # 预期值出现在其他字段/说明里不能补偿字段本身的错值。
    _r4_block("22", REVISION_R4).replace(
        "13.24", "页码2 " + REVISION_R4 + " 13.24"),
])
def test_field_substring_or_stray_reference_is_rejected(block) -> None:
    from scripts.d4_real_link_acceptance import _verify_text_issue_block

    check = _verify_text_issue_block(block, MEMBER_R4, REVISION_R4)
    assert check["ok"] is False
    assert {"page_not_bound", "revision_not_bound"} & set(check["failures"])
