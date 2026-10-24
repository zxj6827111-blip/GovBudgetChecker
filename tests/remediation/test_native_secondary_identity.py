"""Secondary recognition must not replace original fiscal table identity."""

from itertools import permutations
from unittest.mock import AsyncMock

import pytest

from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.fiscal_table_rules import detect_table_code
from src.services.pdf_parser import PDFParser
from src.services.structured_ingest_runner import _build_review_items, _legacy_artifact_projection
from src.services.table_recognizer import ColumnMapping, TableInstance, TableRecognizer


FIN01 = "FIN_01_income_expenditure_total"
FIN03 = "FIN_03_expenditure"
FIN04 = "FIN_04_fiscal_grant_total"
FIN05 = "FIN_05_general_public_expenditure"


def cells_for(rows, code, *, page=7, confidence=.99, method="lines"):
    return [dict(table_code=code, row_idx=r, col_idx=c, raw_text=text,
                 page_number=page, confidence=confidence, extraction_method=method)
            for r, row in enumerate(rows) for c, text in enumerate(row)]


@pytest.mark.parametrize("title", [
    "一、收入支出决算总体情况说明",
    "二、收入决算情况说明",
    "一般公共预算财政拨款支出决算情况说明",
    "财政拨款“三公”经费支出决算情况说明",
    "收入决算表：反映单位本年取得的全部收入",
    "收入支出决算总表是指收入和支出汇总情况",
    "收入决算表：指本年取得的收入构成",
    "定义：收入支出决算总表",
    "根据收入支出决算总表本年支出100万元",
    "收入支出决算总表中本年支出100万元",
    "名词解释：一般公共预算财政拨款收入",
])
def test_narration_or_definition_cannot_be_promoted_by_fiscal_words(title):
    # Full header keywords do not turn a narration heading into a core table.
    code, confidence = detect_table_code(title, ["本年收入", "本年支出", "合计", "财政拨款收入"])
    assert code is None
    assert confidence == 0
    assert TableRecognizer(None)._recognize_single_table(
        "UNKNOWN_P21_T0", cells_for([[title], ["本年收入"], ["本年支出"]],
                                    "UNKNOWN_P21_T0", page=21, confidence=.32, method="text"),
    ) is None


def test_title_similarity_without_independent_table_structure_is_not_evidence():
    assert detect_table_code("收入支出决算总体情况", ["今年收入支出总计100万元"])[0] is None
    assert detect_table_code("收支决算汇总表", ["项目", "年度", "说明"])[0] is None
    assert detect_table_code("收支决算汇总表", ["本年收入本年支出"])[0] is None
    assert detect_table_code("附件目录", [], source_hint="FIN说明01概况")[0] is None


@pytest.mark.parametrize("source_code,title", [(FIN01, "收入 支出"), (FIN05, "支出决算表"),
                                               (FIN05, "决算数 较上年增减")])
def test_native_canonical_code_and_confidence_survive_generic_cell_title(source_code, title):
    cells = cells_for([[title, ""], ["项目", "决算数"], ["合计", "100.00"]], source_code)
    result = TableRecognizer(None)._recognize_single_table(source_code, cells)
    assert result.table_code == source_code
    assert result.confidence == .99
    assert result.native_identity


def test_real_low_confidence_table_stays_recognized_and_reviewable():
    cells = cells_for([["支出决算表", "", ""], ["合计", "基本支出", "项目支出"],
                       ["100.00", "70.00", "30.00"]], "UNKNOWN_P3_T0", page=3,
                      confidence=.63, method="ocr")
    result = TableRecognizer(None)._recognize_single_table("UNKNOWN_P3_T0", cells)
    assert result.table_code == FIN03
    assert result.confidence == .63
    review = _build_review_items({}, [result], {"facts_count": 3, "low_confidence_tables": [FIN03]})
    assert any(item["type"] == "low_confidence_table" and item["confidence"] == .63 for item in review)


def test_fuzzy_real_title_with_separate_headers_remains_available_for_review():
    rows = [["收支决算汇总表", ""], ["本年收入", "本年支出"], ["100.00", "100.00"]]
    result = TableRecognizer(None)._recognize_single_table(
        "UNKNOWN_P4_T0", cells_for(rows, "UNKNOWN_P4_T0", confidence=.64),
    )
    assert (result.table_code, result.confidence) == (FIN01, .64)


