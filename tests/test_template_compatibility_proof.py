"""兼容样本 n 与自动确定 m 分开；失效依据仅对新分析降级，不改历史。"""
from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4
import zipfile

import pytest
from fastapi import HTTPException
from src.services import annual_templates as service
from src.engine.checklist_identity_rules import CMM027_TemplatePositionScope
from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable
from tests.test_material_writing_rules import document


CAPS = {"writing_auto": True, "position_checks": True, "structure": True, "residue": True}


def package():
    content = {"config": {"anchors": [], "prompts": []}}
    return {"id": str(uuid4()), "content": content, "content_sha256": service.sha(content),
            "source_report_year": 2024, "report_kind": "final", "capability_scope": deepcopy(CAPS),
            "unit_source_id": str(uuid4()), "department_source_id": str(uuid4())}


def proof(pkg, **changes):
    return {"report_reference": "synthetic-independent-report", "independent_labels": True,
            "boundary_cases_passed": True, "independent_sample_count": 100,
            "automatic_verified_count": 40, "automatic_errors": 0, "corpus_size": 200,
            "source_report_year": 2024, "target_year": 2025, "capability_scope": deepcopy(CAPS),
            "algorithm": service.ALGORITHM, "package_hash": pkg["content_sha256"], **changes}


def validate(p, pkg, caps=None):
    return service.validate_compatibility(p, pkg, target_year=2025, capabilities=caps or CAPS)


@pytest.mark.parametrize("n,m,corpus,all_checked", [(100, 40, 200, False), (100, 1, 100, True), (12, 4, 12, True)])
def test_independent_sample_gate_never_becomes_automatic_coverage_gate(n, m, corpus, all_checked):
    pkg = package()
    validate(proof(pkg, independent_sample_count=n, automatic_verified_count=m,
                   corpus_size=corpus, all_corpus_verified=all_checked), pkg)


def test_position_only_can_have_no_automatic_determinations():
    pkg = package()
    validate(proof(pkg, automatic_verified_count=0), pkg, {"position_checks": True})
    with pytest.raises(HTTPException):
        validate(proof(pkg, automatic_verified_count=0), pkg)


@pytest.mark.parametrize("changes", [
    {"independent_sample_count": 99}, {"independent_sample_count": True},
    {"independent_sample_count": 0}, {"independent_sample_count": 12, "corpus_size": 12},
    {"independent_sample_count": 12, "corpus_size": 12, "all_corpus_verified": "true"},
    {"corpus_size": 12}, {"corpus_size": False}, {"corpus_size": -1},
    {"all_corpus_verified": True, "corpus_size": 200}, {"all_corpus_verified": True, "corpus_size": None},
    {"automatic_verified_count": 101}, {"automatic_verified_count": -1},
    {"automatic_verified_count": True}, {"automatic_verified_count": 1.0},
    {"automatic_errors": 1}, {"automatic_errors": -1}, {"automatic_errors": False},
    {"automatic_errors": 0.0}, {"independent_labels": "true"}, {"boundary_cases_passed": "true"},
    {"algorithm": "material-writing-v3.5"}, {"target_year": 2024},
    {"source_report_year": 2023}, {"package_hash": "another-package"},
    {"capability_scope": {"position_checks": True}}, {"capability_scope": {"writing_auto": "true"}},
])
def test_invalid_or_inconsistent_evidence_cannot_enable_capabilities(changes):
    pkg = package()
    with pytest.raises(HTTPException):
        validate(proof(pkg, **changes), pkg)


def test_legacy_proof_is_not_silently_relabelled_as_independent_samples():
    pkg = package()
    old = proof(pkg, automatic_verified_count=100)
    old.pop("independent_sample_count")
    before = deepcopy(old)
    with pytest.raises(HTTPException):
        validate(old, pkg)
    assert old == before


def record(pkg, p, adoption="not_received", **basis_extra):
    return {"region": "上海市", "overlay": "", "report_kind": "final", "target_year": 2025,
            "revision": 1, "package_id": pkg["id"], "adoption_kind": adoption,
            "basis": service.canonical({"basis": "synthetic only", "compatibility": p, **basis_extra}),
            **{k: deepcopy(v) for k, v in pkg.items() if k != "id"}}


@pytest.fixture
def qualified_sources(monkeypatch):
    monkeypatch.setattr(service, "verify_source_receipts", lambda content: None)
    monkeypatch.setattr(service, "text", AsyncMock(return_value="synthetic source"))


