"""统一输入编排入口；原始读取、逻辑重建、事实和校正使用同一产物。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re

from src.services.fiscal_reconstructor import (
    UnifiedFiscalReconstructor,
    RECONSTRUCTION_VERSION,
    TEMPLATE_VERSION,
)
from src.services.input_geometry import GEOMETRY_VERSION
from src.engine.fiscal_lexeme import LEXER_VERSION
from src.services.fiscal_narrative import reconstruct_narrative, NARRATIVE_VERSION
from src.services.fiscal_field_adjudication import ADJUDICATION_VERSION
from src.services.ocr_image_grid import IMAGE_GRID_VERSION
from src.services.ocr_grid_fragments import FRAGMENT_STITCH_VERSION
from src.services.ocr_grid_reconstruction import GRID_RECONSTRUCTION_VERSION
from src.services.ocr_orientation import ORIENTATION_VERSION
from src.services.fiscal_source_profile import SOURCE_PROFILE_VERSION
from src.services.ocr_schedule import SCHEDULE_VERSION

INPUT_VERSION = "fiscal-input-v2-source-image-grid"
VERSIONS = {
    "input": INPUT_VERSION,
    "template": TEMPLATE_VERSION,
    "geometry": GEOMETRY_VERSION,
    "preprocessing": "reversible-image-v1",
    "number": LEXER_VERSION,
    "unit": "scoped-unit-v1",
    "profile": "document-profile-v6-source-year",
    "reconstruction": RECONSTRUCTION_VERSION,
    "materialization": "fiscal-fact-v3-unparsed-literals",
    "narrative": NARRATIVE_VERSION,
    "adjudication": ADJUDICATION_VERSION,
    "image_grid": IMAGE_GRID_VERSION,
    "fragment_stitch": FRAGMENT_STITCH_VERSION,
    "grid_reconstruction": GRID_RECONSTRUCTION_VERSION,
    "orientation": ORIENTATION_VERSION,
    "source_profile": SOURCE_PROFILE_VERSION,
    "schedule": SCHEDULE_VERSION,
    "effective_projection": "effective-projection-v1-snapshot-only",
}


@dataclass
class FiscalInputResult:
    artifact: object
    raw_context: object
    effective_context: object
    overlay_report: dict


class FiscalInputBuilder:
    @staticmethod
    def canonical_profile(previous=None, **profile):
        from src.services.document_profile_resolver import _kind_from_doc_type

        merged = {**(previous or {}), **profile}
        if "doc_type" in profile and "report_kind" not in profile:
            merged["report_kind"] = profile["doc_type"]
        return {
            "org_name": merged.get("org_name"),
            "fiscal_year": merged.get("fiscal_year"),
            "report_kind": _kind_from_doc_type(merged.get("report_kind") or merged.get("doc_type")),
        }

    @staticmethod
    def config_identity(basis="", **profile):
        basis = re.sub(r":fiscal-input:[0-9a-f]{64}", "", basis)
        canonical_profile = FiscalInputBuilder.canonical_profile(**profile)
        return (
            basis
            + ":fiscal-input:"
            + hashlib.sha256(
                json.dumps(
                    {"versions": VERSIONS, "profile": canonical_profile}, sort_keys=True
                ).encode()
            ).hexdigest()
        )

    def read_native(self, path, *, config_fingerprint="", **profile):
        from src.services.pdf_parser import PDFParser

        return self.reconstruct(
            PDFParser(None).build_parse_artifact(str(path), config_fingerprint=config_fingerprint),
            **profile,
        )

    def read_native_from_handle(self, pdf, **kwargs):
        from src.services.pdf_parser import PDFParser

        return self.reconstruct(PDFParser(None).build_parse_artifact_from_handle(pdf, **kwargs))

    def reconstruct(self, artifact, **profile):
        profile = self.canonical_profile(artifact.reconstruction_profile, **profile)
        result = UnifiedFiscalReconstructor().rebuild(artifact, **profile)
        artifact.reconstruction_profile = dict(profile)
        artifact.logical_cell_candidates = result["candidates"]
        artifact.resolved_logical_cells = result["resolved"]
        artifact.reconstruction_version = result["version"]
        artifact.reconstructed_tables = result["tables"]
        artifact.reconstruction_diagnostics = result["diagnostics"]
        artifact.input_versions = dict(VERSIONS)
        # 已包含版本的产物重复编排必须幂等，人工覆盖不进入原始缓存。
        artifact.config_fingerprint = self.config_identity(artifact.config_fingerprint, **profile)
        artifact.parse_run_uid = hashlib.sha256(
            "|".join(
                (
                    artifact.source_file_hash,
                    artifact.parser_fingerprint,
                    artifact.config_fingerprint,
                    artifact.ocr_model_fingerprint or "",
                )
            ).encode()
        ).hexdigest()[:32]
        artifact.narrative_entries = reconstruct_narrative(
            artifact.text_blocks,
            tables=artifact.physical_fragments or artifact.table_fragments,
        )
        for entry in artifact.narrative_entries:
            for claim in entry["amount_claims"]:
                claim["file_hash"] = artifact.source_file_hash
                for source in claim["sources"]:
                    source["file_hash"] = artifact.source_file_hash
                self._claim_profile(claim, profile)
        return artifact

    @staticmethod
    def _claim_profile(claim, profile):
        claim["report_year"] = profile.get("fiscal_year")
        if claim["amount_year"] is None:
            year = profile.get("fiscal_year")
            claim["amount_year"] = year - 1 if year and claim["period"] == "上年" else year

    def facts(self, artifact, **profile):
        from src.engine.fact_context import _build_fact_context_from_artifact, FactContext

        profile = self.canonical_profile(artifact.reconstruction_profile, **profile)
        self.reconstruct(artifact, **profile)
        context = _build_fact_context_from_artifact(artifact, **profile)
        if context is None:
            context = FactContext(
                [],
                file_hash=artifact.source_file_hash,
                parse_run_uid=artifact.parse_run_uid,
                org_name=profile.get("org_name"),
                fiscal_year=profile.get("fiscal_year"),
                report_kind=profile.get("report_kind"),
                budget_stage={"budget": "年初预算", "final": "决算"}.get(
                    profile.get("report_kind")
                ),
            )
        from src.services.fiscal_source_profile import source_document_profile

        source_profile = source_document_profile(artifact)
        image_profiles = [
            f.extra_dims.get("ocr_provenance", {}).get("context_profile") for f in context.facts
        ]
        # 上下文也消费相同原件身份，避免错误调用参数使已确认事实全部失去准入。
        for field, source_field in (
            ("org_name", "org_name"),
            ("fiscal_year", "report_year"),
            ("report_kind", "report_kind"),
        ):
            value = source_profile.get(source_field)
            if value is None and image_profiles and all(p is not None for p in image_profiles):
                values = {p.get(source_field) for p in image_profiles}
                value = next(iter(values)) if len(values) == 1 else None
            if value is not None:
                setattr(context, field, value)
        context.budget_stage = {"budget": "年初预算", "final": "决算"}.get(context.report_kind)
        context.input_status = "parsed" if context.facts else "no_fiscal_facts"
        context.input_diagnostics = []
        conflicting_instances = {
            i.instance_uid: i for i in artifact.logical_tables if i.identity_conflict
        }
        context.input_diagnostics.extend(
            {
                "instance_uid": uid,
                "status": "table_identity_conflict",
                "reasons": list(instance.conflict_reasons),
            }
            for uid, instance in conflicting_instances.items()
        )
        logical_by_source = {
            record["physical_source_id"]: record for record in artifact.logical_cell_candidates
        }
        for fact in context.facts:
            if fact.instance_uid in conflicting_instances:
                fact.recognition_status = "low_confidence"
                fact.extra_dims["table_identity_status"] = "conflict"
            if not fact.org_name:
                # 缺少画像不等于金额不可辨认；保留未知身份，不能把整表清空或补主体。
                fact.extra_dims["organization_identity_status"] = "unresolved"
            related = [
                logical_by_source.get(ref.get("source_cell_id"))
                for ref in [
                    *fact.extra_dims.get("evidence_refs", []),
                    *fact.extra_dims.get("row_identity_sources", []),
                ]
            ]
            unresolved = [
                record
                for record in related
                if record and record["status"] not in {"resolved", "observed_blank"}
            ]
            if unresolved:
                fact.recognition_status = "low_confidence"
                fact.extra_dims["reconstruction_status"] = unresolved[0]["status"]
            header = str(fact.extra_dims.get("source_header") or "")
            year = re.search(r"(20\d{2})年", header)
            image_profile = fact.extra_dims.get("ocr_provenance", {}).get("context_profile")
            fact.extra_dims["report_year"] = (
                image_profile.get("report_year", image_profile.get("fiscal_year"))
                if image_profile is not None
                else profile.get("fiscal_year")
            )
            fact.extra_dims["amount_year"] = (
                int(year[1])
                if year
                else fact.fiscal_year - 1
                if "上年" in header and fact.fiscal_year
                else image_profile.get("amount_year", fact.fiscal_year)
                if image_profile is not None
                else fact.fiscal_year
            )
            fact.period = (
                "上年"
                if "上年" in header
                else "年末"
                if "年末" in header
                else "年初"
                if "年初结转" in header
                else "本年"
            )
        context.version_token = context.compute_version_token()
        return context

    def finish(self, artifact, *, overlay_records=(), previous_facts=(), **profile):
        from src.engine.fact_context import (
            apply_table_column_mapping_overlays,
            apply_fact_overlays_with_remap,
            parse_overlays_from_records,
        )

        raw = self.facts(artifact, **profile)
        logical = deepcopy(artifact)
        mapping = apply_table_column_mapping_overlays(logical, overlay_records)
        effective = self.facts(logical, **profile) if mapping.get("applied") else deepcopy(raw)
        report = {"status": "not_requested", "column_mapping_overlay": mapping}
        overlays = parse_overlays_from_records(overlay_records)
        if effective is not None and overlays:
            effective, amount_report = apply_fact_overlays_with_remap(
                effective, overlays, previous_facts=previous_facts
            )
            report = {**amount_report, "column_mapping_overlay": mapping}
        return FiscalInputResult(artifact, raw, effective, report)
