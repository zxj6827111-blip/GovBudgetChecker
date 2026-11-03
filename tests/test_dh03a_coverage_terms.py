"""DH-03a：legacy_coverage_terms 的生成、投影与同源判定契约。"""

from __future__ import annotations

from types import SimpleNamespace

from src.engine.check_obligations import build_obligation_ledger
from src.services.material_detail_query_service import build_coverage
from src.services.obligation_finding_coverage import obligation_covered_by_finding


def _finding(message: str) -> dict:
    return {
        "id": "finding-1",
        "rule_id": "V33-222",
        "tags": ["V33-222"],
        "severity": "warn",
        "message": message,
        "location": {"page": 12},
    }


def _item(**overrides):
    base = {
        "status": "insufficient_data",
        "checkers": ["V33-222"],
        "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
        "legacy_coverage_terms": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_legacy_terms_are_frozen_from_old_detail() -> None:
    ledger = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={
            "rule_statuses": {"V33-240": "insufficient_data"},
            "unresolved_rules": [{
                "rule_id": "V33-240",
                "status": "insufficient_data",
                "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
            }],
        },
    )
    item = next(
        row for row in ledger["instances"]
        if row["obligation_id"] == "OBL-INTERNAL-T2-TOTAL"
    )
    assert item["status"] == "insufficient_data"
    assert item["legacy_coverage_terms"] == ["旧科目名称"]


def test_empty_list_is_new_result_and_never_reads_detail() -> None:
    result = {"rule_findings": [_finding("旧科目名称在表内找不到")]}
    assert obligation_covered_by_finding(_item(legacy_coverage_terms=[]), result) is False
    # 旧结果（字段缺失或 null）才回退旧算法。
    assert obligation_covered_by_finding(
        _item(legacy_coverage_terms=None), result
    ) is True
    assert obligation_covered_by_finding(
        _item(legacy_coverage_terms=["旧科目名称"]), result
    ) is True
    # 新列表不命中时，即使旧 detail 命中也不得关闭。
    assert obligation_covered_by_finding(
        _item(legacy_coverage_terms=["另一个科目"]), result
    ) is False


def test_new_list_keeps_union_semantics_for_two_sub_checks() -> None:
    result = {"rule_findings": [_finding("旧科目名称与另一个科目都不一致")]}
    item = _item(
        detail="说明中的款“旧科目名称”“另一个科目”在表内找不到同名科目",
        legacy_coverage_terms=["旧科目名称", "另一个科目"],
    )
    assert obligation_covered_by_finding(item, result) is True


def test_coverage_projection_preserves_none_and_list() -> None:
    raw = {
        "catalog_version": "obligations-v12-checklist-global-20261022",
        "applicable_total": 2, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 2, "blocking_total": 2,
        "instances": [
            {
                "obligation_id": "OBL-OLD", "status": "insufficient_data",
                "detail": "旧结果", "checkers": ["V33-222"],
            },
            {
                "obligation_id": "OBL-NEW", "status": "insufficient_data",
                "detail": "新结果", "checkers": ["V33-222"],
                "legacy_coverage_terms": ["旧科目名称"],
            },
        ],
    }
    coverage = build_coverage(raw)
    by_id = {item.obligation_id: item for item in coverage.items}
    assert by_id["OBL-OLD"].legacy_coverage_terms is None
    assert by_id["OBL-NEW"].legacy_coverage_terms == ["旧科目名称"]


def test_export_and_review_model_keep_legacy_terms() -> None:
    from api.routes.reports import _build_check_scope_export
    from src.schemas.review_lifecycle import ObligationReviewItem

    raw = {
        "catalog_version": "obligations-v12-checklist-global-20261022",
        "applicable_total": 1, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 1, "blocking_total": 1,
        "instances": [{
            "obligation_id": "OBL-TXT-T5", "status": "insufficient_data",
            "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
            "checkers": ["V33-222"],
            "legacy_coverage_terms": ["旧科目名称"],
        }],
    }
    exported = _build_check_scope_export({
        "check_coverage": raw,
        "document_profile": {},
    })
    assert exported["unfinished_checks"][0]["legacy_coverage_terms"] == ["旧科目名称"]
    item = ObligationReviewItem(
        obligation_id="OBL-TXT-T5",
        status="insufficient_data",
        legacy_coverage_terms=["旧科目名称"],
    )
    assert item.legacy_coverage_terms == ["旧科目名称"]


def test_old_task_projected_through_api_model_keeps_legacy_null_and_empty_codes() -> None:
    """真实 API 模型投影不能把旧任务伪装成“新结果”。

    旧任务经 ``build_coverage`` → ``ObligationReviewItem`` 后，``reason_codes``
    会被模型默认成 ``[]``；旧/新判别必须继续看 ``legacy_coverage_terms``
    是 ``None``（旧）还是列表（新，含 ``[]``）。
    """
    from src.schemas.review_lifecycle import ObligationReviewItem

    raw = {
        "catalog_version": "obligations-v12-checklist-global-20261022",
        "applicable_total": 2, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 2, "blocking_total": 2,
        "instances": [
            {
                "obligation_id": "OBL-TXT-T5", "status": "insufficient_data",
                "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
                "checkers": ["V33-222", "V33-227"],
            },
            {
                "obligation_id": "OBL-TXT-T5-NEW", "status": "insufficient_data",
                "detail": "新结果",
                "checkers": ["V33-222"],
                "reason_codes": [],
                "legacy_coverage_terms": [],
            },
        ],
    }
    coverage = build_coverage(raw)
    projected = {
        item.obligation_id: ObligationReviewItem(**item.model_dump()).model_dump()
        for item in coverage.items
    }
    old_task = projected["OBL-TXT-T5"]
    assert old_task["reason_codes"] == []
    assert old_task["legacy_coverage_terms"] is None
    assert old_task["checkers"] == ["V33-222", "V33-227"]
    new_task = projected["OBL-TXT-T5-NEW"]
    assert new_task["reason_codes"] == []
    assert new_task["legacy_coverage_terms"] == []
