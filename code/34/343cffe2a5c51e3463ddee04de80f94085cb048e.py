"""凭原件身份及几何连接扫描续表；重复印刷的汇总仍逐格保留。"""

from collections import Counter
from copy import deepcopy
import re

from src.services.fiscal_table_rules import normalize_text

CONTINUATION_VERSION = "image-continuation-v2-observed-table-boundaries"
REFERENCE_ROLE = "repeated_summary_reference"
_DETAIL_CODES = {"FIN_02_income", "FIN_03_expenditure", "FIN_05_general_public_expenditure"}


def cell_source_id(cell):
    return (f"{cell.origin_fragment_uid}:r{cell.origin_row_idx}c{cell.origin_col_idx}")


def _profile(table):
    return table.cells[0].ocr_provenance.get("context_profile", {}) if table.cells else {}


def _lanes(table, instance):
    """比较校正图中的实际区间，不比较旋转 PDF 上的横坐标。"""
    grid = table.cells[0].ocr_provenance["physical_grid"]
    x0, _, x1, _ = grid["bbox_px"]
    width = x1 - x0
    result = []
    for mapping in instance.column_mappings:
        col = mapping["source_col_idx"]
        boxes = [c.ocr_provenance["physical_grid"]["cell"]["bbox_px"]
                 for c in table.cells if c.col_idx == col and not c.is_header]
        if not boxes or width <= 0:
            return []
        box = boxes[0]
        result.append((col, mapping["canonical_measure"], tuple(mapping["source_label_cols"]),
                       normalize_text(mapping.get("source_col_name", "")),
                       (box[0] - x0) / width, (box[2] - x0) / width))
    return sorted(result)


def _body_codes(table, instance):
    labels = set(instance.column_mappings[0].get("source_label_cols", []))
    codes = []
    for row in range(table.header_row_count, len(table.rows)):
        text = "".join(normalize_text(c.raw_text) for c in sorted(table.cells, key=lambda c: c.col_idx)
                       if c.row_idx == row and c.col_idx in labels and c.raw_text.strip().isdigit())
        if re.fullmatch(r"\d{3}(?:\d{2}){0,2}", text):
            codes.append(text)
    return codes


def _confirmed_adjacent(left, right, left_instance, right_instance):
    if (left.table_code not in _DETAIL_CODES or left.table_code != right.table_code
            or right.page_number != left.page_number + 1):
        return False
    a, b = _profile(left), _profile(right)
    fields = ("org_name", "report_year", "amount_year", "report_kind", "unit", "table_serial")
    if a.get("reasons") or b.get("reasons") or any(
        a.get(field) is None or a.get(field) != b.get(field) for field in fields
    ):
        return False
    # 同一页出现第二张同号表时不能仅凭相邻页选择。
    la, lb = _lanes(left, left_instance), _lanes(right, right_instance)
    if not la or len(la) != len(lb) or any(
        x[:4] != y[:4] or max(abs(x[4] - y[4]), abs(x[5] - y[5])) > .015
        for x, y in zip(la, lb, strict=True)
    ):
        return False
    ca, cb = _body_codes(left, left_instance), _body_codes(right, right_instance)
    # 原件编码仍原样保存。编码重新从头开始可能是另一实例，不能按同额总计合并。
    return bool(ca and cb and ca[-1].ljust(7, "0") < cb[0].ljust(7, "0"))


def _top_summary(table, instance):
    row = table.header_row_count
    labels = set(instance.column_mappings[0].get("source_label_cols", []))
    cells = [c for c in table.cells if c.row_idx == row and c.col_idx in labels]
    text = "".join(normalize_text(c.raw_text) for c in sorted(cells, key=lambda c: c.col_idx))
    if text not in {"合计", "总计"} or any(
        c.ocr_provenance.get("qualification", {}).get("status") != "qualified"
        for c in cells if c.raw_text.strip()
    ):
        return None
    return row, text