@pytest.mark.asyncio
async def test_new_database_and_frozen_reads_mask_only_unqualified_capabilities(qualified_sources):
    from src.schemas.document_profile import DocumentProfile, ProfileField
    pkg = package()
    old = proof(pkg)
    old.pop("independent_sample_count")
    value = record(pkg, old)
    before = deepcopy(value)
    profile = DocumentProfile(report_kind=ProfileField(value="final"), report_year=ProfileField(value=2025))
    conn = type("ReadConnection", (), {"fetchrow": AsyncMock(return_value=deepcopy(value))})()
    for snap in [await service.validated_snapshot(None, value),
                 await service.snapshot(conn, 2025),
                 await service.select_frozen(None, [value], profile, ["上海市示例局2025年度决算"])]:
        assert snap["available"]
        assert snap["capability_scope"] == {**CAPS, "writing_auto": False, "position_checks": False}
        assert snap["approved_capability_scope"] == CAPS
        assert set(snap["compatibility_pending"]) == {"writing_auto", "position_checks"}
        assert snap["basis"] == value["basis"]
    assert value == before


@pytest.mark.asyncio
async def test_capabilities_are_qualified_separately(qualified_sources):
    pkg = package()
    value = record(pkg, proof(pkg, automatic_verified_count=0))
    snap = await service.validated_snapshot(None, value)
    assert snap["capability_scope"]["position_checks"] is True
    assert snap["capability_scope"]["writing_auto"] is False
    assert set(snap["compatibility_pending"]) == {"writing_auto"}


@pytest.mark.parametrize("legacy", [False, True])
def test_offline_read_uses_the_same_effective_capability_gate(tmp_path, legacy):
    pkg = package()
    p = proof(pkg)
    if legacy: p.pop("independent_sample_count")
    value = record(pkg, p)
    before = deepcopy(value)
    current = {k: value[k] for k in ("region", "overlay", "report_kind", "target_year", "revision")}
    payload = {"current": [current], "bindings": [value], "packages": [pkg]}
    bundle = tmp_path / "approved-test.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("catalog.json", service.canonical(payload))
        archive.writestr("checksums.json", "{}")
    snap = service.offline_snapshot(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), 2025)
    assert snap["available"]
    assert snap["capability_scope"]["writing_auto"] is not legacy
    assert snap["capability_scope"]["position_checks"] is not legacy
    assert snap["capability_scope"]["structure"]
    assert value == before


class FakeConnection:
    def __init__(self, pkg, prior):
        self.pkg = pkg
        self.prior = prior
        self.writes = []
        self.current = 1
    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def execute(self, sql, *args):
        self.writes.append((sql, args))
    async def fetchval(self, sql, *args): return self.current
    async def fetchrow(self, sql, *args):
        if "disclosure_template_packages" in sql: return deepcopy(self.pkg)
        if "disclosure_template_binding_revisions" in sql: return deepcopy(self.prior)
        raise AssertionError(sql)


def rollback_data(pkg):
    return {"region": "上海市", "overlay": "", "report_kind": "final", "target_year": 2025,
            "package_id": pkg["id"], "expected_revision": 1, "adoption_kind": "rollback",
            "restore_revision": 1, "capabilities": deepcopy(CAPS), "basis": "synthetic restore"}


@pytest.mark.asyncio
async def test_rollback_keeps_and_revalidates_original_evidence(qualified_sources, monkeypatch):
    monkeypatch.setattr(service, "audit", AsyncMock())
    pkg = package()
    p = proof(pkg)
    prior = record(pkg, p)
    before = deepcopy(prior)
    conn = FakeConnection(pkg, prior)
    assert (await service.bind(conn, rollback_data(pkg), "test"))["revision"] == 2
    saved = next(args for sql, args in conn.writes if "INSERT INTO disclosure_template_binding_revisions" in sql)
    basis = json.loads(saved[8])
    assert basis["restored_from_revision"] == 1
    assert basis["original_adoption_kind"] == "not_received"
    assert basis["compatibility_source_revision"] == 1
    assert basis["compatibility"] == p
    assert prior == before
    snap = await service.validated_snapshot(None, {**prior, "adoption_kind": "rollback", "basis": service.canonical(basis)})
    assert snap["capability_scope"] == CAPS


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing-n", "wrong-algorithm", "legacy-rollback"])
async def test_rollback_cannot_bypass_compatibility_gate(defect, qualified_sources, monkeypatch):
    monkeypatch.setattr(service, "audit", AsyncMock())
    pkg = package()
    p = proof(pkg)
    if defect == "missing-n": p.pop("independent_sample_count")
    if defect == "wrong-algorithm": p["algorithm"] = "material-writing-v3.5"
    prior = record(pkg, p, adoption="rollback" if defect == "legacy-rollback" else "historical_reference")
    conn = FakeConnection(pkg, prior)
    with pytest.raises(HTTPException):
        await service.bind(conn, rollback_data(pkg), "test")
    assert not any("INSERT INTO" in sql for sql, _ in conn.writes)


