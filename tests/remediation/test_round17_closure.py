import copy
import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import runtime
from scripts.backup_all import BackupError, restore_uploads, UPLOADS_ARTIFACT
from src.engine.fact_context import (
    FactOverlay,
    apply_fact_overlay,
    build_fact_context_from_artifact,
)
from src.engine.fact_table_projection import fact_bound_table_rows, ProjectionUnavailable
from src.engine.rules_v33 import R33120_DetailTableCheck, build_document
from src.services.expected_material_import import COLUMNS, preview_csv, import_csv
from src.services import issue_workflow_store as workflow
from src.services.pdf_parser import PDFParser
from tests.remediation.test_rc_batch1 import _make_fragment, _artifact_with

RECORDS = [
    {"id": "d", "name": "测试区", "level": "district"},
    {"id": "p", "name": "同名局", "level": "department", "parent_id": "d"},
    {"id": "u", "name": "同名局", "level": "unit", "parent_id": "p"},
]


def csv_text(*rows):
    return ",".join(COLUMNS) + "\n" + "\n".join(rows)


GOOD = (
    "u,unit,2024,final,unit_self,self,2099-10-31T23:59:59+08:00,https://example.gov.cn/source.pdf"
)


def test_expected_csv_identity_dates_permissions_and_duplicate_errors():
    good = preview_csv(csv_text(GOOD), RECORDS, ["u"])
    assert good["valid"] and good["rows"][0]["due_at"] == "2099-10-31T15:59:59+00:00"
    for bad in (
        GOOD.replace("unit,", "department,", 1),
        GOOD.replace("2024", "2024.0"),
        GOOD.replace("+08:00", ""),
        GOOD.replace("final", "unknown"),
        GOOD.replace("https://example.gov.cn", "javascript:alert(1)"),
    ):
        assert not preview_csv(csv_text(bad), RECORDS, None)["valid"]
    assert not preview_csv(csv_text(GOOD, GOOD), RECORDS, None)["valid"]
    with pytest.raises(HTTPException) as exc:
        preview_csv(csv_text(GOOD), RECORDS, ["p"])
    assert exc.value.status_code == 403
    assert not preview_csv(csv_text(GOOD.replace("u,", "missing,", 1)), RECORDS, None)["valid"]


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    p = tmp_path / "j"
    p.mkdir()
    issue = {
        "id": "i",
        "title": "金额不同",
        "severity": "high",
        "page": 1,
        "fact_ids": ["f"],
        "evidence_ids": ["e"],
        "check_relation_id": "r",
    }
    raw = {
        "job_id": "j",
        "analysis_revision": "rev1",
        "status": "done",
        "result": {"issues": {"all": [issue]}},
    }
    runtime.write_json_file(p / "status.json", raw)
    return p, raw


def test_ignore_single_authority_revision_reuse_restore_and_audit(job):
    p, raw = job
    workflow.add_ignored_issue_id(
        "j", "i", user={"is_admin": True, "username": "real-reviewer"}, note="已核对原件"
    )
    assert not (p / "ignored_issues.json").exists()
    assert not runtime.get_job_status_payload("j")["result"]["issues"]["all"]
    state = workflow._read_state()
    assert state["issues"]["j::i"]["decision_actor"] == "real-reviewer"
    raw["analysis_revision"] = "rev2"
    runtime.write_json_file(p / "status.json", raw)
    assert runtime.get_job_status_payload("j")["result"]["issues"]["all"]
    assert workflow.get_job_problem_snapshot("j")["decisions"] == {}
    # The old report still projects its own decision.
    old = copy.deepcopy(raw)
    old["analysis_revision"] = "rev1"
    assert not runtime.apply_job_issue_filters(p, old)["result"]["issues"]["all"]


def test_bare_legacy_ids_are_quarantined_once_and_cannot_complete(job):
    p, _ = job
    source = b'{"issue_ids":["i"]}'
    (p / "ignored_issues.json").write_bytes(source)
    assert workflow.get_job_problem_snapshot("j")["ignored_issue_ids"] == []
    receipts = workflow._read_state()["legacy_ignore_migrations"]
    assert len(receipts) == 1
    assert next(iter(receipts.values()))["outcomes"][0]["reason"] == "analysis_identity_missing"
    assert next(p.glob("ignored_issues.migrated.*.json")).read_bytes() == source
    workflow.get_job_problem_snapshot("j")
    assert workflow._read_state()["legacy_ignore_migrations"] == receipts


