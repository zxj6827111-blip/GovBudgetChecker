"""人工补录/确认单元格：写回表格、生成标注来源的事实、接口留痕与待校正项读取。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

from api import runtime
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import tag_manual_fill_issues
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact
from src.services.correction_overlay_store import read_jsonl_records
from src.services.manual_cell_overlay import apply_table_cell_overlays
from src.services.table_import_receipt import build_table_import_receipt
from tests.remediation.test_budget_2022_table_semantics import grid
from tests.remediation.test_zc05 import JOB_ID, _basis, _headers, zc05_client  # noqa: F401

ORG = "上海市测试局"
HEAD = [["功能分类科目编码", "功能分类科目名称", "合计", "财政拨款收入"]]


def artifact(rows, ocr=False):
    table = grid("FIN_02_income", "2024年收入决算表", HEAD + rows, {}, 1, uid="t1")
    if ocr:
        table.extraction_method = "ocr"
        for cell in table.cells:
            cell.extraction_method = "ocr"
            status = "pending" if cell.raw_text == "6,194,400" else "qualified"
            cell.ocr_provenance = {"qualification": {"status": status}}
    built = ParseArtifact(source_file_hash="a" * 64)
    built.table_fragments = [table]
    built.physical_fragments = [copy.deepcopy(table)]
    built.logical_tables = [LogicalTableInstance(
        instance_uid="i1", table_code=table.table_code, title=table.title, fragment_uids=["t1"],
        page_start=1, page_end=1, org_name=ORG, fiscal_year=2024)]
    return built


def fill(row, col, value, record_id="cor-1"):
    return {"record_id": record_id, "actor": "审核员甲", "reason": "对照原页补录",
            "changes": [{"target_type": "table_instance", "target_id": "t1", "field": "cell_value",
                         "value": {"row_idx": row, "col_idx": col, "raw_value": value},
                         "location": {"physical_page": 1, "bbox": [20, 20, 30, 30]}}]}


def facts(built):
    return {fact.fact_uid: fact for fact in build_fact_context_from_artifact(
        built, org_name=ORG, fiscal_year=2024, report_kind="final").facts}


def test_blank_amount_is_written_into_the_table_and_marked_as_manual():
    built = artifact([["206", "科学技术支出", "300", "300"], ["221", "住房保障支出", "", ""],
                      ["合计", "", "500", "500"]])
    run_before = built.parse_run_uid
    report = apply_table_cell_overlays(built, [fill(2, 2, "200")])
    assert report["applied"] == 1 and not report["unresolved"]
    assert built.parse_run_uid != run_before
    assert built.table_fragments[0].rows[2][2] == "200"
    assert built.physical_fragments[0].rows[2][2] == "200"
    fact = facts(built)["t1:2:total_actual"]
    assert (fact.raw_value, fact.recognition_status) == ("200", "verified")
    assert fact.extra_dims["manual_fill"]["actor"] == "审核员甲"
    assert fact.extra_dims["evidence_refs"][0]["source_method"] == "manual"
    assert fact.extra_dims["evidence_refs"][0]["location_level"] == "region"


def test_same_fill_gives_the_same_parse_identity():
    first, second = artifact([["221", "住房保障支出", "", ""]]), artifact([["221", "住房保障支出", "", ""]])
    apply_table_cell_overlays(first, [fill(1, 2, "200")])
    apply_table_cell_overlays(second, [fill(1, 2, "200")])
    apply_table_cell_overlays(second, [fill(1, 2, "200")])
    assert first.parse_run_uid == second.parse_run_uid


def test_scan_cell_below_threshold_is_listed_then_confirmed_by_reviewer():
    rows = [["206", "科学技术支出", "101,360,282", "101,360,282"], ["221", "住房保障支出", "6,194,400", "6,194,400"]]
    built = artifact(rows, ocr=True)
    receipt = build_table_import_receipt(built, list(facts(built).values()), org_name=ORG)
    [table] = receipt["tables"]
    assert {(c["row"], c["column"]) for c in table["unqualified_ocr_cells"]} == {(2, 2), (2, 3)}
    assert table["rows"][2][2] == "6,194,400" and table["row_boxes"][2]["page"] == 1
    assert facts(built)["t1:2:total_actual"].recognition_status == "low_confidence"

    apply_table_cell_overlays(built, [fill(2, 2, "6,194,400"), fill(2, 3, "6,194,400", "cor-2")])
    assert facts(built)["t1:2:total_actual"].recognition_status == "verified"
    receipt = build_table_import_receipt(built, list(facts(built).values()), org_name=ORG)
    assert receipt["tables"][0]["unqualified_ocr_cells"] == []


def test_fills_outside_the_data_area_or_over_native_amounts_are_refused():
    built = artifact([["206", "科学技术支出", "300", ""]])
    run_before = built.parse_run_uid
    report = apply_table_cell_overlays(built, [
        fill(0, 2, "1"), fill(5, 0, "1"), fill(1, 3, "三百"), fill(1, 2, "301"),
        {**fill(1, 3, "1"), "changes": [{**fill(1, 3, "1")["changes"][0], "target_id": "missing"}]},
    ])
    assert report["applied"] == 0
    assert [item["reason"] for item in report["unresolved"]] == [
        "表头行不接受补录", "补录位置超出表格矩阵", "金额列只能补录数字",
        "该格已有文字层读出的金额，改值请走事实校正", "补录目标表不在当前解析结果中"]
    assert built.parse_run_uid == run_before


def test_findings_using_manual_data_are_tagged():
    context = SimpleNamespace(facts=[SimpleNamespace(fact_uid="f1", extra_dims={"manual_fill": {"actor": "甲"}}),
                                     SimpleNamespace(fact_uid="f2", extra_dims={})])
    items = [{"fact_ids": ["f1", "f2"], "tags": ["R1"]}, {"fact_ids": ["f2"], "tags": ["R2"]}]
    tag_manual_fill_issues(items, context)
    assert items[0]["manual_fill_fact_ids"] == ["f1"] and items[0]["tags"] == ["R1", "人工补录"]
    assert "manual_fill_fact_ids" not in items[1]


def test_cell_fill_api_turns_the_original_page_location_into_evidence(zc05_client):  # noqa: F811
    client, job_dir = zc05_client
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json={
        "analysis_basis_token": _basis(), "reason": "对照原页补录扫描件未读金额",
        "changes": [{"target_type": "table_instance", "target_id": "t1", "field": "cell_value",
                     "value": {"row_idx": 2, "col_idx": 2, "raw_value": "200"},
                     "location": {"physical_page": 1, "bbox": [20, 15, 200, 55]}}]})
    assert response.status_code == 202, response.text
    record = read_jsonl_records(job_dir / "corrections.jsonl")[-1]
    [evidence] = record["manual_evidence"]
    change = record["changes"][0]
    assert change["evidence_ids"] == [evidence["evidence_id"]]
    assert change["location"] == {"physical_page": 1, "bbox": [20.0, 15.0, 200.0, 55.0]}
    assert evidence["source_method"] == "manual" and evidence["verification_status"] == "unverified"


def test_cell_fill_api_needs_a_position_value_and_evidence(zc05_client):  # noqa: F811
    client, job_dir = zc05_client
    base = {"target_type": "table_instance", "target_id": "t1", "field": "cell_value",
            "value": {"row_idx": 2, "col_idx": 2, "raw_value": "200"}}
    for change in ({**base}, {**base, "location": {"physical_page": 1, "bbox": [20, 15, 200, 55]},
                              "value": {"row_idx": -1, "col_idx": 2, "raw_value": "200"}},
                   {**base, "target_type": "fact", "location": {"physical_page": 1, "bbox": [20, 15, 200, 55]}},
                   {**base, "location": {"physical_page": 9, "bbox": [20, 15, 200, 55]}}):
        response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json={
            "analysis_basis_token": _basis(), "reason": "对照原页补录", "changes": [change]})
        assert response.status_code == 422, response.text
    assert not (job_dir / "corrections.jsonl").exists()


def test_correction_targets_lists_alerted_tables_with_column_choices(zc05_client):  # noqa: F811
    client, job_dir = zc05_client
    status = runtime.read_json_file(job_dir / "status.json")
    status["structured_ingest"] = {"reading_import": {
        "tables": [
            {"fragment_uid": "t1", "table_code": "FIN_02_income", "title": "收入决算表", "page": 1,
             "rows": [["科目", "合计"], ["206", "300"]], "row_boxes": [None, {"page": 1, "bbox": [0, 0, 9, 9]}],
             "header_row_count": 1, "unresolved_money_cells": [], "unqualified_ocr_cells": [],
             "column_mappings": [], "arithmetic_conflicts": []},
            {"fragment_uid": "t2", "table_code": "FIN_03_expenditure", "title": "完整的表", "page": 2},
        ],
        "reading_quality": {"status": "partial", "gaps": [{"id": "g1", "page": 1, "reason": "native_amount_not_extracted",
                                                           "raw_text": "200", "affected_fragment_uids": ["t1"]}]},
    }}
    runtime.write_json_file(job_dir / "status.json", status)
    body = client.get(f"/api/jobs/{JOB_ID}/correction-targets", headers=_headers()).json()
    assert body["analysis_basis_token"] == _basis()
    [table] = body["tables"]
    assert table["fragment_uid"] == "t1" and table["row_boxes"][1]["page"] == 1
    assert {"measure": "total_actual", "label": "合计"} in table["measure_options"]
    assert body["gaps"][0]["raw_text"] == "200" and body["history"] == []


def test_missing_row_label_filled_by_reviewer_binds_the_row_and_marks_its_source():
    built = artifact([["", "", "500", "500"], ["206", "科学技术支出", "500", "500"]])
    receipt = build_table_import_receipt(built, list(facts(built).values()), org_name=ORG)
    assert {c["reason"] for c in receipt["tables"][0]["unresolved_money_cells"]} == {"source_label_missing"}
    apply_table_cell_overlays(built, [fill(1, 0, "合计")])
    rebuilt = facts(built)
    receipt = build_table_import_receipt(built, list(rebuilt.values()), org_name=ORG)
    assert receipt["tables"][0]["unresolved_money_cells"] == []
    fact = rebuilt["t1:1:total_actual"]
    assert fact.extra_dims["manual_fill"]["scope"] == "row_label" and fact.recognition_status == "unverified"


def test_value_corrected_fact_still_counts_as_read_from_its_cell():
    from src.engine.fact_context import FactOverlay, apply_fact_overlay

    built = artifact([["206", "科学技术支出", "300", "300"]])
    context = build_fact_context_from_artifact(built, org_name=ORG, fiscal_year=2024, report_kind="final")
    corrected = apply_fact_overlay(context, [FactOverlay(fact_uid="t1:1:total_actual", raw_value="301", reason="改值")])
    receipt = build_table_import_receipt(built, corrected.facts, org_name=ORG)
    assert receipt["tables"][0]["unresolved_money_cells"] == []
