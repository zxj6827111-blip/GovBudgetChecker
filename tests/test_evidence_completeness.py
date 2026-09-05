"""Task 8 / 缺口 P0-07：证据链完整性校验与缺证据降级。

断言意图：
1. 单条校验的正反例：有页码+原文片段算完整；只有标题没有任何原文算不完整；
   只有 bbox 没有文字也算可复核（能把人带回原文某处）。
2. AI 缺证据的问题被降级为 manual_review、打 evidence_status 标记，
   且**不计入正式问题数**；规则缺证据只记录告警、仍是正式问题。
3. `evidence_completeness` 的完整率、降级计数、明细数值正确。
4. 与 M1 质量门禁的联动：降级后 issue_total 用的是正式问题数，
   不会出现"问题全被降级却仍报 findings_detected"；存在降级项时必须转 review_required。
5. 向后兼容：历史 finding 没有 evidence_status 时按正式问题计数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from api.main import _count_result_findings, _evaluate_quality_gate
from src.services.evidence_guard import (
    EVIDENCE_DEGRADED_TAG,
    EVIDENCE_STATUS_COMPLETE,
    EVIDENCE_STATUS_DEGRADED,
    EVIDENCE_STATUS_RULE_WARNING,
    apply_evidence_completeness,
    evaluate_finding_evidence,
    is_document_level_finding,
    is_formal_finding,
)

GOOD_PAGES = {
    "page_count": 2,
    "text_page_count": 2,
    "low_text_pages": [],
    "low_text_page_count": 0,
    "scanned_pages": [],
    "scanned_page_count": 0,
    "page_coverage": 1.0,
}


def _reason_codes(gate: Dict[str, Any]) -> List[str]:
    return [reason["code"] for reason in gate["review_reasons"]]


# --------------------------------------------------------------------------
# 单条校验：正例与反例
# --------------------------------------------------------------------------
def test_finding_with_page_and_text_is_complete() -> None:
    complete, missing = evaluate_finding_evidence(
        {
            "page_number": 12,
            "evidence": [{"page": 12, "text": "合计 35.20，其中因公出国 10.00"}],
        }
    )
    assert complete is True
    # 没有坐标只是"无法框选"，不影响可复核性
    assert missing == ["missing_bbox"]


def test_finding_with_only_bbox_is_complete() -> None:
    complete, missing = evaluate_finding_evidence(
        {"page_number": 3, "bbox": [10.0, 20.0, 120.0, 40.0], "evidence": []}
    )
    assert complete is True
    assert missing == ["missing_evidence_text"]


def test_finding_without_any_evidence_is_incomplete() -> None:
    complete, missing = evaluate_finding_evidence(
        {"page_number": 5, "title": "疑似口径不一致", "evidence": []}
    )
    assert complete is False
    assert "missing_evidence_text" in missing
    assert "missing_bbox" in missing


def test_finding_without_page_is_incomplete() -> None:
    complete, missing = evaluate_finding_evidence(
        {"page_number": 0, "location": {}, "evidence": [{"text": "有原文但定位不到页"}]}
    )
    assert complete is False
    assert missing == ["missing_page", "missing_bbox"]


def test_blank_text_does_not_count_as_evidence() -> None:
    """反例：空白字符串不算证据，避免"填了字段就算有证据"。"""
    complete, _missing = evaluate_finding_evidence(
        {"page_number": 2, "text_snippet": "   ", "evidence": [{"text": ""}]}
    )
    assert complete is False


def test_invalid_bbox_does_not_count() -> None:
    complete, missing = evaluate_finding_evidence(
        {"page_number": 2, "bbox": [1, 2, 3], "evidence": []}
    )
    assert complete is False
    assert "missing_bbox" in missing


# --------------------------------------------------------------------------
# 按来源分别处理
# --------------------------------------------------------------------------
def test_ai_finding_without_evidence_is_degraded_and_not_formal() -> None:
    result: Dict[str, Any] = {
        "ai_findings": [
            {
                "id": "ai-no-evidence",
                "source": "ai",
                "severity": "high",
                "title": "疑似三公经费不一致",
                "page_number": 4,
                "evidence": [],
                "tags": ["AI检测"],
            },
            {
                "id": "ai-with-evidence",
                "source": "ai",
                "severity": "high",
                "page_number": 6,
                "evidence": [{"page": 6, "text": "合计 35.20"}],
                "tags": ["AI检测"],
            },
        ],
        "rule_findings": [],
    }

    completeness = apply_evidence_completeness(result)

    degraded, kept = result["ai_findings"]
    assert degraded["evidence_status"] == EVIDENCE_STATUS_DEGRADED
    assert degraded["severity"] == "manual_review"
    assert degraded["original_severity"] == "high"
    assert EVIDENCE_DEGRADED_TAG in degraded["tags"]
    assert "EVIDENCE_INCOMPLETE" in degraded["why_not"]
    assert is_formal_finding(degraded) is False

    # 对照：证据完整的那条保持原样
    assert kept["evidence_status"] == EVIDENCE_STATUS_COMPLETE
    assert kept["severity"] == "high"
    assert is_formal_finding(kept) is True

    assert completeness["total"] == 2
    assert completeness["complete"] == 1
    assert completeness["incomplete"] == 1
    assert completeness["completeness_rate"] == 0.5
    assert completeness["degraded_count"] == 1
    assert completeness["formal_issue_total"] == 1
    assert completeness["degraded"][0]["id"] == "ai-no-evidence"
    assert completeness["degraded"][0]["missing"] == ["missing_evidence_text"]


def test_rule_finding_without_evidence_is_warned_but_stays_formal() -> None:
    result: Dict[str, Any] = {
        "ai_findings": [],
        "rule_findings": [
            {
                "id": "rule-no-evidence",
                "source": "rule",
                "rule_id": "C-001",
                "severity": "high",
                "page_number": 0,
                "evidence": [],
            }
        ],
    }

    completeness = apply_evidence_completeness(result)
    finding = result["rule_findings"][0]

    assert finding["evidence_status"] == EVIDENCE_STATUS_RULE_WARNING
    # 规则是确定性判定，不降级严重程度
    assert finding["severity"] == "high"
    assert is_formal_finding(finding) is True
    assert completeness["rule_warning_count"] == 1
    assert completeness["degraded_count"] == 0
    assert completeness["formal_issue_total"] == 1


def test_legacy_bucket_structure_is_checked_without_double_counting() -> None:
    """legacy 分桶结构：all 与 error/warn/info 共享同一批对象，只能算一次。"""
    item = {
        "id": "C-001-1",
        "source": "rule",
        "severity": "error",
        "location": {"page": 12},
        "evidence": [{"page": 12, "text": "合计 35.20"}],
    }
    result: Dict[str, Any] = {"issues": {"all": [item], "error": [item], "warn": [], "info": []}}

    completeness = apply_evidence_completeness(result)

    assert completeness["total"] == 1
    assert completeness["completeness_rate"] == 1.0
    # 原地标记同步反映到各分桶（同一对象）
    assert result["issues"]["error"][0]["evidence_status"] == EVIDENCE_STATUS_COMPLETE


def test_empty_result_reports_none_completeness() -> None:
    """空样本红线：分母为 0 时完整率必须是 None——"没有问题"不等于"证据完整"。"""
    completeness = apply_evidence_completeness({"ai_findings": [], "rule_findings": []})
    assert completeness["total"] == 0
    assert completeness["completeness_rate"] is None
    assert completeness["locatable_total"] == 0
    assert completeness["locatable_completeness_rate"] is None
    assert completeness["degraded_count"] == 0


# --------------------------------------------------------------------------
# B1 口径：文档级规则（BUD-001 缺表/缺章节）单列
# --------------------------------------------------------------------------
def test_is_document_level_finding_anchors_on_rule_id() -> None:
    """文档级识别锚是规则编号，不是页码（引擎会把缺失页码折成 1）。"""
    assert is_document_level_finding({"rule_id": "BUD-001"})
    assert is_document_level_finding({"rule_id": "bud-001"})
    assert is_document_level_finding({"rule": "BUD-001"})
    # 其他规则即便页码缺失也是真证据缺口，不能豁免
    assert not is_document_level_finding({"rule_id": "C-001", "page_number": None})
    assert not is_document_level_finding({"rule_id": "BUD-101"})


def test_document_level_findings_are_counted_separately() -> None:
    """BUD-001 类文档级 finding 单列：不进可定位类分母，整体口径保留对照。

    文档级 finding 页码天然缺失，旧口径下永远是"不完整"，会把证据留痕
    完整的语料拖到 0%；新口径下可定位类完整率反映真实证据水平。
    """
    result = {
        "rule_findings": [
            {
                "id": "b1",
                "rule_id": "BUD-001",
                "source": "rule",
                "severity": "critical",
                "message": "缺少预算表：政府采购预算表",
            },
            {
                "id": "b2",
                "rule_id": "BUD-001",
                "source": "rule",
                "severity": "critical",
                "message": "缺少必要章节：三公经费说明",
            },
            {
                "id": "c1",
                "rule_id": "C-001",
                "source": "rule",
                "severity": "error",
                "location": {"page": 12},
                "evidence": [{"page": 12, "text": "合计 35.20"}],
            },
        ]
    }

    completeness = apply_evidence_completeness(result)

    assert completeness["total"] == 3
    assert completeness["complete"] == 1
    assert completeness["completeness_rate"] == round(1 / 3, 4)
    assert completeness["document_level_total"] == 2
    assert completeness["locatable_total"] == 1
    assert completeness["locatable_complete"] == 1
    assert completeness["locatable_completeness_rate"] == 1.0


def test_document_level_single_counting_keeps_rule_warning_marking() -> None:
    """单列只改统计口径，不改变 finding 级标记：缺页的 BUD-001 仍是规则告警。"""
    finding = {
        "id": "b1",
        "rule_id": "BUD-001",
        "source": "rule",
        "severity": "critical",
    }
    completeness = apply_evidence_completeness({"rule_findings": [finding]})
    assert finding["evidence_status"] == EVIDENCE_STATUS_RULE_WARNING
    assert completeness["document_level_total"] == 1
    assert completeness["locatable_total"] == 0
    assert completeness["locatable_completeness_rate"] is None


# --------------------------------------------------------------------------
# 计数口径与质量门禁联动
# --------------------------------------------------------------------------
def test_count_result_findings_excludes_degraded() -> None:
    result = {
        "ai_findings": [
            {"id": "a", "evidence_status": EVIDENCE_STATUS_DEGRADED},
            {"id": "b", "evidence_status": EVIDENCE_STATUS_COMPLETE},
        ],
        "rule_findings": [{"id": "c"}],
    }
    # 3 条里 1 条被降级 -> 正式问题 2 条
    assert _count_result_findings(result) == 2


def test_count_result_findings_keeps_legacy_findings_formal() -> None:
    """向后兼容：历史 finding 没有 evidence_status，仍按正式问题计数。"""
    legacy = {"issues": {"all": [{"id": "x"}, {"id": "y"}], "error": [], "warn": [], "info": []}}
    assert _count_result_findings(legacy) == 2


def test_gate_flags_review_when_findings_were_degraded() -> None:
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=2025,
        ai_requested=True,
        ai_degraded=False,
        issue_total=0,
        evidence_degraded_count=2,
    )

    assert gate["status"] == "review_required"
    assert gate["analysis_conclusion"] == "incomplete"
    assert "evidence_incomplete_findings" in _reason_codes(gate)
    assert gate["evidence_degraded_count"] == 2
    # 关键：不能因为正式问题数为 0 就报"确实没问题"
    assert gate["analysis_conclusion"] != "no_findings"


def test_gate_stays_done_when_no_degradation() -> None:
    """对照：没有降级项时门禁行为与 M1 完全一致。"""
    gate = _evaluate_quality_gate(
        page_assessment=GOOD_PAGES,
        report_kind="budget",
        report_year=2025,
        ai_requested=True,
        ai_degraded=False,
        issue_total=0,
        evidence_degraded_count=0,
    )
    assert gate["status"] == "done"
    assert gate["analysis_conclusion"] == "no_findings"
    assert "evidence_incomplete_findings" not in _reason_codes(gate)
    assert gate["evidence_degraded_count"] == 0


# --------------------------------------------------------------------------
# 端到端：流水线写入 meta.evidence_completeness
# --------------------------------------------------------------------------
class _FakePdf:
    def __init__(self, page_count: int) -> None:
        self.pages = [object()] * page_count

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def _run_legacy_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    job_dir = tmp_path / "job-evidence"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )

    full_text = "一般公共预算财政拨款支出预算表" * 10
    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: full_text
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {
                    "all": issue_items,
                    "error": list(issue_items),
                    "warn": [],
                    "info": [],
                }
            }
        ),
    )
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(
            return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
        ),
    )
    monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)
    return job_dir  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_pipeline_reports_evidence_completeness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整证据的规则问题：完整率 1.0、无降级、终态仍是 done + findings_detected。"""
    job_dir = _run_legacy_pipeline(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "C-001-1",
                "source": "rule",
                "severity": "error",
                "location": {"page": 12},
                "evidence": [{"page": 12, "text": "合计 35.20"}],
            }
        ],
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    completeness = payload["result"]["meta"]["evidence_completeness"]

    assert completeness["total"] == 1
    assert completeness["completeness_rate"] == 1.0
    assert completeness["degraded_count"] == 0
    assert completeness["formal_issue_total"] == 1
    assert payload["status"] == "done"
    assert payload["analysis_conclusion"] == "findings_detected"