def test_bound_legacy_ignore_migrates_then_completion_lock_rejects_mutation(job):
    p, _ = job
    runtime.write_json_file(
        p / "ignored_issues.json", {"issue_ids": ["i"], "analysis_revision": "rev1"}
    )
    snapshot = workflow.get_job_problem_snapshot("j")
    assert snapshot["ignored_issue_ids"] == ["i"] and snapshot["decisions"] == {"i": "no_issue"}
    workflow.set_review_lock(
        "j", slot_id="s", review_session_id="r", analysis_basis_token="b", completed_by="reviewer"
    )
    with pytest.raises(HTTPException) as exc:
        workflow.add_ignored_issue_id("j", "i")
    assert exc.value.status_code == 409


def test_detail_rule_recomputes_current_facts_and_rejects_missing_mapping():
    rows = [
        ["科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["", "合计", "100", "100", "0"],
        ["201", "一般公共服务", "90", "90", "0"],
    ]
    fragment = _make_fragment(
        "FIN_03_expenditure", rows, title="测试局支出决算表", unit_hint="万元", page=2
    )
    instance = PDFParser(None)._build_logical_instance(fragment)
    artifact = _artifact_with([fragment], [instance])
    context = build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2024, report_kind="final"
    )
    doc = build_document(
        "测试局2024年度决算.pdf",
        ["测试局2024年部门决算", "支出决算表\n单位：万元"],
        [[], [rows]],
        100,
    )
    doc.fact_context, doc.parse_artifact = context, artifact
    assert R33120_DetailTableCheck().apply(doc)
    changed = [
        fact
        for fact in context.facts
        if fact.subject_code == "201" and fact.indicator in ("total_actual", "basic_actual")
    ]
    assert len(changed) == 2
    for fact in changed:
        context = apply_fact_overlay(
            context, [FactOverlay(fact_uid=fact.fact_uid, raw_value="100")]
        )
    doc.fact_context = context
    assert not R33120_DetailTableCheck().apply(doc)
    assert artifact.table_fragments[0].rows[2][2] == "90"
    assert doc.structured_consumption[-1]["fact_version"] == context.version_token
    doc.fact_context = copy.deepcopy(context)
    doc.fact_context.facts = doc.fact_context.facts[1:]
    with pytest.raises(ProjectionUnavailable):
        fact_bound_table_rows(doc, "支出决算表")


@pytest.mark.parametrize(
    "name,kind", [("../restored-escape/x", "file"), ("link", "symlink"), ("link", "hardlink")]
)
def test_backup_restore_rejects_prefix_escape_and_links_before_writes(tmp_path, name, kind):
    archive = tmp_path / "backup"
    archive.mkdir()
    with tarfile.open(archive / UPLOADS_ARTIFACT, "w:gz") as tar:
        item = tarfile.TarInfo(name)
        if kind == "file":
            item.size = 1
            tar.addfile(item, io.BytesIO(b"x"))
        else:
            item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            item.linkname = "../restored-escape/x"
            tar.addfile(item)
    with pytest.raises(BackupError):
        restore_uploads(archive, tmp_path / "restored")
    assert not list((tmp_path / "restored").rglob("*"))


@pytest.mark.parametrize(
    "row,expected",
    [
        (["**", "…", "1", "2", "3", "4", "5"], True),
        (["栏次", "1", "2"], True),
        (["合计", "1", "2", "3"], False),
        (["", "1", "2", "3"], False),
        (["*", "1", "3", "4"], False),
    ],
)
def test_structural_column_indices_do_not_capture_real_integer_amounts(row, expected):
    from src.services.fiscal_row_roles import is_index_header

    assert is_index_header(row) is expected


def test_per_cell_qualification_keeps_strong_amount_and_rejects_weak_or_unbound():
    import json
    from tests.remediation.test_round11_ocr import fixture
    from ocr_service.protocol import map_table_cells
    from src.services.ocr_qualification import qualification
    from src.services.ocr_fact_promotion import promote_ocr_candidates

    artifact, candidate = fixture()
    raw = candidate.provenance["raw_prediction"]
    raw["table_res_list"][0]["table_ocr_pred"]["rec_scores"][-1] = 0.94
    remapped = map_table_cells(raw["table_res_list"][0])
    for new, old in zip(remapped["cells"], candidate.cells, strict=True):
        for field in ("score", "fragments"):
            old[field] = new[field]
    candidate.provenance["raw_prediction_sha256"] = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    transform = artifact.coordinate_transforms[candidate.coordinate_transform_id]
    transform["ocr_response"] = copy.deepcopy(candidate.provenance)
    transform["page_request"]["raw_prediction_sha256"] = candidate.provenance[
        "raw_prediction_sha256"
    ]
    kwargs = dict(organization="测试局", year=2025, report_kind="final")
    assert qualification(candidate, artifact, **kwargs)["status"] == "rejected"
    assert (
        qualification(candidate, artifact, cell_position=(1, 2), **kwargs)["status"] == "qualified"
    )
    assert (
        qualification(candidate, artifact, cell_position=(1, 3), **kwargs)["status"] == "rejected"
    )
    promote_ocr_candidates(artifact, **kwargs)
    context = build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert len(context.definite_facts()) == 1
    # Per-cell mode still requires the original physical page/request binding.
    candidate.page_number = 2
    assert (
        qualification(candidate, artifact, cell_position=(1, 2), **kwargs)["status"] == "rejected"
    )


