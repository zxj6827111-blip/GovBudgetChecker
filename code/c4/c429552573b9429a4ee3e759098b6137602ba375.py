"""业务行反例；原件错误保留，合成控制不冒充真实材料验收。"""

from copy import deepcopy
from decimal import Decimal
import pytest
from src.schemas.parse_artifact import ParseArtifact, ParsedCell, ParsedTable, LogicalTableInstance
from src.services.fiscal_input_builder import FiscalInputBuilder


def contract(reverse=False):
    rows = [
        ["科目编码", "科目名称", "本年收入合计"],
        ["201", "甲", "12.00"],
        ["202", "尾乙", ""],
        ["203041", "丙", "12.00"],
    ]
    cells = [
        ParsedCell(
            r,
            c,
            text,
            text,
            None,
            1,
            (c * 100, r * 20, (c + 1) * 100, (r + 1) * 20),
            r == 0,
            0.99,
            "万元",
            "lines",
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    table = ParsedTable(
        "FIN_02_income",
        "收入决算表",
        1,
        0,
        (0, 0, 300, 80),
        1,
        0.99,
        "lines",
        "万元",
        rows,
        cells,
        fragment_uid="grid",
    )
    instance = LogicalTableInstance("instance", table.table_code, table.title, ["grid"], 1, 1)
    chars = []
    for x, y, text in [
        (5, 22, "201"),
        (105, 22, "甲"),
        (105, 34, "尾"),
        (205, 22, "12.00"),
        (5, 46, "202"),
        (105, 46, "乙"),
        (5, 66, "203041"),
        (105, 66, "丙"),
        (205, 66, "12.00"),
    ]:
        for offset, char in enumerate(text):
            chars.append(
                {
                    "physical_page": 1,
                    "text": char,
                    "bbox": [x + offset * 5, y, x + (offset + 1) * 5, y + 8],
                    "granularity": "character",
                    "upright": True,
                }
            )
    if reverse:
        chars.reverse()
    for index, char in enumerate(chars):
        char.update(char_index=index, source_id=f"native-p1-char{index}")
    return ParseArtifact(
        "a" * 64,
        source_fragments=chars,
        table_fragments=[table],
        source_grids=[deepcopy(table)],
        logical_tables=[instance],
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_cross_line_name_uses_code_start_and_keeps_empty_amount_row(reverse):
    artifact = contract(reverse)
    original = deepcopy(artifact.table_fragments[0].to_dict())
    builder = FiscalInputBuilder()
    context = builder.facts(artifact, org_name="测试局", fiscal_year=2024, report_kind="final")
    view = artifact.reconstructed_tables["instance"]
    assert view.rows[1][1].replace("\n", "") == "甲尾"
    assert view.rows[2][1] == "乙"
    assert view.rows[2][2] == ""  # 空金额行有名称归属，不补零。
    assert artifact.table_fragments[0].to_dict() == original
    assert artifact.source_grids[0].to_dict() == original
    assert [
        (f.subject_code, f.subject_name.replace("\n", ""), f.raw_value) for f in context.facts
    ] == [("201", "甲尾", "12.00"), ("203041", "丙", "12.00")]
    assert all(f.normalized_value == Decimal("12.00") for f in context.facts)
    # 原件异常编码保留 203041，不能按规范截成 20304 或添加末位数字。
    sources = {c["char_index"]: c for c in artifact.source_fragments}
    for cell in view.cells:
        for proof in cell.native_text_sources:
            assert (
                "".join(
                    sources[i]["text"]
                    for i in range(proof["start_char_index"], proof["end_char_index"])
                )
                == proof["text"]
            )
    saved = artifact.to_dict()
    assert ParseArtifact.from_dict(saved).to_dict() == saved
    builder.reconstruct(artifact)
    assert artifact.to_dict() == saved


def test_row_label_character_conflict_blocks_related_amounts():
    artifact = contract()
    # 两张实例引用同一物理正文，不能各自成为可用业务行。
    second = deepcopy(artifact.logical_tables[0])
    second.instance_uid = "second"
    artifact.logical_tables.append(second)
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2024, report_kind="final"
    )
    assert context.facts and not context.definite_facts()
    assert all(
        f.extra_dims.get("reconstruction_status") == "ownership_conflict" for f in context.facts
    )


def mixed_contract(raw="�", other="100"):
    from tests.remediation.test_round11_ocr import fixture

    artifact, candidate = fixture()
    cells = []
    rows = deepcopy(candidate.rows)
    rows[1][2] = raw
    rows[1][3] = other
    for source in candidate.cells:
        r, c = source["row_idx"], source["col_idx"]
        text = rows[r][c]
        cells.append(
            ParsedCell(
                r, c, text, text, None, 1, tuple(source["page_bbox"]), r == 0, 0.99, "万元", "lines"
            )
        )
    table = ParsedTable(
        "FIN_02_income",
        "收入决算表",
        1,
        0,
        (0, 0, 400, 100),
        1,
        0.99,
        "lines",
        "万元",
        rows,
        cells,
        fragment_uid="native-grid",
    )
    artifact.table_fragments = [table]
    artifact.source_grids = [deepcopy(table)]
    artifact.logical_tables = [
        LogicalTableInstance("hybrid", table.table_code, table.title, [table.fragment_uid], 1, 1)
    ]
    return artifact, candidate


def test_source_bound_visual_repairs_only_bad_native_field():
    artifact, _ = mixed_contract()
    original = deepcopy(artifact.source_grids[0].to_dict())
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert {f.raw_value for f in context.facts} == {"100"} and len(context.definite_facts()) == 2
    view = artifact.reconstructed_tables["hybrid"]
    assert (
        next(c for c in view.cells if (c.row_idx, c.col_idx) == (1, 2)).extraction_method == "ocr"
    )
    assert (
        next(c for c in view.cells if (c.row_idx, c.col_idx) == (1, 3)).extraction_method == "lines"
    )
    assert (
        artifact.source_grids[0].to_dict() == original
        and artifact.table_fragments[0].rows[1][2] == "�"
    )
    assert {f.extra_dims["evidence_refs"][0]["source_cell_id"] for f in context.facts} == {
        "native-grid:r1c2",
        "native-grid:r1c3",
    }
    assert any(d["status"] == "visual_field_selected" for d in artifact.reconstruction_diagnostics)


@pytest.mark.parametrize("raw", ["900", "1,23.00", "− 100"])
def test_visual_does_not_silently_legalize_original_amount_or_pick_between_valid_values(raw):
    artifact, _ = mixed_contract(raw=raw)
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    bad = next(f for f in context.facts if f.indicator == "total_actual")
    good = next(f for f in context.facts if f.indicator == "fiscal_allocation")
    assert bad.raw_value == raw and bad.recognition_status == "low_confidence"
    assert bad.extra_dims["reconstruction_status"] == "requires_regional_adjudication"
    assert good.normalized_value == Decimal(100) and good in context.definite_facts()
    if raw != "900":
        assert bad.normalized_value is None


def test_tampered_visual_response_cannot_replace_bad_native_field():
    artifact, candidate = mixed_contract()
    candidate.rows[1][2] = "999"
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert next(f for f in context.facts if f.indicator == "total_actual").raw_value == "�"
    assert not any(
        d["status"] == "visual_field_selected" for d in artifact.reconstruction_diagnostics
    )


def test_hybrid_rebuild_is_profile_bound_and_cached_views_are_not_authority():
    artifact, _ = mixed_contract()
    builder = FiscalInputBuilder()
    first = builder.facts(artifact, org_name="测试局", fiscal_year=2025, report_kind="final")
    saved = artifact.to_dict()
    builder.reconstruct(artifact)
    assert artifact.to_dict() == saved
    restored = ParseArtifact.from_dict(saved)
    assert builder.facts(restored).to_dict() == first.to_dict()
    restored.reconstructed_tables["hybrid"].cells[-1].raw_text = "999"
    rebuilt = builder.facts(restored)
    assert rebuilt.to_dict() == first.to_dict()


def test_visual_can_recover_missing_native_amount_without_adding_a_table():
    artifact, _ = mixed_contract(raw="")
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert len(context.definite_facts()) == 2
    assert len(artifact.logical_tables) == len(artifact.table_fragments) == 1
    assert artifact.source_grids[0].rows[1][2] == ""
    assert next(f for f in context.facts if f.indicator == "total_actual").raw_value == "100"


def test_source_bound_fragment_works_without_repeating_the_table_header_in_crop():
    from tests.remediation.test_round11_ocr import fixture
    from src.services.fiscal_fragment_adjudication import adjudicate_fragments
    from src.services.fiscal_business_rows import valid_samples, bind_literal_sources

    artifact, candidate = fixture()
    # 原生表提供原件身份、完整表头和标签，裁片仅保留数字片段。
    header = ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"]
    table_rows = [header, ["", "合计", "�", "100"]]
    cells = [
        ParsedCell(
            r,
            c,
            text,
            text,
            None,
            1,
            (c * 100, r * 50, (c + 1) * 100, (r + 1) * 50),
            r == 0,
            0.99,
            "万元",
            "lines",
        )
        for r, row in enumerate(table_rows)
        for c, text in enumerate(row)
    ]
    table = ParsedTable(
        "FIN_02_income",
        "收入决算表",
        1,
        0,
        (0, 0, 400, 100),
        1,
        0.99,
        "lines",
        "万元",
        table_rows,
        cells,
        fragment_uid="grid",
    )
    sources = []
    for r, row in enumerate(table_rows):
        for c, text in enumerate(row):
            if text == "�":
                continue
            for offset, char in enumerate(text):
                sources.append(
                    {
                        "physical_page": 1,
                        "char_index": len(sources),
                        "text": char,
                        "bbox": [
                            c * 100 + 5 + offset * 5,
                            r * 50 + 5,
                            c * 100 + 10 + offset * 5,
                            r * 50 + 15,
                        ],
                        "granularity": "character",
                    }
                )
    for offset, char in enumerate("测试局2025年决算"):
        sources.append(
            {
                "physical_page": 1,
                "char_index": len(sources),
                "text": char,
                "bbox": [5 + offset * 10, 150, 15 + offset * 10, 160],
                "granularity": "character",
            }
        )
    artifact.source_fragments = sources
    transform = artifact.coordinate_transforms[candidate.coordinate_transform_id]
    # fixture 的整体 OCR 仅含身份块。加入原始表内金额片段且重新绑定完整响应。
    source = transform["ocr_response"]
    raw = source["raw_prediction"]
    raw["overall_ocr_res"]["rec_texts"].append("100")
    raw["overall_ocr_res"]["rec_scores"].append(0.995)
    raw["overall_ocr_res"]["rec_polys"].append([[210, 60], [290, 60], [290, 90], [210, 90]])
    import hashlib
    import json

    source["raw_prediction_sha256"] = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    transform["page_request"]["raw_prediction_sha256"] = source["raw_prediction_sha256"]
    artifact.ocr_table_candidates = []
    pages = valid_samples(artifact)
    table = bind_literal_sources(table, pages)
    mappings = [
        {"source_col_idx": 2, "canonical_measure": "total_actual", "source_label_cols": [0, 1]}
    ]
    view, decisions = adjudicate_fragments(
        table, mappings, artifact, pages, organization="测试局", year=2025, report_kind="final"
    )
    assert view.rows[1][2] == "100" and table.rows[1][2] == "�"
    assert decisions[0]["status"] == "visual_field_selected"
    # 真实词级框整体保留，不按字数切成伪造的逐字框。
    assert next(c for c in view.cells if (c.row_idx, c.col_idx) == (1, 2)).ocr_provenance["cell"][
        "fragments"
    ] == [{"text": "100", "score": 0.995, "render_bbox": [210.0, 60.0, 290.0, 90.0]}]
    source["raw_prediction_sha256"] = "0" * 64
    unchanged, rejected = adjudicate_fragments(
        table, mappings, artifact, pages, organization="测试局", year=2025, report_kind="final"
    )
    assert unchanged.rows[1][2] == "�" and not rejected


def test_conflicting_continuation_identity_cannot_be_repaired_into_an_operand():
    artifact, _ = mixed_contract()
    artifact.logical_tables[0].identity_conflict = True
    artifact.logical_tables[0].conflict_reasons = ["续页单位显式冲突"]
    context = FiscalInputBuilder().facts(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert context.facts and not context.definite_facts()
    assert next(f for f in context.facts if f.indicator == "total_actual").raw_value == "�"
    assert all(f.extra_dims["table_identity_status"] == "conflict" for f in context.facts)
    assert context.input_diagnostics[0]["reasons"] == ["续页单位显式冲突"]


def test_public_and_ingest_profile_aliases_keep_one_parse_run():
    from src.services.structured_ingest_runner import _materialize_fiscal_facts_from_artifact

    artifact = contract()
    builder = FiscalInputBuilder()
    context = builder.facts(artifact, org_name="测试局", fiscal_year=2024, report_kind="final")
    frozen = deepcopy(artifact.to_dict())
    facts = _materialize_fiscal_facts_from_artifact(
        artifact,
        org_name="测试局",
        fiscal_year=2024,
        doc_type="部门决算",
        file_hash=artifact.source_file_hash,
    )
    assert artifact.to_dict() == frozen
    assert [f.to_dict() for f in facts] == [f.to_dict() for f in context.facts]
    assert builder.config_identity(
        "base", org_name="测试局", fiscal_year=2024, report_kind="final"
    ) == builder.config_identity("base", org_name="测试局", fiscal_year=2024, doc_type="部门决算")
