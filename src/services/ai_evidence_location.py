"""为历史 AI 结果补充可核验原文定位，不改写分析快照或业务结论。"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from src.services.evidence_guard import evaluate_finding_evidence, iter_findings
from src.services.rule_source_binding import bind_ai_literal_sources, verify_bound_ai_locations
from src.services.ai_page_display import synchronize_ai_page_display
from src.services.text_source_relations import add_text_source_relations


@lru_cache(maxsize=32)
def _locate_cached(path: str, digest: str, findings_json: str, rules_json: str) -> str:
    import pdfplumber

    pdf_path = Path(path)
    # 使用与字符坐标定位器完全一致的文本抽取器，避免偏移来自另一种阅读顺序。
    with pdfplumber.open(pdf_path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    findings = json.loads(findings_json)
    result = {"ai_findings": findings, "rule_findings": json.loads(rules_json)}
    bind_ai_literal_sources(result, page_texts=pages, source_file_hash=digest)
    verify_bound_ai_locations(result, source_pdf_path=path)
    for finding in findings:
        if finding.get("source_binding_status") != "bound":
            continue
        complete, missing, verification = evaluate_finding_evidence(
            finding, document_page_count=len(pages), page_texts=pages,
            source_file_hash=digest, source_pdf_path=path,
        )
        finding["evidence_verification"] = verification
        finding["evidence_missing"] = missing
        if complete:
            if finding.get("evidence_status") == "degraded_missing_evidence":
                # 原文定位通过不等于 AI 判断正确；原有人工候选身份与严重度保留。
                finding["review_kind"] = "manual_review"
            finding["evidence_status"] = "complete"
        synchronize_ai_page_display(finding)
    add_text_source_relations(result, page_texts=pages, source_file_hash=digest)
    return json.dumps({"findings": findings, "relations": result.get("source_overlap_relations", [])}, ensure_ascii=False)


def enrich_ai_evidence_locations(payload: dict, source_pdf_path: Path) -> dict:
    """仅补充返回值；缓存同时绑定原件和分析内容，重新分析/换件不会复用旧框。"""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    findings = [item for item in iter_findings(result) if item.get("source") == "ai"]
    if not findings:
        return payload
    # 同一 finding 可能在兼容分桶与双模式分桶中重复出现，使用统一定位结果。
    by_id = {str(item.get("id") or index): item for index, item in enumerate(findings)}
    digest = hashlib.sha256()
    with source_pdf_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    recovery = json.loads(_locate_cached(
        str(source_pdf_path.resolve()), digest.hexdigest(),
        json.dumps(list(by_id.values()), ensure_ascii=False, sort_keys=True),
        json.dumps(result.get("rule_findings") or [], ensure_ascii=False, sort_keys=True),
    ))
    located = recovery["findings"]
    by_id = {str(item.get("id") or index): item for index, item in enumerate(located)}
    enriched = deepcopy(payload)
    enriched["result"]["source_overlap_relations"] = recovery["relations"]
    ai_findings = (item for item in iter_findings(enriched["result"]) if item.get("source") == "ai")
    for index, item in enumerate(ai_findings):
        resolved = by_id.get(str(item.get("id") or index))
        if resolved is not None:
            item.update(deepcopy(resolved))
    located_count = sum(bool(item.get("bbox")) and item.get("verification_status") == "verified"
                        for item in located)
    if not isinstance(enriched["result"].get("meta"), dict):
        enriched["result"]["meta"] = {}
    enriched["result"]["meta"]["ai_location_recovery"] = {
        "total": len(located), "located": located_count,
        "unresolved": len(located) - located_count, "business_verdict_changed": False,
    }
    return enriched