def test_empty_conditional_core_table_keeps_its_identity():
    code = "FIN_09_state_capital"
    rows = [["国有资本经营预算财政拨款收入支出决算表", "", ""],
            ["合计", "基本支出", "项目支出"], ["", "", ""]]
    result = TableRecognizer(None)._recognize_single_table(code, cells_for(rows, code))
    assert (result.table_code, result.confidence) == (code, .99)


def test_coalesced_continuation_does_not_downgrade_native_head():
    cells = cells_for([["收入", "", "支出", ""], ["项目", "决算数", "项目", "决算数"],
                       ["财政拨款收入", "100.00", "一般公共服务", "100.00"]], FIN01)
    cells.extend(dict(cell, row_idx=cell["row_idx"] + 3) for cell in
                 cells_for([["总计", "100.00", "总计", "100.00"]], FIN01,
                           page=8, confidence=.61))
    result = TableRecognizer(None)._recognize_single_table(FIN01, cells)
    assert (result.table_code, result.confidence, result.page_number, result.row_end) == (FIN01, .99, 7, 3)


def instance(title, confidence, page, mapping_col, *, native=False):
    return TableInstance(FIN01, title, confidence, page, 0, 10,
                         [ColumnMapping(mapping_col, title, "income_actual", confidence)],
                         source_table_code=FIN01 if native else "UNKNOWN", native_identity=native)


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_write_boundary_keeps_one_winner_and_never_blends_rejected_columns(reverse):
    conn = AsyncMock()
    conn.fetchval.return_value = 42
    real = instance("收入 支出", .99, 7, 1, native=True)
    weak = instance("收入支出决算总体", .63, 21, 2)
    candidates = [weak, real] if reverse else [real, weak]
    await TableRecognizer(conn).save_table_instances(1, candidates)
    assert conn.fetchval.await_count == 1
    args = conn.fetchval.await_args.args
    assert args[2:6] == (FIN01, real.source_title, .99, 7)
    mapping_calls = [call.args for call in conn.execute.await_args_list if "INSERT INTO fiscal_column_mappings" in call.args[0]]
    assert len(mapping_calls) == 1
    assert mapping_calls[0][2] == 1


def test_reliable_native_identity_wins_even_over_inflated_fuzzy_confidence():
    real = instance("收入 支出", .98, 7, 1, native=True)
    text = instance("收入支出决算总表", .99, 21, 2)
    recognizer = TableRecognizer(None)
    assert recognizer._select_best_instances([real, text]) == [real]
    assert recognizer._select_best_instances([text, real]) == [real]
    low = instance("收入支出决算总表", .6, 7, 1, native=True)
    assert recognizer._select_best_instances([low]) == [low]


def parsed_table(rows, code, uid, page, *, method="lines", confidence=.99, title=None):
    parser = PDFParser(None)
    cells = [ParsedCell(r, c, text, text, parser._parse_numeric(text), page,
                        (c * 20, r * 10, (c + 1) * 20, (r + 1) * 10), r < 2,
                        confidence, "万元", method)
             for r, values in enumerate(rows) for c, text in enumerate(values)]
    return ParsedTable(code, title or "财政拨款收入支出决算总表", page, 0,
                       (0, 0, 140, len(rows) * 10), 2, confidence, method,
                       "万元", rows, cells, uid)


def artifact_for(tables):
    return ParseArtifact(source_file_hash="a" * 64, page_count=30, table_fragments=tables,
                         logical_tables=[LogicalTableInstance(t.fragment_uid, t.table_code, t.title,
                                                              [t.fragment_uid], t.page_number,
                                                              max(c.page_number for c in t.cells),
                                                              confidence=t.confidence)
                                         for t in tables], physical_fragments=list(tables))


@pytest.mark.asyncio
async def test_fallback_candidates_cannot_overwrite_native_p12_or_its_p13_continuation():
    # Original failing shape: one coalesced native FIN04 and single-column
    # directory/table-text/narration candidates sharing its code.
    real = parsed_table([["收入", "", "支出", "", "", "", ""],
                         ["项目", "决算数", "项目", "合计", "一般公共预算财政拨款", "政府性基金预算财政拨款", "国有资本经营预算财政拨款"],
                         ["一般公共预算财政拨款", "233085.87", "一般公共服务", "226550.41", "226550.41", "", ""],
                         ["总计", "234283.60", "总计", "234283.60", "234283.60", "", ""]], FIN04, "native", 12)
    for cell in real.cells[-7:]:
        cell.page_number = 13
        cell.origin_fragment_uid = "native-continuation"
        cell.origin_row_idx = 0
    shadows = [parsed_table([[text], ["财政拨款收入支出决算总表"]], FIN04,
                            f"fallback-{page}", page, method="page_text_fallback", title=text)
               for page, text in [(2, "第二部分 年度部门决算表"), (7, "收入支出决算总表"),
                                  (22, "财政拨款收入支出决算总体情况说明")]]
    for ordered in permutations([real, *shadows]):
        artifact = artifact_for(list(ordered))
        before = artifact.to_dict()
        projection, review = _legacy_artifact_projection(artifact)
        assert projection.table_fragments == [real]
        assert artifact.to_dict() == before
        assert review == []
        conn = AsyncMock()
        await PDFParser(conn).persist_artifact_to_legacy_tables(99, projection)
        inserts = [call.args for call in conn.execute.await_args_list if "INSERT INTO fiscal_table_cells" in call.args[0]]
        assert len(inserts) == len(real.cells)
        assert {args[8] for args in inserts} == {12, 13}
        continuation = next(args for args in inserts if args[3:5] == (3, 1))
        assert (continuation[5], continuation[8]) == ("234283.60", 13)


