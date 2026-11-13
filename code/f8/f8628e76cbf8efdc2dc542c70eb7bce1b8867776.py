"""统一输入编排入口；原始读取、逻辑重建、事实和校正使用同一产物。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
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

INPUT_VERSION = "fiscal-input-v2-authoritative-empty"
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
}


@dataclass
class FiscalInputResult:
    artifact: object
    raw_context: object
    effective_context: object
    overlay_report: dict


class FiscalInputBuilder:
    def read_native(self, path):
        from src.services.pdf_parser import PDFParser

        return self.reconstruct(PDFParser(None).build_parse_artifact(str(path)))

    def read_native_from_handle(self, pdf, **kwargs):
        from src.services.pdf_parser import PDFParser

        return self.reconstruct(PDFParser(None).build_parse_artifact_from_handle(pdf, **kwargs))

    def reconstruct(self, artifact):
        result = UnifiedFiscalReconstructor().rebuild(artifact)
        artifact.logical_cell_candidates = result["candidates"]
        artifact.resolved_logical_cells = result["resolved"]
        artifact.reconstruction_version = result["version"]
        artifact.input_versions = dict(VERSIONS)
        # 已包含版本的产物重复编排必须幂等，人工覆盖不进入原始缓存。
        basis = re.sub(r":fiscal-input:[0-9a-f]{64}", "", artifact.config_fingerprint)
        artifact.config_fingerprint = (
            basis
            + ":fiscal-input:"
            + hashlib.sha256(json.dumps(VERSIONS, sort_keys=True).encode()).hexdigest()
        )
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
        artifact.narrative_entries = reconstruct_narrative(artifact.text_blocks)
        return artifact

    def facts(self, artifact, **profile):
        from src.engine.fact_context import _build_fact_context_from_artifact, FactContext

        self.reconstruct(artifact)
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
        context.input_status = "parsed" if context.facts else "no_fiscal_facts"
        context.input_diagnostics = []
        logical_by_source = {
            record["physical_source_id"]: record for record in artifact.logical_cell_candidates
        }
        for fact in context.facts:
            if not fact.org_name:
                # 缺少画像不等于金额不可辨认；保留未知身份，不能把整表清空或补主体。
                fact.extra_dims["organization_identity_status"] = "unresolved"
            related = [
                logical_by_source.get(ref.get("source_cell_id"))
                for ref in fact.extra_dims.get("evidence_refs", [])
            ]
            unresolved = [record for record in related if record and record["status"] != "resolved"]
            if unresolved:
                fact.recognition_status = "low_confidence"
                fact.extra_dims["reconstruction_status"] = unresolved[0]["status"]
            header = str(fact.extra_dims.get("source_header") or "")
            year = re.search(r"(20\d{2})年", header)
            fact.extra_dims["report_year"] = profile.get("fiscal_year")
            fact.extra_dims["amount_year"] = (
                int(year[1])
                if year
                else fact.fiscal_year - 1
                if "上年" in header and fact.fiscal_year
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
            effective, report = apply_fact_overlays_with_remap(
                effective, overlays, previous_facts=previous_facts
            )
        return FiscalInputResult(artifact, raw, effective, report)


def reconstruct_narrative(blocks):
    """章节是硬边界；跨页只有同一条目才能延续，不借用下节金额或原因。"""
    entries = []
    section = None
    item = None
    ordered = sorted(
        blocks,
        key=lambda b: (b.page_number, b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0),
    )
    lines = []
    for block in ordered:
        if (
            not lines
            or block.page_number != lines[-1][0].page_number
            or not block.bbox
            or not lines[-1][0].bbox
            or abs(block.bbox[1] - lines[-1][0].bbox[1]) > 3
        ):
            lines.append([])
        lines[-1].append(block)
    for line in lines:
        text = "".join(b.text for b in line)
        heading = re.match(r"^(?:第[一二三四五六七八九十]+部分|[一二三四五六七八九十]+、)", text)
        start = re.match(r"^(?:[（(][一二三四五六七八九十]+[）)]|\d+[.、])", text)
        if heading:
            section = text
            item = None
        if start:
            item = text
        sources = [
            {
                "block_uid": b.block_uid,
                "page": b.page_number,
                "bbox": list(b.bbox) if b.bbox else None,
                "text": b.text,
                "granularity": "fragment",
            }
            for b in line
        ]
        entries.append({"section": section, "item": item, "text": text, "sources": sources})
    return entries
