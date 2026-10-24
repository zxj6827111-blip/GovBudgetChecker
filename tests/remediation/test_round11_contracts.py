"""Round 10 counterexamples against production rules and evaluation contracts."""

import json

import pytest

from tests.remediation.test_round8_percentage_binding import execute, make_document
from src.engine.rules_v33 import _bound_narrative_amount, _resolve_indicator_percentage
from src.services.truth_grouping import canonical_truth_group
from scripts.evaluate_golden_corpus import _truth_group
from scripts.run_zc07_acceptance import _evaluate_exact_location, validate_dataset_manifest


@pytest.mark.parametrize("text", ["20.82", "支出20.82", "2024.50万元", "1,2024.00", "财政拨款307.82"])
def test_decimal_amounts_do_not_become_report_years(text):
    from src.utils.report_year import parse_report_year

    assert parse_report_year(text) is None
    assert parse_report_year("2024年度部门决算\n" + text) == 2024
    assert parse_report_year("office-24 final.pdf") == 2024


def test_atomic_json_preserves_decimal_and_rejects_invalid_value(tmp_path):
    from decimal import Decimal
    from api import runtime

    path = tmp_path / "status.json"
    runtime.write_json_file(path, {"value": Decimal("100.0100")})
    assert json.loads(path.read_text())["value"] == "100.0100"
    original = path.read_bytes()
    with pytest.raises(TypeError):
        runtime.write_json_file(path, {"value": Decimal("NaN")})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("name", ["第二实验中学", "某某科研院所", "城市发展研究院"])
@pytest.mark.parametrize("template", ["{name}2025年", "2025年{name}", "{name}"])
def test_unknown_subject_does_not_use_year_as_identity(name, template):
    prefix = template.format(name=name)
    _, relations, issues = execute("income", prefix + "财政拨款收入50万元，占50%。", fiscal=1000)
    receipt = relations["T2:fiscal_allocation:share_of_income_total"]
    assert receipt["status"] == "insufficient_data"
    assert prefix in receipt["incomplete_reason"]
    assert not issues


@pytest.mark.parametrize("prefix", ["第二实验中学2025年", "2025年第二实验中学"])
def test_unknown_year_subject_does_not_hide_current_error(prefix):
    _, relations, issues = execute(
        "income",
        "本单位财政拨款收入1000万元，占99.89%，" + prefix + "财政拨款收入50万元，占50%。",
        fiscal=1000,
    )
    assert relations["T2:fiscal_allocation:share_of_income_total"]["status"] == "fail"
    assert len(issues) == 1
    assert "99.89%" in issues[0]["evidence"][0]["text"]
    assert prefix not in issues[0]["evidence"][0]["text"]


@pytest.mark.parametrize(
    "kind,body",
    [
        ("income", "2024年本单位上级补助收入0万元，事业收入0万元，财政拨款收入800万元，占80%。"),
        (
            "expense",
            "2024年本单位人员经费30万元，公用经费20万元，基本支出50万元，占50%，项目支出50万元，占50%。",
        ),
    ],
)
def test_past_year_inherits_across_amount_clauses(kind, body):
    _, relations, issues = execute(kind, body, fiscal=1000)
    assert all(r["status"] == "insufficient_data" for r in relations.values())
    assert all("2024年" in r["incomplete_reason"] for r in relations.values())
    assert not issues
    doc, *_ = make_document(kind, body, fiscal=1000)
    for metric in ("财政拨款收入",) if kind == "income" else ("基本支出", "项目支出"):
        assert _bound_narrative_amount(doc, body, (metric,)) is None


@pytest.mark.parametrize("reset", ["2025年本单位", "本年本单位", "本单位本年"])
def test_explicit_current_year_resets_inherited_year(reset):
    body = (
        "2024年本单位上级补助收入0万元，事业收入0万元，" + reset + "财政拨款收入1000万元，占100%。"
    )
    _, relations, issues = execute("income", body, fiscal=1000)
    assert relations["T2:fiscal_allocation:share_of_income_total"]["status"] == "pass"
    assert not issues


def test_budget_stage_requires_explicit_reset():
    context = dict(organization_name="测试局", report_year=2025, report_kind="final")
    value = _resolve_indicator_percentage(
        "年初预算本单位上级补助收入0万元，财政拨款收入800万元，占80%。",
        ("财政拨款收入",),
        **context,
    )
    assert value.status == "ambiguous"
    assert "年初预算" in value.reason
    value = _resolve_indicator_percentage(
        "年初预算本单位上级补助收入0万元，决算数本单位财政拨款收入1000万元，占100%。",
        ("财政拨款收入",),
        **context,
    )
    assert value.status == "ok" and value.value == 100


@pytest.mark.parametrize("number", ["8,000.00", "8，000.00", "8,000,000.00"])
def test_grouped_amount_is_not_a_scope_boundary(number):
    value = float(number.replace(",", "").replace("，", ""))
    body = f"本单位财政拨款收入{number}万元，占100%。"
    doc, *_ = make_document("income", body, fiscal=value)
    assert _bound_narrative_amount(doc, body, ("财政拨款收入",)) == value
    past = f"2024年本单位财政拨款收入{number}万元，占100%。"
    assert _bound_narrative_amount(doc, past, ("财政拨款收入",)) is None


@pytest.mark.parametrize(
    "label,scope", [("编制部门", "department"), ("编制单位", "unit"), ("发布单位", None)]
)
def test_explicit_publisher_label_does_not_guess_report_kind(label, scope):
    from src.services.document_profile_resolver import resolve_document_profile

    profile = resolve_document_profile(page_texts=[f"2025年测试局部门决算\n{label}：测试局"])
    assert profile.organization_name.value == "测试局"
    assert profile.kind == "final"
    assert profile.subject_level.value == (scope or "department")
    label_only = resolve_document_profile(page_texts=[f"{label}：测试局"])
    assert label_only.kind == "unknown"
    assert label_only.subject_level.value == scope
    conflicting = resolve_document_profile(page_texts=["编制部门：测试局\n发布单位：其他局"])
    assert conflicting.organization_name.value is None
    assert conflicting.kind == "unknown"