def test_application_state_backup_restores_actual_authority_bytes(tmp_path, monkeypatch):
    from scripts.backup_all import create_backup, verify_backup, restore_application_state

    state = tmp_path / "state"
    state.mkdir()
    originals = {
        "organizations.json": b'{"id":"real-source-id"}',
        "users.json": b'{"password_hash":"fixture-only"}',
    }
    for name, value in originals.items():
        (state / name).write_bytes(value)
    monkeypatch.setenv("ORG_DATA_DIR", str(state))
    monkeypatch.setenv("USER_DATA_DIR", str(state))
    monkeypatch.delenv("USER_FILE", raising=False)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    archive = tmp_path / "backup"
    create_backup(
        archive,
        uploads_dir=uploads,
        database_url="",
        audit_log_path=tmp_path / "absent-audit",
        include_database=False,
    )
    assert verify_backup(archive)["ok"]
    restored = tmp_path / "restored"
    assert restore_application_state(archive, restored)["verified"]
    assert {name: (restored / name).read_bytes() for name in originals} == originals
    with pytest.raises(BackupError):
        restore_application_state(archive, restored)


def _add_catalog_record_and_link(org_id):
    from src.services.org_storage import OrganizationStorage
    from src.schemas.organization import Organization

    store = OrganizationStorage()
    store.add(Organization(id=org_id, name="并发演练单位" + org_id, level="unit", parent_id="dept-concurrency"))
    store.link_job("job-" + org_id, org_id)


def test_api_worker_catalog_refresh_and_concurrent_writers(tmp_path, monkeypatch):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from src.services import org_storage as catalog
    from src.schemas.organization import Organization

    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog, "ORG_FILE", tmp_path / "organizations.json")
    monkeypatch.setattr(catalog, "LINKS_FILE", tmp_path / "job_org_links.json")
    api, worker = catalog.OrganizationStorage(), catalog.OrganizationStorage()
    # 目录校验要求 unit 必须挂在 department 下，先落一个共用上级。
    api.add(Organization(id="dept-concurrency", name="并发演练部门", level="department"))
    api.add(Organization(id="initial", name="目录演练单位", level="unit", parent_id="dept-concurrency"))
    assert worker.get_by_id("initial").name == "目录演练单位"
    api.link_job("initial-job", "initial")
    assert worker.get_job_org("initial-job").org_id == "initial"
    api.delete("initial")
    assert worker.get_by_id("initial") is None and worker.get_job_org("initial-job") is None
    if "fork" not in multiprocessing.get_all_start_methods():
        return  # refresh assertion still runs; process concurrency exercised on POSIX
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("fork")
    ) as executor:
        list(executor.map(_add_catalog_record_and_link, ["u" + str(n) for n in range(8)]))
    assert len(worker.get_all()) == 9  # 1 个共用部门 + 8 个并发写入的单位
    assert all(worker.get_job_org("job-u" + str(n)).org_id == "u" + str(n) for n in range(8))


@pytest.mark.parametrize(
    "text,indicator,denominator,value",
    [
        (
            "本年收入合计 2,418,329.38 万元，其中：财政拨款收入2,406,656.62 万元，占 99.52%",
            "财政拨款收入",
            "本年收入",
            "99.52",
        ),
        (
            "本年支出合计 2,443,579.41 万元，其中：基本支出 122,048.88万元，占本年支出 4.99%",
            "基本支出",
            "本年支出",
            "4.99",
        ),
        (
            "本年支出合计 2，443，579.41 万元，其中：项目支出2，321，530.53万元，占本年支出95.01%",
            "项目支出",
            "本年支出",
            "95.01",
        ),
    ],
)
def test_real_shanghai_thousands_separators_keep_ratio_owner_and_total(
    text, indicator, denominator, value
):
    from decimal import Decimal
    from src.engine.rules_v33 import _resolve_indicator_percentage

    result = _resolve_indicator_percentage(
        text,
        (indicator,),
        (denominator,),
        organization_name="上海市交通委员会",
        report_year=2025,
        report_kind="final",
    )
    assert result.status == "ok" and result.value == Decimal(value)
    assert result.quotes[0] in text
    assert all(subject not in {"418", "443", "321"} for subject in result.subjects)