@pytest.mark.parametrize("same_amount", [False, True])
def test_independent_real_instances_remain_unified_and_require_manual_legacy_review(same_amount):
    first = parsed_table([["项目", "决算数"], ["合计", "100.00"]], FIN04, "first", 5)
    second = parsed_table([["项目", "决算数"], ["合计", "100.00" if same_amount else "200.00"]],
                          FIN04, "second", 9, confidence=.65)
    artifact = artifact_for([first, second])
    before = artifact.to_dict()
    projection, review = _legacy_artifact_projection(artifact)
    assert not projection.table_fragments
    assert artifact.to_dict() == before
    assert len(artifact.logical_tables) == 2
    assert review[0]["reason"] == "multiple_logical_instances"
    assert review[0]["recognition_status"] == "manual_review"
    items = _build_review_items({"legacy_projection_review_items": review}, [], {"facts_count": 0})
    assert any(item["type"] == "legacy_table_projection_unavailable" for item in items)


def test_single_low_confidence_real_table_is_projected_without_promoting_confidence():
    real = parsed_table([["项目", "决算数"], ["合计", "100.00"]], FIN04, "real", 4,
                        confidence=.62, method="ocr")
    projection, review = _legacy_artifact_projection(artifact_for([real]))
    assert projection.table_fragments == [real]
    assert projection.table_fragments[0].confidence == .62
    assert not review


def test_stable_other_tables_do_not_suppress_a_source_bound_low_confidence_table():
    from types import SimpleNamespace
    from src.services.structured_ingest_runner import CORE_TABLES

    real = TableInstance(FIN04, "收入 支出", .62, 12, 0, 20, [],
                         source_table_code=FIN04, native_identity=True)
    instances = [real, *[SimpleNamespace(table_code=code, confidence=.99, page_number=1)
                         for code in CORE_TABLES if code != FIN04]]
    review = _build_review_items({}, instances, {"facts_count": 9, "low_confidence_tables": [FIN04]})
    assert len(review) == 1
    assert review[0]["type"] == "low_confidence_table"
    assert review[0]["confidence"] == .62


def test_conflicted_logical_instance_is_never_silently_projected():
    real = parsed_table([["项目", "决算数"], ["合计", "100.00"]], FIN04, "real", 4)
    artifact = artifact_for([real])
    artifact.logical_tables[0].identity_conflict = True
    projection, review = _legacy_artifact_projection(artifact)
    assert not projection.table_fragments
    assert review[0]["reason"] == "logical_identity_conflict"


def test_legacy_projection_keeps_both_real_instance_fact_identities_in_unified_materialization():
    from src.services.structured_ingest_runner import _materialize_fiscal_facts_from_artifact

    first = parsed_table([["支出决算表", "", "", ""],
                          ["科目名称", "本年支出合计", "基本支出", "项目支出"],
                          ["合计", "100.00", "70.00", "30.00"]], FIN03, "first", 5)
    second = parsed_table([["支出决算表", "", "", ""],
                           ["科目名称", "本年支出合计", "基本支出", "项目支出"],
                           ["合计", "200.00", "150.00", "50.00"]], FIN03, "second", 9,
                          confidence=.65)
    artifact = artifact_for([first, second])
    kwargs = dict(org_name="甲局", fiscal_year=2025, doc_type="dept_final", file_hash="a" * 64)
    before = _materialize_fiscal_facts_from_artifact(artifact, **kwargs)
    projection, review = _legacy_artifact_projection(artifact)
    after = _materialize_fiscal_facts_from_artifact(artifact, **kwargs)
    assert not projection.table_fragments
    assert review[0]["reason"] == "multiple_logical_instances"
    assert len(after) == 6
    assert {fact.instance_uid for fact in after} == {"first", "second"}
    assert len({fact.fact_uid for fact in after}) == 6
    assert [fact.to_dict() for fact in after] == [fact.to_dict() for fact in before]
    assert all(fact.recognition_status == "low_confidence" for fact in after if fact.instance_uid == "second")