@pytest.mark.asyncio
async def test_pending_position_proof_is_deferred_but_intentional_disable_is_not_applicable(qualified_sources):
    pkg = package()
    old = proof(pkg)
    old.pop("independent_sample_count")
    snap = await service.validated_snapshot(None, record(pkg, old))
    resolution = {"status": "confirmed", "value": "unit", "snapshot": snap}
    doc = document(["2025年度决算", "2025年度决算表"], resolution)
    with pytest.raises(RuleDeferred): CMM027_TemplatePositionScope().apply(doc)
    doc.document_profile.writing_scope["snapshot"] = {"available": True, "capability_scope": {"position_checks": False}}
    with pytest.raises(RuleNotApplicable): CMM027_TemplatePositionScope().apply(doc)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_invalid_automatic_proof_remains_insufficient_without_overriding_explicit_evidence(monkeypatch, qualified_sources, explicit):
    from src.services import material_writing
    from src.services.document_profile_resolver import resolve_document_profile
    pkg = package()
    old = proof(pkg)
    old.pop("independent_sample_count")
    snap = await service.validated_snapshot(None, record(pkg, old))
    monkeypatch.setenv("GBC_TEMPLATE_SCOPE", "true")
    monkeypatch.setenv("GBC_TEMPLATE_EXPORT", "synthetic-only")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(service, "offline_for_profile", lambda *args: snap)
    pages = ["上海市测试局2025年度" + ("单位决算" if explicit else "决算"),
             "2025年度决算表\n本表反映单位本年度收入。"]
    profile = resolve_document_profile(page_texts=pages)
    result = await material_writing.load("test-only", "a" * 64, profile, pages)
    assert result["template_capability_pending"]["writing_auto"]
    assert result["status"] == ("confirmed" if explicit else "insufficient")
    assert result["source"] == ("declaration" if explicit else "unresolved")


@pytest.mark.parametrize("errors", [False, 0.0, -1, 1])
def test_publish_automatic_errors_are_real_integer_counts(errors):
    with pytest.raises(HTTPException):
        service.validate_capabilities({"writing_auto": True}, {"writing_auto": {
            "report_reference": "synthetic-only", "independent_acceptance": True,
            "automatic_verified_count": 1, "automatic_errors": errors}})


@pytest.mark.asyncio
async def test_rollback_chain_preserves_original_proof_instead_of_request_replacement(qualified_sources, monkeypatch):
    monkeypatch.setattr(service, "audit", AsyncMock())
    pkg = package()
    original = proof(pkg)
    prior = record(pkg, original, adoption="rollback", restored_from_revision=1,
                   original_adoption_kind="not_received", compatibility_source_revision=1)
    prior["revision"] = 2
    conn = FakeConnection(pkg, prior)
    conn.current = 2
    data = {**rollback_data(pkg), "expected_revision": 2, "restore_revision": 2,
            "compatibility": proof(pkg, target_year=2024)}
    assert (await service.bind(conn, data, "test"))["revision"] == 3
    saved = next(args for sql, args in conn.writes if "INSERT INTO disclosure_template_binding_revisions" in sql)
    basis = json.loads(saved[8])
    assert basis["restored_from_revision"] == 2
    assert basis["compatibility_source_revision"] == 1
    assert basis["original_adoption_kind"] == "not_received"
    assert basis["compatibility"] == original


@pytest.mark.asyncio
async def test_same_year_issued_rollback_needs_no_cross_year_sample_gate(qualified_sources, monkeypatch):
    monkeypatch.setattr(service, "audit", AsyncMock())
    pkg = package()
    prior = record(pkg, None, adoption="issued")
    prior["target_year"] = 2024
    conn = FakeConnection(pkg, prior)
    assert (await service.bind(conn, {**rollback_data(pkg), "target_year": 2024}, "test"))["revision"] == 2
    saved = next(args for sql, args in conn.writes if "INSERT INTO disclosure_template_binding_revisions" in sql)
    restored = {**prior, "adoption_kind": "rollback", "basis": saved[8]}
    assert (await service.validated_snapshot(None, restored))["capability_scope"] == CAPS
