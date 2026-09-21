"""报告画像的唯一性与"不猜测"纪律测试。

覆盖 plan §1 的验收要求："同一 PDF 在上传、规则执行、AI 分析、入库和导出环节
使用同一个报告画像，不能各自重新猜文种。" 因此本文件最重要的一条断言是
**四个入口对同一份材料必须给出同一个文种**；其次是"识别不到就留空"，
不允许任何入口用经验值兜底。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import main as pipeline_mod  # noqa: E402
from api import runtime  # noqa: E402
from src.engine import common_rules as common_rules_mod  # noqa: E402
from src.engine import pipeline  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402
from src.schemas.document_profile import (  # noqa: E402
    PROFILE_STATUS_PARTIAL,
    PROFILE_STATUS_RESOLVED,
    PROFILE_STATUS_UNRESOLVED,
    DocumentProfile,
)
from src.services import engine_rule_runner as runner_mod  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402
from src.schemas.issues import JobContext  # noqa: E402


def _all_entry_kinds(path: str, page_texts):
    """从四个入口分别取文种，用于一致性断言。"""
    doc = build_document(path=path, page_texts=page_texts, page_tables=[[]], filesize=1)
    runner = runner_mod.EngineRuleRunner()
    job_context = JobContext(job_id="j", pdf_path=path, page_texts=list(page_texts))
    return {
        "pipeline": pipeline._resolve_report_kind(doc),
        "engine_rule_runner": runner._resolve_report_kind(job_context, doc),
        "common_rules": common_rules_mod._infer_report_kind(doc),
        "api_runtime": runtime.normalize_report_kind(None, Path(path).name),
    }


# ---------------------------------------------------------------------------
# 单一事实源：四个入口必须一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "page_texts", "expected"),
    [
        ("财务决算公开.pdf", ["2024年度部门决算"], "final"),
        ("unit-2026-budget.pdf", ["2026年单位预算"], "budget"),
        ("plain.pdf", ["2025年部门决算公开材料"], "final"),
        ("plain.pdf", ["2025年部门预算公开材料"], "budget"),
        ("plain.pdf", ["公开材料说明"], "unknown"),
    ],
)
def test_engine_entries_resolve_the_same_report_kind(path, page_texts, expected):
    """三个能同时看到文件名与正文的入口必须给出相同文种。

    此前这四处各写一份实现，且兜底值互相矛盾（common_rules 默认 final、
    pipeline 默认 unknown）。同一 PDF 在上传与规则执行之间被换成另一套检查
    配置时，结果里没有任何字段能显示这件事。
    """
    kinds = _all_entry_kinds(path, page_texts)
    engine_kinds = {
        key: value for key, value in kinds.items() if key != "api_runtime"
    }
    assert set(engine_kinds.values()) == {expected}, kinds


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2024年度部门决算.pdf", "final"),
        ("unit-2026-budget.pdf", "budget"),
        ("plain.pdf", "unknown"),
    ],
)
def test_upload_entry_agrees_with_engine_entries_on_filename_signal(
    filename, expected
):
    """上传环节只拿得到"文种 + 文件名"，有信号时也必须与引擎入口同结论。

    上传时正文还没解析出来，所以这里只比对文件名能决定的那部分；
    正文参与判定的一致性由上一个用例覆盖。
    """
    assert runtime.normalize_report_kind(None, filename) == expected
    engine = pipeline._resolve_report_kind(
        build_document(path=filename, page_texts=[""], page_tables=[[]], filesize=1)
    )
    assert engine == expected


def test_pipeline_resolves_kind_once_and_common_rules_consume_it():
    """一次解析、全程消费（独立验收 2026-09-17 kind_disagreement 反例）。

    显式指定 final、文件名与正文都是"部门预算"：pipeline 判 final 后，
    通用规则不得再按文件名判 budget——同一份 PDF 在两个环节拿到互斥的
    检查配置，正是独立验收复现的主流程/通用规则互斥结论。pipeline
    解析一次并把结论挂到 Document.report_kind，规则体消费同一个值。
    """
    doc = build_document(
        path="2024部门预算.pdf",
        page_texts=["2024年度部门预算"],
        page_tables=[[]],
        filesize=1,
    )
    assert common_rules_mod._infer_report_kind(doc) == "budget", "未挂接时按文件名推断"
    pipeline.run_rules_with_outcomes(doc, report_kind="final", rules=[])
    assert doc.report_kind == "final"
    assert common_rules_mod._infer_report_kind(doc) == "final", "挂接后不得重新猜"


def test_repository_directory_name_does_not_route_rules():
    """仓库目录名含 "Budget"，不得据此把材料判成预算。

    这是历史真缺陷：整串路径参与关键词匹配时，任何位于仓库下的 PDF
    都会被判成预算，决算规则整套不执行。
    """
    doc = SimpleNamespace(
        path=r"E:\Software Development\GovBudgetChecker\uploads\plain.pdf",
        page_texts=["2024年度部门决算"],
        page_tables=[[]],
    )
    assert pipeline._resolve_report_kind(doc) == "final"


def test_windows_path_basename_is_extracted_on_any_platform():
    """Windows 风格路径即使含目录关键词，也只按基名判定。"""
    profile = resolve_document_profile(
        path=r"E:\dir\GovBudgetChecker\plain.pdf",
        page_texts=["2024年度部门决算"],
    )
    assert profile.kind == "final"


# ---------------------------------------------------------------------------
# 识别冲突与来源优先级
# ---------------------------------------------------------------------------


def test_conflicting_candidates_are_recorded_not_silently_dropped():
    """文种互斥时保留全部依据。

    上传文种（用户选的"决算"）与文件名（含"预算"）矛盾时，取来源优先级
    更高的上传文种，并把落选候选写进 rejected，供人工确认。旧实现把两者
    混在一个"预算优先"的判断里，冲突被静默消掉。
    """
    profile = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年预算公开材料.pdf",
    )
    assert profile.kind == "final"
    assert profile.report_kind.rejected, "落选候选必须保留原始依据"
    assert any(item["value"] == "budget" for item in profile.report_kind.rejected)
    assert profile.conflicts, "互斥候选必须记入 conflicts"
    assert profile.conflicts[0]["field"] == "report_kind"


def test_explicit_source_wins_over_filename():
    profile = resolve_document_profile(
        explicit_report_kind="budget",
        filename="2024年度部门决算.pdf",
    )
    assert profile.kind == "budget"
    assert profile.report_kind.source == "explicit"


def test_kind_conflict_downgrades_status_and_states_why():
    """文种互斥时状态不得是 resolved（独立验收 2026-09-17 反例）。

    反例：显式指定 final、封面/文件名/正文都是预算。画像记录了
    "文种冲突，需人工确认"，profile_status 却是 resolved——按优先级
    取的只是候选选择，不是"已确认"。必须降为 partial 并给出原因码，
    台账才有依据把冲突登记为阻塞事项。
    """
    profile = resolve_document_profile(
        explicit_report_kind="final",
        filename="2024部门预算.pdf",
        page_texts=["2024年度部门预算"],
    )
    assert profile.kind == "final", "显式值保留为候选选择"
    assert profile.profile_status == PROFILE_STATUS_PARTIAL
    assert profile.unsupported_reason == "report_kind_conflict"
    assert profile.has_kind_conflict is True
    assert profile.conflicts[0]["field"] == "report_kind"

    # 无互斥候选的干净画像不得被这条规则误降级
    clean = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年度部门决算.pdf",
        page_texts=["2024年度部门决算"],
    )
    assert clean.profile_status == PROFILE_STATUS_RESOLVED
    assert clean.has_kind_conflict is False


# ---------------------------------------------------------------------------
# 不猜测纪律
# ---------------------------------------------------------------------------


def test_unknown_kind_is_unresolved_and_states_why():
    profile = resolve_document_profile(page_texts=["公开材料说明"])
    assert profile.kind == "unknown"
    assert profile.profile_status == PROFILE_STATUS_UNRESOLVED
    assert profile.unsupported_reason == "report_kind_unresolved"
    assert profile.to_dict()["report_kind"]["confidence"] == 0.0


def test_missing_metadata_is_left_empty_not_fabricated():
    """识别不到就留空：不得兜底成任何年份、机构或地区。"""
    profile = resolve_document_profile(page_texts=["某单位公开材料"])
    assert profile.year is None
    assert profile.organization_name.value is None
    assert profile.jurisdiction.value is None
    assert profile.caliber.value is None
    assert profile.fund_scope_values() == []
    assert profile.extraction_quality.value is None
    # 文种未知时状态必须是 unresolved，而不是"已识别完成"
    assert profile.profile_status == PROFILE_STATUS_UNRESOLVED


def test_resolved_profile_requires_both_kind_and_level():
    """只有文种、没有主体层级时是 partial：层级专项检查的适用性尚未确认。"""
    profile = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年度部门决算.pdf",
        page_texts=["一、收入支出决算总体情况说明"],
    )
    assert profile.kind == "final"
    assert profile.level == "unknown"
    assert profile.profile_status == PROFILE_STATUS_PARTIAL
    assert profile.is_fully_resolved is False


def test_cover_label_gives_level_and_kind():
    profile = resolve_document_profile(
        page_texts=[
            "\n".join(
                [
                    "上海市普陀区2026年区级单位预算",
                    "预算单位：上海市普陀区人民政府石泉路街道办事处（本级）",
                ]
            )
        ],
        filename="石泉26单位.pdf",
    )
    assert profile.kind == "budget"
    assert profile.level == "unit"
    assert profile.organization_name.value == "上海市普陀区人民政府石泉路街道办事处（本级）"
    assert profile.profile_status == PROFILE_STATUS_RESOLVED


def test_government_level_is_not_inferred_from_organisation_name():
    """机构名含"人民政府"不等于政府级材料。

    "…人民政府某街道办事处"是**街道单位**的预算，按机构名判成政府级会把
    部门材料错分到政府口径——比识别不到更危险。只有封面标题直接写
    "政府预算/政府决算"才判政府级。
    """
    unit_profile = resolve_document_profile(
        page_texts=[
            "上海市普陀区2026年区级单位预算\n预算单位：上海市普陀区人民政府石泉路街道办事处（本级）"
        ]
    )
    assert unit_profile.level == "unit"

    gov_profile = resolve_document_profile(
        page_texts=["2025年上海市普陀区政府决算\n收支决算总表"],
    )
    assert gov_profile.level == "government"


# ---------------------------------------------------------------------------
# 口径 / 资金范围 / 质量维度
# ---------------------------------------------------------------------------


def test_caliber_and_fund_scopes_are_detected_from_material():
    profile = resolve_document_profile(
        page_texts=[
            "2024年度部门决算汇总",
            "政府性基金预算财政拨款收入支出决算表",
            "国有资本经营预算财政拨款收入支出决算表",
            "一般公共预算财政拨款支出决算表",
        ]
    )
    assert profile.caliber.value == "summary"
    assert set(profile.fund_scope_values()) == {
        "general_public",
        "government_fund",
        "state_capital",
    }


def test_extraction_quality_is_derived_from_page_assessment():
    text_profile = resolve_document_profile(
        page_texts=["x"],
        page_assessment={"page_count": 10, "page_coverage": 1.0, "scanned_page_count": 0},
    )
    assert text_profile.extraction_quality.value == "text"

    mixed_profile = resolve_document_profile(
        page_texts=["x"],
        page_assessment={"page_count": 10, "page_coverage": 0.7, "scanned_page_count": 1},
    )
    assert mixed_profile.extraction_quality.value == "mixed"

    scanned_profile = resolve_document_profile(
        page_texts=["x"],
        page_assessment={"page_count": 4, "page_coverage": 0.0, "scanned_page_count": 4},
    )
    assert scanned_profile.extraction_quality.value == "scanned"


# ---------------------------------------------------------------------------
# 持久化兼容
# ---------------------------------------------------------------------------


def test_profile_roundtrip_and_legacy_absence():
    profile = resolve_document_profile(doc_type="dept_final", filename="2024年度部门决算.pdf")
    restored = DocumentProfile.from_dict(profile.to_dict())
    assert restored is not None
    assert restored.kind == profile.kind
    assert restored.resolver_version == profile.resolver_version

    # 旧任务没有画像字段：返回 None 而不是空画像，上层才能区分
    # "没有记录"与"记录为空"，并显示"旧版未记录"而不是"已完成识别"。
    assert DocumentProfile.from_dict(None) is None
    assert DocumentProfile.from_dict({"report_kind": "not-a-dict"}) is None


def test_extract_cover_metadata_keeps_legacy_keys_and_adds_profile():
    cover = runtime.extract_cover_metadata(
        page_texts=["2026年单位预算\n预算单位：某街道办事处"],
        filename="unit-2026-budget.pdf",
        preferred_year=None,
        doc_type=None,
    )
    for key in ("cover_title", "cover_org_name", "cover_org_label", "scope_hint",
                "report_kind", "report_year", "doc_type"):
        assert key in cover, key
    assert cover["report_kind"] == "budget"
    assert cover["scope_hint"] == "unit"
    assert cover["profile"]["resolver_version"] == "document-profile-v1"
    assert cover["profile_status"] in {"resolved", "partial", "unresolved"}


def test_pipeline_module_reexports_build_document_for_existing_callers():
    """收敛入口不得破坏既有再导出（replay 脚本依赖）。"""
    assert pipeline_mod.build_document is build_document