def stitch_image_instances(artifact, diagnostics):
    """只修改已建立的逻辑副本；不删任何原表行或原始金额声明。"""
    fragments = {t.fragment_uid: t for t in artifact.table_fragments}
    images = sorted((i for i in artifact.logical_tables if i.instance_uid.startswith("image-logical-")),
                    key=lambda i: (i.page_start, i.instance_uid))
    counts = Counter((i.page_start, _profile(fragments[i.fragment_uids[0]]).get("table_serial"))
                     for i in images)
    kept = [i for i in artifact.logical_tables if not i.instance_uid.startswith("image-logical-")]
    previous = None
    for instance in images:
        table = fragments[instance.fragment_uids[0]]
        serial = _profile(table).get("table_serial")
        if (previous and counts[(instance.page_start, serial)] == 1
                and counts[(previous.page_end, serial)] == 1):
            tail = fragments[previous.fragment_uids[-1]]
            # 已合并实例的首列语义对每个片段保持一致。
            if _confirmed_adjacent(tail, table, previous, instance):
                first = fragments[previous.fragment_uids[0]]
                canonical, repeated = _top_summary(first, previous), _top_summary(table, instance)
                if canonical and repeated and canonical[1] == repeated[1]:
                    by_col = {c.col_idx: c for c in first.cells if c.row_idx == canonical[0]}
                    for cell in table.cells:
                        if cell.row_idx == repeated[0]:
                            target = by_col.get(cell.col_idx)
                            if target:
                                cell.ocr_provenance["continuation_row"] = {
                                    "version": CONTINUATION_VERSION, "role": REFERENCE_ROLE,
                                    "canonical_source_cell_id": cell_source_id(target),
                                    "instance_uid": previous.instance_uid,
                                }
                    for cell in first.cells:
                        if cell.row_idx == canonical[0]:
                            cell.ocr_provenance["continuation_row"] = {
                                "version": CONTINUATION_VERSION, "role": "total",
                            }
                diagnostics.append({"version": CONTINUATION_VERSION, "status": "confirmed_continuation",
                                    "instance_uid": previous.instance_uid,
                                    "fragment_uid": table.fragment_uid, "page": table.page_number,
                                    "evidence": {k: deepcopy(_profile(table).get(k)) for k in
                                                 ("table_serial", "sources")}})
                previous.fragment_uids.extend(instance.fragment_uids)
                previous.page_end = instance.page_end
                previous.row_count += instance.row_count
                previous.confidence = min(previous.confidence, instance.confidence)
                continue
        kept.append(instance)
        previous = instance
    artifact.logical_tables = kept
    for instance in kept:
        if not instance.instance_uid.startswith("image-logical-"):
            continue
        first = fragments[instance.fragment_uids[0]]
        if _profile(first).get("table_serial") is None:
            continue
        sides = {}
        for name, page in (("start", instance.page_start - 1), ("end", instance.page_end + 1)):
            if page < 1 or page > artifact.page_count:
                sides[name] = {"status": "confirmed", "basis": "original_document_boundary"}
                continue
            adjacent = [t for t in artifact.table_fragments if t.page_number == page
                        and t.table_code and not t.table_code.startswith("UNKNOWN")
                        and t.extraction_method != "page_text_fallback"]
            different = [t for t in adjacent if t.table_code != instance.table_code
                         or (_profile(t).get("table_serial") is not None
                             and _profile(t).get("table_serial") != _profile(first).get("table_serial"))]
            if adjacent and len(different) == len(adjacent):
                sides[name] = {"status": "confirmed", "basis": "adjacent_different_table_identity",
                               "physical_page": page, "fragment_uids": [t.fragment_uid for t in different]}
            else:
                sides[name] = {"status": "requires_adjacent_region_read", "physical_page": page}
        diagnostics.append({"version": CONTINUATION_VERSION, "instance_uid": instance.instance_uid,
                            "kind": "table_extent", "status": "confirmed" if all(
                                s["status"] == "confirmed" for s in sides.values()) else "incomplete",
                            "boundaries": sides})


def is_summary_reference(fact):
    return fact.extra_dims.get("row_role") == REFERENCE_ROLE


def bind_summary_claims(context):
    """保留每个物理汇总格，另建明确引用；值只用于检查原件声明是否一致。"""
    if not any(f.extra_dims.get("ocr_provenance", {}).get("continuation_row") for f in context.facts):
        return
    for fact in context.facts:
        fact.extra_dims.pop("repeated_summary_claims", None)
    context.input_diagnostics = [d for d in context.input_diagnostics or []
                                 if not str(d.get("status", "")).startswith("repeated_summary_")]
    by_source = {ref.get("source_cell_id"): fact for fact in context.facts
                 for ref in fact.extra_dims.get("evidence_refs", [])}
    for fact in context.facts:
        role = fact.extra_dims.get("ocr_provenance", {}).get("continuation_row")
        if not role:
            continue
        fact.extra_dims["row_role"] = role["role"]
        if role["role"] != REFERENCE_ROLE:
            continue
        canonical = by_source.get(role["canonical_source_cell_id"])
        fact.extra_dims["summary_reference"] = {**deepcopy(role),
                                                "canonical_fact_uid": canonical.fact_uid if canonical else None}
        if canonical is None or canonical.indicator != fact.indicator:
            fact.recognition_status = "low_confidence"
            continue
        comparable = (canonical.normalized_value is not None and fact.normalized_value is not None
                      and canonical.unit == fact.unit
                      and canonical.recognition_status in {"unverified", "verified"}
                      and fact.recognition_status in {"unverified", "verified"})
        status = ("consistent" if canonical.normalized_value == fact.normalized_value else "conflict") if comparable else "unresolved"
        claim = {"status": status, "fact_uid": fact.fact_uid, "raw_value": fact.raw_value,
                 "source_quote": fact.extra_dims.get("evidence_refs", [{}])[0].get("quote"),
                 "normalized_value": str(fact.normalized_value) if fact.normalized_value is not None else None,
                 "evidence_refs": deepcopy(fact.extra_dims.get("evidence_refs", []))}
        canonical.extra_dims.setdefault("repeated_summary_claims", []).append(claim)
        fact.extra_dims["summary_reference"]["source_claim_status"] = status
        if status != "consistent":
            context.input_diagnostics.append({"status": "repeated_summary_" + status,
                "canonical_fact_uid": canonical.fact_uid, "reference_fact_uid": fact.fact_uid,
                "instance_uid": canonical.instance_uid})