@pytest.mark.parametrize(
    "text",
    [
        "上海市统计局本年收入合计2,418,329.38万元，其中财政拨款收入2,406,656.62万元，占99.52%",
        "2024年本年收入合计2,418,329.38万元，其中财政拨款收入2,406,656.62万元，占99.52%",
        "实验甲中学本年收入合计2,418,329.38万元，其中财政拨款收入2,406,656.62万元，占99.52%",
        "财政拨款收入2,406,656.62万元，占一般公共预算本年收入99.52%",
    ],
)
def test_separator_fix_keeps_foreign_year_unknown_owner_and_fund_guards(text):
    from src.engine.rules_v33 import _resolve_indicator_percentage

    result = _resolve_indicator_percentage(
        text,
        ("财政拨款收入",),
        ("本年收入",),
        organization_name="上海市交通委员会",
        report_year=2025,
        report_kind="final",
    )
    assert result.status == "ambiguous"


def test_real_shanghai_bare_expense_denominator_reaches_production_rule():
    from tests.remediation.test_round8_percentage_binding import execute

    _, relations, issues = execute(
        "expense",
        "本年支出合计100万元，其中：基本支出60万元，占本年支出60%，项目支出40万元，占本年支出40%。",
    )
    assert relations["T3:basic_share_of_expense_total"]["status"] == "pass"
    assert relations["T3:project_share_of_expense_total"]["status"] == "pass"
    assert not issues


def test_real_reception_counts_after_amount_paragraph_are_not_missing():
    from src.engine.rules_v33 import R33246_DomesticReceptionDisclosure

    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "3、公务接待费支出34.15万元。其中：\n"
        "国内公务接待支出34.15万（含外宾接待支出1.08万元）。\n"
        "主要用于接待开支的餐费、住宿费、交通费等支出，2025年共\n"
        "接待734批次，8515人次，其中接待外宾7批次，79人次。\n"
        "八、政府性基金预算财政拨款收入支出决算情况说明"
    )
    doc = build_document("交通委员会2025年决算.pdf", [text], [[]], 100)
    assert R33246_DomesticReceptionDisclosure().apply(doc) == []


@pytest.mark.parametrize(
    "counts",
    ["接待外宾7批次，79人次。", "接待外宾7批次。79人次。", "接待734批次。", "接待8515人次。"],
)
def test_reception_scope_keeps_foreign_only_and_partial_counts_incomplete(counts):
    from src.engine.rules_v33 import R33246_DomesticReceptionDisclosure

    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "3、公务接待费支出34.15万元。\n国内公务接待支出34.15万元。\n"
        + counts
        + "\n八、政府性基金预算财政拨款收入支出决算情况说明"
    )
    doc = build_document("测试局2025年决算.pdf", [text], [[]], 100)
    assert R33246_DomesticReceptionDisclosure().apply(doc)


@pytest.mark.parametrize(
    "counts",
    [
        "公务接待批次626次，人次2,839次。",
        "全年共接待492个批次、4364人次。",
        "接待批次1次、接待15人次、陪同5人。",
    ],
)
def test_real_reception_count_word_orders(counts):
    from src.engine.rules_v33 import R33246_DomesticReceptionDisclosure

    text = (
        "七、财政拨款“三公”经费支出决算情况说明\n"
        "3、公务接待费支出34.15万元。\n国内公务接待支出34.15万元（含外宾接待支出1.08万元）。\n"
        + counts
        + "\n八、政府性基金预算财政拨款收入支出决算情况说明"
    )
    assert (
        R33246_DomesticReceptionDisclosure().apply(
            build_document("测试局2025年决算.pdf", [text], [[]], 100)
        )
        == []
    )