@pytest.mark.asyncio
async def test_pipeline_rule_evidence_warning_does_not_change_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规则问题缺证据：记录告警与完整率下降，但仍是正式问题，终态不变。"""
    job_dir = _run_legacy_pipeline(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "C-001-1",
                "source": "rule",
                "severity": "error",
                "location": {},
                "evidence": [],
            }
        ],
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    completeness = payload["result"]["meta"]["evidence_completeness"]

    assert completeness["completeness_rate"] == 0.0
    assert completeness["rule_warning_count"] == 1
    assert completeness["degraded_count"] == 0
    assert completeness["formal_issue_total"] == 1
    assert payload["status"] == "done"
    assert payload["analysis_conclusion"] == "findings_detected"


@pytest.mark.asyncio
async def test_pipeline_degrades_ai_finding_and_gates_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双模式：唯一的 AI 问题缺证据 -> 降级、正式问题为 0、终态 review_required。

    这是与 M1 门禁联动的关键断言：不允许出现"问题全被降级却报 no_findings/done"。
    """
    job_dir = tmp_path / "job-ai-degrade"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "dual",
            "use_local_rules": False,
            "use_ai_assist": True,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )

    full_text = "一般公共预算财政拨款支出预算表" * 10
    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: full_text
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(
            return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
        ),
    )

    from src.schemas.issues import DualModeResponse, IssueItem, MergedSummary

    ai_item = IssueItem(
        id="ai-no-evidence",
        source="ai",
        rule_id="AI-SEM-001",
        severity="high",
        title="疑似三公经费口径不一致",
        message="没有可复核的原文证据",
        evidence=[],
        page_number=3,
    )
    # 构造"确实没有证据"的条目：display 会从 message 派生，需显式清空证据文本
    ai_item.evidence = []
    ai_item.text_snippet = None
    if ai_item.display is not None:
        ai_item.display.evidence_text = ""

    async def _fake_analyze(_ctx: Any, _cfg: Any) -> DualModeResponse:
        return DualModeResponse(
            job_id=job_dir.name,
            ai_findings=[ai_item],
            rule_findings=[],
            merged=MergedSummary(totals={"merged": 1}),
            meta={"elapsed_ms": {"total": 1}, "tokens": {}},
        )

    monkeypatch.setattr(pipeline_mod.dual_analyzer, "analyze", _fake_analyze)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    completeness = payload["result"]["meta"]["evidence_completeness"]
    gate = payload["result"]["meta"]["quality_gate"]

    assert completeness["degraded_count"] == 1
    assert completeness["formal_issue_total"] == 0
    assert completeness["completeness_rate"] == 0.0
    assert payload["result"]["ai_findings"][0]["severity"] == "manual_review"
    assert payload["result"]["ai_findings"][0]["evidence_status"] == EVIDENCE_STATUS_DEGRADED

    assert gate["issue_total"] == 0
    assert gate["evidence_degraded_count"] == 1
    assert payload["status"] == "review_required"
    assert payload["analysis_conclusion"] == "incomplete"
    assert "evidence_incomplete_findings" in [r["code"] for r in payload["review_reasons"]]