@pytest.mark.asyncio
async def test_ingest_projects_only_legacy_write_and_keeps_unified_artifact(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.services import structured_ingest_runner as runner

    real = parsed_table([["项目", "决算数"], ["合计", "100.00"]], FIN04, "real", 12)
    shadow = parsed_table([["财政拨款收入支出决算总表"]], FIN04, "shadow", 22,
                          method="page_text_fallback")
    artifact = artifact_for([real, shadow])
    before = artifact.to_dict()
    consumed = {}
    conn = AsyncMock()

    async def ready():
        return True

    async def acquire():
        return conn

    async def release(_conn):
        pass

    async def document_version(**_kwargs):
        return {"org_unit_id": 1, "document_id": 2, "document_version_id": 3}

    async def allocate(**_kwargs):
        return {"status": "resolved", "bound": True, "slot_id": "slot"}

    async def no_cache(*_args, **_kwargs):
        return None

    async def persist_unified(_conn, source, **_kwargs):
        consumed["unified"] = source
        return 11

    async def load_unified(_conn, _run_id):
        return ParseArtifact.from_dict(artifact.to_dict())

    async def persist_facts(*_args, **_kwargs):
        return 0

    def materialize_unified(source, **_kwargs):
        consumed["unified_fact_source"] = source
        from src.engine.fact_context import FactContext
        return FactContext([],file_hash=source.source_file_hash,parse_run_uid=source.parse_run_uid)

    class Parser:
        def __init__(self, _conn):
            pass

        async def persist_artifact_to_legacy_tables(self, _version, source):
            consumed["legacy"] = source
            return {"success": True, "tables_count": len(source.table_fragments), "unknown_tables": []}

    recognizer = SimpleNamespace(recognize_tables=AsyncMock(return_value=[]),
                                 save_table_instances=AsyncMock())
    materializer = SimpleNamespace(materialize=AsyncMock(return_value={"facts_count": 0}))
    sync = SimpleNamespace(sync=AsyncMock(return_value={"status": "skipped"}))
    monkeypatch.setattr(runner, "ensure_structured_ingest_ready", ready)
    monkeypatch.setattr(runner, "DatabaseConnection", SimpleNamespace(acquire=acquire, release=release))
    monkeypatch.setattr(runner, "_ensure_document_version", document_version)
    monkeypatch.setattr(runner, "_allocate_material_slot", allocate)
    monkeypatch.setattr(runner, "find_cached_parse_run", no_cache)
    monkeypatch.setattr(runner, "persist_parse_artifact", persist_unified)
    monkeypatch.setattr(runner, "load_parse_artifact", load_unified)
    monkeypatch.setattr(runner, "persist_facts_and_evidence", persist_facts)
    monkeypatch.setattr('src.engine.fact_context.build_fact_context_from_artifact', materialize_unified)
    monkeypatch.setattr(runner, 'verify_persisted_facts', AsyncMock(return_value={'verified': True}))
    monkeypatch.setattr(runner, "PDFParser", Parser)
    monkeypatch.setattr(runner, "TableRecognizer", lambda _conn: recognizer)
    monkeypatch.setattr(runner, "FiscalFactMaterializer", lambda _conn: materializer)
    monkeypatch.setattr(runner, "PSSharedSchemaSync", lambda _conn: sync)
    monkeypatch.setattr(runner, "_detect_document_profile", lambda _path: "canonical_nine_table")
    monkeypatch.setattr(runner, "_backfill_local_organization_catalog", lambda **_kwargs: {})
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    artifact.source_file_hash = runner._sha256_file(path)
    before = artifact.to_dict()
    payload = await runner.run_structured_ingest(job_id="projection", pdf_path=path,
                                                metadata={"organization_name": "甲局", "report_year": "2025",
                                                          "doc_type": "dept_final", "checksum": artifact.source_file_hash},
                                                parse_artifact=artifact)
    assert payload["status"] == "done"
    assert consumed["unified"] is artifact
    assert consumed["unified_fact_source"] is artifact
    assert consumed["legacy"].table_fragments == [real]
    assert artifact.to_dict() == before