@pytest.mark.parametrize("narrative_total,expect_issue", [("120.00", False), ("130.00", True)])
def test_total_narrative_uses_grand_total_and_excludes_fiscal_section(
    narrative_total, expect_issue
):
    from src.engine.rules_v33 import R33102_TotalSheet_vs_Text

    table = [
        ["收入项目", "决算数", "支出项目", "决算数"],
        ["本年收入合计", "90", "本年支出合计", "100"],
        ["总计", "120", "总计", "120"],
    ]
    text = (
        "测试局2025年度部门决算\n收入支出决算总表\n单位：万元\n"
        "一、收入支出决算总体情况说明\n测试局2025年度收入支出总计" + narrative_total + "万元。\n"
        "二、收入决算情况说明\n本年收入90万元。\n"
        "四、财政拨款收入支出决算总体情况说明\n财政拨款收入支出总计110万元。\n"
        "五、一般公共预算财政拨款支出决算情况说明"
    )
    doc = build_document("测试局2025年决算.pdf", [text], [[table]], 100)
    issues = R33102_TotalSheet_vs_Text().apply(doc)
    assert bool(issues) == expect_issue


@pytest.mark.parametrize("other_income,expect_error", [("0.02", False), ("10.00", True)])
def test_income_horizontal_rounding_is_not_formal_error(other_income, expect_error):
    from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck

    rows = [
        ["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
        ["类", "", "", "", ""],
        ["201", "一般公共服务支出", "100", "100", other_income],
    ]
    doc = build_document("测试局2025年决算.pdf", ["收入决算表\n单位：万元"], [[rows]], 100)
    issues = R33240_Table2_IncomeAdvancedCheck().apply(doc)
    assert any(i.severity == "error" for i in issues) == expect_error


def test_column_width_drift_does_not_promote_project_column_as_total():
    rows = [
        ["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["201", "一般公共服务支出", "100", "60", "40"],
        ["合计", "100", "60"],
    ]
    fragment = _make_fragment(
        "FIN_05_general_public_expenditure",
        rows,
        title="测试局一般公共预算财政拨款支出决算表",
        unit_hint="万元",
        page=2,
    )
    instance = PDFParser(None)._build_logical_instance(fragment)
    artifact = _artifact_with([fragment], [instance])
    context = build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert context.facts and all(f.recognition_status == "low_confidence" for f in context.facts)
    assert all(
        f.extra_dims["column_identity_status"] == "cross_page_width_drift" for f in context.facts
    )
    doc = build_document("测试局2025年决算.pdf", ["一般公共预算财政拨款支出决算表"], [[rows]], 100)
    doc.fact_context, doc.parse_artifact = context, artifact
    with pytest.raises(ProjectionUnavailable, match="列宽/列身份变化"):
        fact_bound_table_rows(doc, "一般公共预算财政拨款支出决算表")


def test_general_narrative_excludes_directory_and_other_sections():
    from src.engine.rules_v33 import R33222_Narrative5_T5

    rows = [
        ["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["合计", "", "100", "60", "40"],
    ]
    pages = [
        "一般公共预算财政拨款支出决算表\n单位：万元",
        "目 录\n五、一般公共预算财政拨款支出决算情况说明\n一般公共预算财政拨款支出0.60万元。",
        "五、一般公共预算财政拨款支出决算情况说明\n一般公共预算财政拨款支出100万元。\n六、一般公共预算财政拨款基本支出决算情况说明\n基本支出60万元。",
        "十一、其他重要事项情况说明\n一般公共预算财政拨款支出0.60万元。",
    ]
    doc = build_document("测试局2025年决算.pdf", pages, [[rows], [], [], []], 100)
    assert not R33222_Narrative5_T5().apply(doc)


def test_final_income_and_expense_are_not_code_mirrors():
    from src.engine.common_rules import CMM004_CodeMirrorConsistency
    from src.engine.rule_outcome import RuleNotApplicable

    doc = build_document(
        "测试局2025年决算.pdf",
        ["收入决算表\n201 服务100\n208 社保200", "支出决算表\n201 服务90\n208 社保250"],
        [[], []],
        100,
    )
    with pytest.raises(RuleNotApplicable, match="结转结余"):
        CMM004_CodeMirrorConsistency().apply(doc)


def test_narrative_amount_pair_cannot_cross_next_chapter():
    from src.engine.rules_v33 import R33235_NarrativeAmountConsistency

    text = (
        "七、财政拨款三公经费支出决算情况说明\n3、公务接待费支出39.76万元。\n"
        "国内公务接待支出39.76万元。\n八、政府性基金预算财政拨款收入支出决算情况说明\n"
        "年初预算为0万元，支出决算为420万元。"
    )
    assert not R33235_NarrativeAmountConsistency().apply(
        build_document("测试局2025年决算.pdf", [text], [[]], 100)
    )