def test_truth_groups_share_exact_identity_and_narrow_legacy_contract(tmp_path):
    ids = ["DOC-001-income", "DOC-001-expense"]
    assert len({_truth_group({"truth_id": name}) for name in ids}) == 2
    assert (
        canonical_truth_group({"truth_id": "T4a"})
        == canonical_truth_group({"truth_id": "T4b"})
        == "T4"
    )
    assert canonical_truth_group({"truth_id": "T4income"}) == "T4income"
    assert canonical_truth_group({"truth_id": "T4a", "truth_group_id": "T4a"}) == "T4a"
    corpus = tmp_path / "corpus" / "DOC-001"
    corpus.mkdir(parents=True)
    (corpus / "golden.json").write_text(
        json.dumps({"labels": [{"label": "defect", "truth_id": name} for name in ids]})
    )
    manifest = tmp_path / "dataset.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "zc07.dataset.v1",
                "documents": [
                    {
                        "doc_id": "DOC-001",
                        "split": "blind",
                        "document_type": "final",
                        "organization_type": "unit",
                        "source_type": "native",
                    }
                ],
            }
        )
    )
    result = validate_dataset_manifest(manifest, tmp_path)
    assert result["defect_groups_by_split"]["blind"] == 2


def test_exact_location_denominator_counts_only_defect_regions(tmp_path):
    labels = [
        {
            "annotation_id": kind,
            "truth_id": kind,
            "label": kind,
            "truth_region": {"page": 1, "bbox": [1, 1, 2, 2]},
        }
        for kind in ["defect", "acceptable", "manual_review", "rounding_hint"]
    ]
    result = _evaluate_exact_location(
        [{"golden": {"labels": labels}, "report": {"matched": []}}], tmp_path
    )
    assert result["truth_total"] == 1


@pytest.mark.parametrize(
    "kind,metric,value,pct,expected",
    [
        ("income", "fiscal_allocation", 800, 80, "pass"),
        ("income", "fiscal_allocation", 800, 79.89, "fail"),
        ("expense", "basic_actual", 500, 50, "pass"),
        ("expense", "project_actual", 500, 49.89, "fail"),
    ],
)
def test_historical_claim_uses_explicit_same_year_owner_facts(kind, metric, value, pct, expected):
    from dataclasses import replace
    from decimal import Decimal
    from src.engine.fact_context import FactContext
    from src.engine.rules_v33 import RuleDeferred

    label = {
        "fiscal_allocation": "财政拨款收入",
        "basic_actual": "基本支出",
        "project_actual": "项目支出",
    }[metric]
    body = f"2024年本单位上级补助收入0万元，事业收入0万元，{label}{value}万元，占{pct}%。"
    doc, rule, *_ = make_document(kind, body, fiscal=1000)
    owner = doc.document_profile.organization_name.value
    current = [
        replace(fact, fiscal_year=2025, org_name=owner, budget_stage="决算")
        for fact in doc.fact_context.facts
    ]
    historical = [
        replace(
            fact,
            fact_uid="past-" + fact.fact_uid,
            fiscal_year=2024,
            normalized_value=Decimal(1000 if fact.indicator == "total_actual" else value),
            raw_value=str(1000 if fact.indicator == "total_actual" else value),
        )
        for fact in current
    ]
    doc.fact_context = FactContext(
        current + historical, org_name=owner, fiscal_year=2025, report_kind="final"
    )
    try:
        issues = rule.apply(doc)
    except RuleDeferred as exc:
        issues = exc.partial_issues
    receipt = next(
        r for r in doc.fact_receipts if r["sub_check_id"] == f"historical:2024:{metric}:share"
    )
    assert receipt["status"] == expected
    assert all(uid.startswith("past-") for uid in receipt["operand_fact_ids"])
    if expected == "fail":
        issue = next(
            i for i in issues if i.check_relation_id.endswith(f"historical:2024:{metric}:share")
        )
        assert "2024年" in issue.evidence_text


def test_sequential_corrections_preserve_prior_context_and_use_latest_value():
    import copy
    from decimal import Decimal
    from src.engine.fact_context import FactContext, FactOverlay, apply_fact_overlay
    from src.schemas.parse_artifact import FiscalFact

    context = FactContext(
        [
            FiscalFact(
                fact_uid="f",
                indicator="total_actual",
                raw_value="100",
                unit="万元",
                normalized_value=Decimal(100),
                recognition_status="unverified",
                extra_dims={
                    "pre_correction_values": [{"raw_value": "90"}],
                    "evidence_refs": [{"verification_status": "unverified"}],
                },
            )
        ]
    )
    original = copy.deepcopy(context.to_dict())
    changed = apply_fact_overlay(
        context, [FactOverlay("f", raw_value="200"), FactOverlay("f", unit="元")]
    )
    assert context.to_dict() == original
    assert changed.facts[0].raw_value == "200" and changed.facts[0].normalized_value == Decimal(
        ".02"
    )
    assert context.version_token != changed.version_token
    assert changed.facts[0].extra_dims["evidence_refs"][0]["verification_status"] == "unverified"
    newer = apply_fact_overlay(changed, [FactOverlay("f", raw_value="300")])
    assert changed.facts[0].raw_value == "200"
    assert len(changed.facts[0].extra_dims["pre_correction_values"]) == 3
    assert len(newer.facts[0].extra_dims["pre_correction_values"]) == 4
