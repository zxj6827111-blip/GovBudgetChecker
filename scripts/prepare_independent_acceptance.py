#!/usr/bin/env python3
"""Prepare empty independent intake slots, never generate human frozen truth."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMPLATES = ROOT / "docs/acceptance_templates"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_handoff(path: Path, *, stage: str = "intake") -> dict:
    if stage not in {"intake", "execution"}:
        raise ValueError("stage must be intake or execution")
    try:
        return _validate_handoff(path, stage=stage)
    except (TypeError, AttributeError, KeyError) as exc:
        return {
            "schema_version": "gbc.independent-handoff-check.v1",
            "stage": stage,
            "status": "incomplete",
            "passed": False,
            "errors": [f"malformed attachment fields: {type(exc).__name__}: {exc}"],
            "business_acceptance": "not_evaluated",
            "identity_authentication": "human_signoff_required_hashes_only_bind_bytes",
            "sealed_truth_read": False,
        }


def _validate_handoff(path: Path, *, stage: str) -> dict:
    """Check signed attachments without opening sealed truth or inventing signers.

    This is a handoff integrity gate, not identity authentication or a business
    acceptance verdict. A hash only binds the supplied attachment's bytes.
    """
    errors: list[str] = []
    base = path.resolve().parent

    def read_object(file: Path, label: str) -> dict:
        try:
            value = json.loads(file.read_text())
            if not isinstance(value, dict):
                raise ValueError("must be a JSON object")
            return value
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            return {}

    def attachment(ref: object, label: str, *, read: bool = True) -> dict:
        if not isinstance(ref, dict):
            errors.append(f"{label}: missing path/sha256 attachment")
            return {}
        raw = ref.get("path")
        digest = ref.get("sha256")
        if not isinstance(raw, str) or not raw.strip() or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"{label}: missing path or lowercase SHA-256")
            return {}
        file = Path(raw)
        file = file if file.is_absolute() else base / file
        try:
            if sha(file) != digest:
                errors.append(f"{label}: attachment SHA-256 mismatch")
                return {}
        except OSError as exc:
            errors.append(f"{label}: {exc}")
            return {}
        return read_object(file, label) if read else {"resolved_path": str(file)}

    def require(condition: bool, label: str) -> None:
        if not condition:
            errors.append(label)

    def timestamp(value: object, label: str) -> datetime | None:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if result.tzinfo is None:
                raise ValueError("timezone is required")
            return result
        except ValueError:
            errors.append(f"{label}: missing/invalid timezone-aware timestamp")
            return None

    payload = read_object(path, "handoff")
    require(payload.get("schema_version") == "gbc.independent-handoff.v1", "handoff: invalid schema_version")
    roles = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
    participants: dict[str, set[str]] = {}
    for role in ("developers", "custodians", "annotators", "independent_reviewers", "executors"):
        value = roles.get(role)
        require(isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value), f"roles.{role}: actual person IDs are required")
        participants[role] = set(v for v in value if isinstance(v, str)) if isinstance(value, list) else set()
    independent = set().union(*(participants[r] for r in participants if r != "developers"))
    require(not (participants["developers"] & independent), "independence: developers must not hold independent roles")
    require(not (participants["annotators"] & participants["independent_reviewers"]), "independence: annotator must not independently review own truth")
    signoff = attachment(payload.get("role_signoff"), "role_signoff")
    require(signoff.get("roles") == roles and signoff.get("status") == "signed", "role_signoff: signed roles must match handoff")
    access = attachment(payload.get("access_review"), "access_review")
    require(access.get("blind_truth_access_before_code_freeze") is False and access.get("status") == "signed", "access_review: pre-freeze blind truth access must be reviewed and absent")
    require(access.get("reviewed_by") in participants["independent_reviewers"], "access_review: independent reviewer ID missing")

    exclusions = attachment(payload.get("development_exclusions"), "development_exclusions")
    known = exclusions.get("sha256_groups") if isinstance(exclusions.get("sha256_groups"), dict) else {}
    intake = attachment(payload.get("corpus_intake"), "corpus_intake")
    documents = intake.get("documents") if isinstance(intake.get("documents"), list) else []
    strata: Counter = Counter()
    seen_sha: set[str] = set()
    seen_ids: set[str] = set()
    for index, row in enumerate(documents):
        label = f"corpus_intake.documents[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label}: must be an object")
            continue
        doc_id = row.get("doc_id")
        require(isinstance(doc_id, str) and bool(doc_id) and doc_id not in seen_ids, f"{label}: missing/duplicate doc_id")
        if isinstance(doc_id, str):
            seen_ids.add(doc_id)
        stratum = tuple(row.get(k) for k in ("split", "document_type", "organization_type", "source_type"))
        valid = all(isinstance(v, str) for v in stratum) and stratum in set(itertools.product(("development", "blind"), ("budget", "final"), ("department", "unit"), ("native", "scanned")))
        require(valid, f"{label}: invalid stratum")
        if valid:
            strata[stratum] += 1
        ref = row.get("source_pdf")
        attachment(ref, f"{label}.source_pdf", read=False)
        digest = ref.get("sha256") if isinstance(ref, dict) else None
        if isinstance(digest, str):
            require(digest not in seen_sha, f"{label}: duplicate original SHA-256")
            require(row.get("split") != "blind" or digest not in known, f"{label}: known development original cannot become blind")
            seen_sha.add(digest)
        require(row.get("official_original") is True and row.get("complete_original") is True, f"{label}: complete official original required")
        require(isinstance(row.get("source_url"), str) and row["source_url"].startswith(("https://", "http://")), f"{label}: official source URL missing")
        timestamp(row.get("downloaded_at"), f"{label}.downloaded_at")
        require(bool(row.get("near_duplicate_group")), f"{label}: near-duplicate group missing")
    for stratum in itertools.product(("development", "blind"), ("budget", "final"), ("department", "unit"), ("native", "scanned")):
        require(strata[stratum] >= 4, f"intake stratum {'/'.join(stratum)}: {strata[stratum]}/4")
    groups: dict[str, set[str]] = {}
    for row in documents:
        if isinstance(row, dict) and isinstance(row.get("near_duplicate_group"), str):
            groups.setdefault(row["near_duplicate_group"], set()).add(str(row.get("split")))
    require(all(len(splits) == 1 for splits in groups.values()), "near_duplicate_review: a group crosses development/blind splits")
    for field in ("near_duplicate_review", "disagreement_review"):
        record = attachment(payload.get(field), field)
        require(record.get("status") == "signed" and record.get("resolved") is True, f"{field}: signed resolved record required")
        require(record.get("reviewed_by") in participants["independent_reviewers"], f"{field}: actual independent reviewer required")
        require(isinstance(record.get("doc_ids"), list) and set(record["doc_ids"]) == seen_ids, f"{field}: review must cover the intake doc IDs")

    freeze = attachment(payload.get("freeze_record"), "freeze_record")
    require(freeze.get("status") == "frozen_signed", "freeze_record: status must be frozen_signed")
    require(freeze.get("blind_truth_access_before_code_freeze") is False, "freeze_record: blind truth access before code freeze must be absent")
    sealed_at = timestamp(freeze.get("truth_sealed_at"), "freeze_record.truth_sealed_at")
    code_at = timestamp(freeze.get("code_frozen_at"), "freeze_record.code_frozen_at")
    require(sealed_at is not None and code_at is not None and sealed_at <= code_at, "freeze_record: truth seal must precede code freeze")
    for field in ("source_snapshot_sha256", "model_manifest_sha256", "configuration_fingerprint", "evaluator_sha256", "dataset_manifest_sha256"):
        require(isinstance(freeze.get(field), str) and bool(re.fullmatch(r"[0-9a-f]{64}", freeze[field])), f"freeze_record.{field}: SHA-256 required")
    attachment(payload.get("truth_seal"), "truth_seal", read=False)
    require(intake.get("blind_defect_groups_attested", 0).__class__ is int and intake.get("blind_defect_groups_attested", 0) >= 100, "intake: independent humans must attest at least 100 blind defect groups")
    signoff = attachment(payload.get("freeze_signoff"), "freeze_signoff")
    require(signoff.get("status") == "signed" and signoff.get("custodian") in participants["custodians"], "freeze_signoff: signed custodian record required")
    require(signoff.get("reviewed_by") in participants["independent_reviewers"], "freeze_signoff: independent reviewer required")
    identities = payload.get("frozen_identities") if isinstance(payload.get("frozen_identities"), dict) else {}
    source_snapshot = {}
    for name, field in (
        ("source_snapshot", "source_snapshot_sha256"),
        ("model_manifest", "model_manifest_sha256"),
        ("configuration", "configuration_fingerprint"),
        ("evaluator", "evaluator_sha256"),
        ("dataset_manifest", "dataset_manifest_sha256"),
        ("overlay_manifest", "overlay_manifest_sha256"),
    ):
        ref = identities.get(name)
        attachment(ref, f"frozen_identities.{name}", read=False)
        require(isinstance(ref, dict) and ref.get("sha256") == freeze.get(field), f"frozen_identities.{name}: freeze identity mismatch")
        if name == "source_snapshot":
            source_snapshot = attachment(ref, "frozen_identities.source_snapshot")
    technical = attachment(payload.get("technical_preflight"), "technical_preflight")
    _validate_technical_preflight(technical, attachment, require, timestamp)
    receipt = attachment(technical.get("service_receipt"), "technical_preflight.service_receipt")
    fingerprints = receipt.get("code_fingerprints")
    require(isinstance(fingerprints, dict) and bool(fingerprints) and all(source_snapshot.get(name) == digest for name, digest in fingerprints.items()), "technical_preflight: service source fingerprints must match frozen source snapshot")

    if stage == "execution":
        runs = payload.get("output_freezes")
        require(isinstance(runs, list) and len(runs) == 2, "output_freezes: exactly two frozen runs required")
        run_ids = set()
        output_times = []
        for index, ref in enumerate(runs if isinstance(runs, list) else []):
            run = attachment(ref, f"output_freezes[{index}]")
            run_id = run.get("run_id")
            require(isinstance(run_id, str) and bool(run_id) and run_id not in run_ids, "output_freezes: unique actual run_id required")
            if isinstance(run_id, str):
                run_ids.add(run_id)
            frozen_at = timestamp(run.get("frozen_at"), f"output_freezes[{index}].frozen_at")
            require(code_at is not None and frozen_at is not None and frozen_at > code_at, "output_freezes: outputs must follow code freeze")
            if frozen_at:
                output_times.append(frozen_at)
            for key in ("source_snapshot_sha256", "model_manifest_sha256", "configuration_fingerprint", "dataset_manifest_sha256", "overlay_manifest_sha256"):
                require(bool(freeze.get(key)) and run.get(key) == freeze.get(key), f"output_freezes[{index}]: frozen {key} mismatch")
            require(run.get("blind_doc_ids") == sorted(row["doc_id"] for row in documents if isinstance(row, dict) and row.get("split") == "blind" and isinstance(row.get("doc_id"), str)), "output_freezes: all blind documents must be covered")
            manifest = attachment(run.get("output_manifest"), f"output_freezes[{index}].output_manifest")
            rows = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
            blind_ids = {row["doc_id"] for row in documents if isinstance(row, dict) and row.get("split") == "blind" and isinstance(row.get("doc_id"), str)}
            actual_ids = [row.get("doc_id") for row in rows if isinstance(row, dict)]
            require(len(actual_ids) == len(blind_ids) and set(actual_ids) == blind_ids, "output_manifest: each blind document requires actual output attachments")
            for row in rows:
                if not isinstance(row, dict):
                    errors.append("output_manifest: document row must be an object")
                    continue
                require(bool(row.get("analysis_revision")), "output_manifest: current analysis revision missing")
                attachment(row.get("replay"), f"output_manifest.{row.get('doc_id')}.replay", read=False)
                exports = row.get("exports") if isinstance(row.get("exports"), dict) else {}
                for kind in ("json", "csv", "docx", "pdf"):
                    attachment(exports.get(kind), f"output_manifest.{row.get('doc_id')}.{kind}", read=False)
        release = attachment(payload.get("truth_release"), "truth_release")
        released_at = timestamp(release.get("released_at"), "truth_release.released_at")
        require(release.get("status") == "signed" and release.get("custodian") in participants["custodians"], "truth_release: actual custodian signed record required")
        require(len(output_times) == 2 and released_at is not None and all(t < released_at for t in output_times), "truth_release: release must follow both frozen outputs")

    return {
        "schema_version": "gbc.independent-handoff-check.v1",
        "stage": stage,
        "status": "ready" if not errors else "incomplete",
        "passed": not errors,
        "errors": errors,
        "document_count": len(documents),
        "business_acceptance": "not_evaluated",
        "identity_authentication": "human_signoff_required_hashes_only_bind_bytes",
        "sealed_truth_read": False,
    }


def _validate_technical_preflight(payload: dict, attachment, require, timestamp) -> None:
    """Require current-run evidence; old database counts cannot satisfy this gate."""
    require(payload.get("schema_version") == "gbc.independent-technical-preflight.v1", "technical_preflight: invalid schema_version")
    receipt = attachment(payload.get("service_receipt"), "technical_preflight.service_receipt")
    require(receipt.get("status") == "passed" and receipt.get("cleanup") == "complete" and receipt.get("source_unchanged_during_run") is True, "technical_preflight: real service run, source stability and cleanup must pass")
    scan = payload.get("original_scanned_budget")
    scan = scan if isinstance(scan, dict) else {}
    require(scan.get("official_original") is True and scan.get("synthetic") is False and scan.get("derived") is False, "original_scanned_budget: complete official original required")
    scan_ref = scan.get("source_pdf")
    scan_file = attachment(scan_ref, "original_scanned_budget.source_pdf", read=False)
    scan_sha = scan_ref.get("sha256") if isinstance(scan_ref, dict) else None
    count = scan.get("page_count")
    processed = scan.get("processed_physical_pages")
    require(type(count) is int and count > 0 and isinstance(processed, list) and len(processed) == count and all(type(page) is int and page == index + 1 for index, page in enumerate(processed)), "original_scanned_budget: every original physical page must be processed")
    budget, elapsed = scan.get("worker_budget_seconds"), scan.get("elapsed_seconds")
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (budget, elapsed)) and elapsed <= budget, "original_scanned_budget: finite Worker budget and measured in-budget completion required")
    result = attachment(scan.get("result"), "original_scanned_budget.result")
    require(result.get("status") in {"done", "completed", "review_required"} and bool(result.get("analysis_revision")), "original_scanned_budget: current result must finish with revision")
    require(bool(scan_sha) and result.get("checksum") == scan_sha, "original_scanned_budget: result must bind to supplied original SHA-256")
    if scan_file.get("resolved_path"):
        try:
            import fitz

            with fitz.open(scan_file["resolved_path"]) as pdf:
                require(pdf.page_count == count, "original_scanned_budget: page_count must match complete source PDF")
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            require(False, f"original_scanned_budget: could not verify original PDF page count: {exc}")
    attachment(scan.get("worker_trace"), "original_scanned_budget.worker_trace", read=False)
    ocr = ((result.get("result") or {}).get("meta") or {}).get("ocr") or {}
    pages = ocr.get("pages") if isinstance(ocr.get("pages"), list) else []
    ok_pages = {row.get("page") for row in pages if isinstance(row, dict) and row.get("ok") is True}
    require(type(count) is int and len(ok_pages) == count and ok_pages == set(range(1, count + 1)) and not ocr.get("failed_pages"), "original_scanned_budget: real OCR must cover all pages without failed requests")

    cache = payload.get("mixed_final_cache")
    cache = cache if isinstance(cache, dict) else {}
    source_ref = cache.get("source_pdf")
    attachment(source_ref, "mixed_final_cache.source_pdf", read=False)
    source_sha = source_ref.get("sha256") if isinstance(source_ref, dict) else None
    readback = attachment(cache.get("cache_readback"), "mixed_final_cache.cache_readback")
    require(readback.get("cache_hit") is True and readback.get("cell_provenance_decoded") is True, "mixed_final_cache: actual cache hit and JSONB cell decoding evidence required")
    require(isinstance(cache.get("target_fact_uid"), str) and bool(cache["target_fact_uid"]) and isinstance(cache.get("expected_corrected_raw_value"), str) and bool(cache["expected_corrected_raw_value"]), "mixed_final_cache: explicit real amount target and corrected value required")
    try:
        amount = Decimal(str(cache.get("expected_corrected_raw_value")).replace(",", ""))
        require(amount.is_finite(), "mixed_final_cache: corrected amount must be a finite number")
    except InvalidOperation:
        require(False, "mixed_final_cache: corrected amount must be numeric")
    revisions = []
    job_ids = []
    for stage in ("initial", "corrected"):
        result = attachment(cache.get(f"{stage}_result"), f"mixed_final_cache.{stage}_result")
        meta = ((result.get("result") or {}).get("meta") or {})
        ingest = meta.get("structured_ingest") or {}
        require(ingest.get("status") == "done", f"mixed_final_cache.{stage}: current structured_ingest must be done")
        revision = result.get("analysis_revision")
        revisions.append(revision)
        job_ids.append(result.get("job_id"))
        require(result.get("status") in {"done", "completed", "review_required"} and bool(revision), f"mixed_final_cache.{stage}: finished current analysis revision required")
        require(bool(source_sha) and result.get("checksum") == source_sha, f"mixed_final_cache.{stage}: result source SHA mismatch")
        db = attachment(cache.get(f"{stage}_database"), f"mixed_final_cache.{stage}_database")
        require(db.get("schema_version") == "gbc.current-ingest-query.v1", f"mixed_final_cache.{stage}: version-scoped query receipt required")
        timestamp(db.get("query_timestamp"), f"mixed_final_cache.{stage}.query_timestamp")
        require(type(ingest.get("parse_run_id")) is int and ingest["parse_run_id"] > 0 and type(ingest.get("document_version_id")) is int and ingest["document_version_id"] > 0, f"mixed_final_cache.{stage}: persisted run/version IDs required")
        for field, expected in (("job_id", result.get("job_id")), ("analysis_revision", revision), ("parse_run_id", ingest.get("parse_run_id")), ("document_version_id", ingest.get("document_version_id")), ("source_sha256", source_sha)):
            require(expected is not None and db.get(field) == expected, f"mixed_final_cache.{stage}: current {field} mismatch")
        require(type(db.get("facts_count")) is int and db["facts_count"] > 0 and type(db.get("evidence_count")) is int and db["evidence_count"] > 0, f"mixed_final_cache.{stage}: current-run facts/evidence missing")
        if stage == "corrected":
            require(ingest.get("parse_cache_hit") is True, "mixed_final_cache: corrected current ingest must actually read the parse cache")
            require(readback.get("parse_run_id") == ingest.get("parse_run_id") and ingest.get("parse_run_id") is not None, "mixed_final_cache: cache readback parse run mismatch")
            _validate_correction_projection(db, result, cache, revisions[0], attachment, require, timestamp)
    require(bool(revisions[0]) and bool(revisions[1]) and revisions[0] != revisions[1], "mixed_final_cache: correction must create a distinct current analysis revision")
    require(bool(job_ids[0]) and job_ids[0] == job_ids[1], "mixed_final_cache: both revisions must belong to the same job")


def _validate_correction_projection(db, result, cache, initial_revision, attachment, require, timestamp) -> None:
    """Bind the projected amount to immutable rows and a consumed JSONL overlay."""
    label = "mixed_final_cache.corrected_projection"
    raw = attachment(db.get("original_database_rows"), f"{label}.original_database_rows")
    context = attachment(db.get("fact_context"), f"{label}.fact_context")
    ledger = attachment(db.get("correction_ledger"), f"{label}.correction_ledger", read=False)
    job = raw.get("job") or {}
    run = raw.get("parse_run") or {}
    expected_sha = db.get("source_sha256")
    require(job.get("job_uuid") == result.get("job_id") and job.get("file_hash") == expected_sha, f"{label}: raw database job/source mismatch")
    require(raw.get("result_revision") == result.get("analysis_revision") and raw.get("result_ingest_status") == "done", f"{label}: raw database must bind the current result revision token")
    require(type(job.get("analysis_revision")) is int and job["analysis_revision"] > 0, f"{label}: database revision counter missing")
    require(run.get("id") == db.get("parse_run_id") and run.get("document_version_id") == db.get("document_version_id") and run.get("status") == "completed", f"{label}: raw database run/version mismatch")
    require(run.get("source_file_hash") == run.get("version_file_hash") == expected_sha, f"{label}: raw parse source/version SHA mismatch")
    require(raw.get("run_facts_count") == db.get("facts_count") and raw.get("run_evidence_count") == db.get("evidence_count"), f"{label}: current-run query counts mismatch")

    def ids(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            require(False, f"{label}: nonempty explicit evidence IDs required")
            return set()
        return set(value)

    target = cache.get("target_fact_uid")
    expected_value = cache.get("expected_corrected_raw_value")
    original_rows = raw.get("target_original_facts") or []
    originals = [row for row in original_rows if isinstance(row, dict) and row.get("fact_uid") == target]
    require(len(originals) == 1, f"{label}: unique immutable original target row required")
    original = originals[0] if len(originals) == 1 else {}
    original_ids = ids(original.get("evidence_ids"))
    evidence = raw.get("target_original_evidence") or []
    require({row.get("evidence_id") for row in evidence if isinstance(row, dict)} == original_ids and bool(original_ids), f"{label}: original evidence query must cover target references")
    require(all(isinstance(row, dict) and row.get("file_hash") == expected_sha and type(row.get("physical_page")) is int and row["physical_page"] > 0 and bool(row.get("quote")) for row in evidence), f"{label}: original evidence source/page/quote mismatch")
    try:
        old_amount = Decimal(str(original.get("raw_value")).replace(",", ""))
        new_amount = Decimal(str(expected_value).replace(",", ""))
        require(old_amount.is_finite() and new_amount.is_finite() and old_amount != new_amount, f"{label}: preserve original amount and prove a real amount change")
    except InvalidOperation:
        require(False, f"{label}: original/corrected amounts must be numeric")

    meta = ((result.get("result") or {}).get("meta") or {})
    summary = meta.get("fact_context") or {}
    require(bool(summary.get("version_token")) and context.get("version_token") == summary.get("version_token"), f"{label}: FactContext must match current result version token")
    require(bool(summary.get("parse_run_uid")) and context.get("parse_run_uid") == summary.get("parse_run_uid") and context.get("file_hash") == summary.get("file_hash") == expected_sha, f"{label}: FactContext source/parse identity mismatch")
    context_rows = context.get("facts") or []
    require(summary.get("fact_count") == len(context_rows), f"{label}: FactContext count must match current result")
    applied = context.get("overlay_records") or []
    require(summary.get("overlay_count") == len(applied) and bool(applied), f"{label}: current result must contain consumed overlays")
    try:
        from src.engine.fact_context import FactContext

        calculated = FactContext.from_dict(context).compute_version_token()
        require(calculated == context.get("version_token"), f"{label}: FactContext token must derive from the supplied facts")
    except (ImportError, ValueError, TypeError, KeyError, AttributeError):
        require(False, f"{label}: invalid FactContext facts")
    targets = [row for row in context_rows if isinstance(row, dict) and row.get("fact_uid") == target]
    require(len(targets) == 1, f"{label}: unique current FactContext target required")
    current = targets[0] if len(targets) == 1 else {}
    current_ids = ids(current.get("evidence_ids"))
    require(current.get("raw_value") == expected_value and current_ids == original_ids, f"{label}: projected value/evidence must come from current FactContext")
    rows = db.get("corrected_fact_rows") or []
    projected = [row for row in rows if isinstance(row, dict) and row.get("fact_uid") == target]
    require(len(projected) == 1 and projected[0].get("raw_value") == current.get("raw_value") and ids(projected[0].get("evidence_ids")) == current_ids, f"{label}: declared projection must equal current FactContext target")

    records = []
    if ledger.get("resolved_path"):
        try:
            from src.services.correction_overlay_store import read_jsonl_records

            records = read_jsonl_records(Path(ledger["resolved_path"]))
        except (OSError, ValueError):
            require(False, f"{label}: invalid persisted correction JSONL")
    relevant = [(record, change) for record in records for change in record.get("changes") or []
                if isinstance(change, dict) and change.get("target_type") == "fact" and change.get("target_id") == target and change.get("field") == "raw_value"]
    require(bool(relevant), f"{label}: persisted amount correction for target required")
    if relevant:
        record, change = relevant[-1]
        basis = f"{result.get('job_id')}:{initial_revision}"
        require(record.get("job_id") == result.get("job_id") and record.get("analysis_basis_token") == basis and record.get("record_status") == "append_only_overlay" and bool(record.get("record_id")) and bool(record.get("actor")), f"{label}: persisted correction must bind original analysis basis and actual actor")
        timestamp(record.get("created_at"), f"{label}.correction.created_at")
        change_ids = ids(change.get("evidence_ids"))
        require(change.get("value") == expected_value and bool(change_ids) and change_ids <= original_ids, f"{label}: persisted target value/evidence mismatch")
        applied_targets = [row for row in applied if isinstance(row, dict) and row.get("fact_uid") == target]
        require(bool(applied_targets) and applied_targets[-1].get("raw_value") == expected_value and applied_targets[-1].get("basis_token") == basis and applied_targets[-1].get("corrected_by") == record.get("actor"), f"{label}: FactContext overlay must consume persisted correction")


def prepare(destination: Path, known_roots: list[Path]) -> dict:
    """Snapshot code/exclusions and create pending worksheets for a custodian."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("output directory must be empty; preserve earlier preparation records")
    destination.mkdir(parents=True, exist_ok=True)
    excluded: dict[str, list[str]] = {}
    defaults = [ROOT / "samples", ROOT / "corpus", ROOT / "outputs"]
    roots = sorted(set(directory.resolve() for directory in defaults + known_roots))
    for directory in roots:
        for path in sorted(directory.rglob("*")) if directory.exists() else []:
            if path.is_file() and path.suffix.lower() == ".pdf" and destination not in path.parents:
                excluded.setdefault(sha(path), []).append(str(path.resolve()))
    exclusion = {
        "scope": "known_development_materials_and_outputs_do_not_relabel_as_blind",
        "sha256_groups": excluded,
        "near_duplicate_review": "pending_independent_human_review",
        "searched_roots": [str(directory) for directory in roots],
        "missing_roots": [str(directory) for directory in roots if not directory.exists()],
    }
    write(destination / "development_exclusions.json", exclusion)

    names = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    extensions = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".cjs",
        ".css",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".lock",
        ".txt",
        ".sh",
    }
    paths = [
        ROOT / name
        for name in sorted(set(names))
        if name
        and not name.startswith(("outputs/", ".mimosa/", "corpus/", "samples/", "docs/"))
        and (
            Path(name).suffix in extensions
            or Path(name).name.startswith("Dockerfile")
            or name in {"AGENTS.md", ".env.example", ".dockerignore"}
        )
    ]
    code = {str(path.relative_to(ROOT)): sha(path) for path in paths if path.is_file()}
    write(destination / "code_manifest.json", code)
    shutil.copytree(TEMPLATES, destination / "templates", dirs_exist_ok=True)

    slots = []
    for split, document, organization, source in itertools.product(
        ("development", "blind"), ("budget", "final"), ("department", "unit"), ("native", "scanned")
    ):
        for number in range(1, 5):
            slot_id = f"{split}-{document}-{organization}-{source}-{number:02}"
            slot = dict(
                slot_id=slot_id,
                status="pending_original_and_human_review",
                split=split,
                document_type=document,
                organization_type=organization,
                source_type=source,
                actual_doc_id=None,
                source_url=None,
                source_sha256=None,
                organization=None,
                year=None,
                reviewer=None,
            )
            slots.append(slot)
            directory = destination / "intake" / split / slot_id
            directory.mkdir(parents=True)
            for name in ("golden", "label", "scan_attestations"):
                shutil.copyfile(
                    TEMPLATES / f"{name}.template.json", directory / f"{name}.pending.json"
                )
            write(directory / "intake.pending.json", slot)
    write(destination / "slots.pending.json", {"scope": "empty_slots_not_samples", "slots": slots})
    write(
        destination / "dataset.pending.json", {"schema_version": "zc07.dataset.v1", "documents": []}
    )
    shutil.copyfile(TEMPLATES / "e2e.template.json", destination / "e2e.pending.json")
    freeze = json.loads((TEMPLATES / "freeze_record.template.json").read_text())
    freeze["source_snapshot_sha256"] = sha(destination / "code_manifest.json")
    model = ROOT / "models/MANIFEST.sha256"
    freeze["model_manifest_sha256"] = sha(model) if model.is_file() else None
    freeze["evaluator_sha256"] = sha(ROOT / "scripts/run_zc07_acceptance.py")
    write(destination / "freeze_record.pending.json", freeze)
    for name in ("corpus_intake", "technical_preflight", "handoff"):
        shutil.copyfile(TEMPLATES / f"{name}.template.json", destination / f"{name}.pending.json")
    handoff = json.loads((destination / "handoff.pending.json").read_text())
    for field, file in (
        ("development_exclusions", "development_exclusions.json"),
        ("corpus_intake", "corpus_intake.pending.json"),
        ("freeze_record", "freeze_record.pending.json"),
        ("technical_preflight", "technical_preflight.pending.json"),
    ):
        handoff[field] = {"path": file, "sha256": sha(destination / file)}
    write(destination / "handoff.pending.json", handoff)
    receipt = dict(
        schema_version="gbc.independent-intake-preparation.v1",
        status="prepared_pending_independent_humans_and_materials",
        prepared_at=datetime.now(timezone.utc).isoformat(),
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        code_manifest_sha256=sha(destination / "code_manifest.json"),
        exclusion_manifest_sha256=sha(destination / "development_exclusions.json"),
        required_documents={"development": 32, "blind": 32},
        required_blind_defect_groups=100,
        actual_frozen_documents=0,
        actual_human_defect_groups=0,
        business_metrics=None,
        slot_count=len(slots),
        handoff_gate="pending_not_passed",
        sealed_truth_read=False,
    )
    write(destination / "preparation_receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="Create a preparation pack in a new empty directory")
    mode.add_argument("--check-handoff", type=Path, help="Validate signed handoff attachments without reading sealed truth")
    parser.add_argument("--stage", choices=("intake", "execution"), default="intake")
    parser.add_argument("--check-report", type=Path, help="Save a gate receipt to a new file")
    parser.add_argument("--known-development-root", type=Path, action="append")
    args = parser.parse_args()
    if args.check_handoff:
        receipt = validate_handoff(args.check_handoff, stage=args.stage)
        if args.check_report:
            if args.check_report.exists():
                parser.error("check-report must be a new file; preserve earlier gate receipts")
            write(args.check_report, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0 if receipt["passed"] else 2
    if args.check_report or args.stage != "intake":
        parser.error("--stage/--check-report only apply to --check-handoff")
    roots = args.known_development_root or []
    receipt = prepare(args.output, roots)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
