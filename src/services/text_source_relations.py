"""按已核验的原文缺陷位置建立来源对照，不改变正式结论或工作流集合。"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import re

from src.services.rule_source_binding import _indexed_text


def _verified_spans(finding, pages, file_hash):
    for evidence in finding.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        if (evidence.get("verification_status") != "verified"
                or evidence.get("location_level") != "exact_span"
                or evidence.get("file_hash") != file_hash):
            continue
        span = evidence.get("source_span")
        page = evidence.get("physical_page")
        if not isinstance(page, int) or not 1 <= page <= len(pages):
            continue
        if not isinstance(span, list) or len(span) != 2 or not all(isinstance(x, int) for x in span):
            continue
        start, end = span
        text = pages[page - 1]
        if not 0 <= start < end <= len(text):
            continue
        # 来源 ID 和偏移也必须对应此次原件，不能相信历史标记本身。
        quote = evidence.get("quote") or evidence.get("text") or ""
        normalized, offsets = _indexed_text(text[start:end])
        if not normalized or normalized != _indexed_text(quote)[0]:
            continue
        yield page, start, normalized, offsets


def add_text_source_relations(result, *, page_texts, source_file_hash):
    """同一 PDF、页、字符位置、重复词才算共同发现；无定位则不推测。"""
    if not re.fullmatch(r"[0-9a-f]{64}", source_file_hash or ""):
        return
    rule_occurrences = defaultdict(set)
    for finding in result.get("rule_findings") or []:
        if not isinstance(finding, dict) or finding.get("rule_id") != "V33-113":
            continue
        match = re.search(r"重复用词：([^，。]+)", finding.get("message") or "")
        token = _indexed_text(match.group(1))[0] if match else ""
        if len(token) != 2 or token[0] != token[1]:
            continue
        for page, start, normalized, offsets in _verified_spans(finding, page_texts, source_file_hash):
            if normalized.count(token) != 1:
                continue
            pos = normalized.index(token)
            key = (page, start + offsets[pos], start + offsets[pos + 1] + 1, token)
            rule_occurrences[key].add(finding["id"])
    ai_occurrences = defaultdict(set)
    for finding in result.get("ai_findings") or []:
        if not isinstance(finding, dict) or "duplicate_text" not in (finding.get("tags") or []):
            continue
        for page, start, normalized, offsets in _verified_spans(finding, page_texts, source_file_hash):
            for key in rule_occurrences:
                target_page, token_start, token_end, token = key
                if page != target_page:
                    continue
                for match in re.finditer(re.escape(token), normalized):
                    pos = match.start()
                    if start + offsets[pos] == token_start and start + offsets[pos + 1] + 1 == token_end:
                        ai_occurrences[key].add(finding["id"])
    relations = []
    for key in sorted(rule_occurrences.keys() & ai_occurrences.keys()):
        page, start, end, token = key
        identity = f"{source_file_hash}:p{page}:s{start}:e{end}:{token}"
        relations.append({
            "relation_id": "text-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            "status": "same_occurrence", "kind": "duplicate_word",
            "file_hash": source_file_hash, "physical_page": page,
            "source_span": [start, end], "quote": token,
            "ai_issue_ids": sorted(ai_occurrences[key]),
            "rule_issue_ids": sorted(rule_occurrences[key]),
            "business_verdict_changed": False,
        })
    result["source_overlap_relations"] = relations
