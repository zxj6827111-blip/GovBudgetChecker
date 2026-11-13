"""无表头扫描续页只引用相邻已确认车道，不制造本页表头或字符。"""

from copy import deepcopy
import re

from src.engine.fiscal_lexeme import lex_numeric
from src.services.fiscal_table_rules import normalize_text
from src.services.fiscal_image_continuation import _DETAIL_CODES, _body_codes, _lanes, _profile

HEADLESS_VERSION = "headless-continuation-v1-source-number-and-lanes"


def _header_sources(table, mapping, file_hash):
    sources = []
    col = mapping["source_col_idx"]
    for cell in table.cells:
        if cell.is_header and cell.raw_text.strip() and cell.col_idx <= col < cell.col_idx + cell.col_span:
            sources.append({"source_relation": "shared_header_reference", "file_hash": file_hash,
                "physical_page": cell.page_number,
                "source_cell_id": f"{cell.origin_fragment_uid}:r{cell.origin_row_idx}c{cell.origin_col_idx}",
                "quote": cell.raw_text, "bbox": cell.bbox,
                "native_text_sources": deepcopy(cell.native_text_sources),
                "ocr_fragment_sources": [deepcopy(g["selected"])
                    for g in cell.ocr_provenance.get("fragment_groups", [])]})
    if not sources:
        sources = next((deepcopy(c.ocr_provenance.get("header_inheritance", {}).get("sources", []))
                        for c in table.cells if c.col_idx == col), [])
    return sources


def inherited_template(artifact, grid, candidates, local_profile, code, title):
    """印刷表号、身份及原图横向区间共同确认；相同金额不参与判断。"""
    serial = local_profile.get("table_serial")
    if serial is None or any(reason not in {
        "source_year_or_title_not_bound", "source_report_kind_not_bound", "source_organization_not_bound"
    } for reason in local_profile["reasons"]):
        return None
    preceding = [t for t in artifact.table_fragments if t.extraction_method == "ocr"
                 and t.page_number == grid.page_number - 1 and t.table_code in _DETAIL_CODES
                 and _profile(t).get("table_serial") == serial]
    # 一页有两张同号表时不能按任意对象顺序选其中一张。
    if len(preceding) != 1 or sum(g.page_number == grid.page_number for g in artifact.source_grids) != 1:
        return None
    previous = preceding[0]
    prior = _profile(previous)
    instance = next((i for i in artifact.logical_tables if previous.fragment_uid in i.fragment_uids), None)
    if not instance or prior.get("reasons") or (code is not None and code != previous.table_code):
        return None
    if title is not None and title["status"] != "resolved":
        return None
    for field in ("org_name", "report_year", "report_kind", "unit"):
        if local_profile.get(field) is not None and local_profile[field] != prior.get(field):
            return None
    if title is not None and re.search(r"20\d{2}年", title["selected"]["text"]):
        if local_profile.get("amount_year") != prior.get("amount_year"):
            return None
    physical = grid.cells[0].ocr_provenance["physical_grid"]
    x0, _, x1, _ = physical["bbox_px"]
    if x1 <= x0:
        return None
    width = x1 - x0
    columns = {}
    for cell in grid.cells:
        box = cell.ocr_provenance["physical_grid"]["cell"]["bbox_px"]
        interval = ((box[0] - x0) / width, (box[2] - x0) / width)
        columns.setdefault(cell.col_idx, interval)
    mappings, used = [], set()
    for old_col, measure, _, header, a, b in _lanes(previous, instance):
        possible = [col for col, box in columns.items()
                    if max(abs(a-box[0]), abs(b-box[1])) <= .015]
        if len(possible) != 1 or possible[0] in used:
            return None
        col = possible[0]
        labels = sorted(c for c in columns if columns[c][1] <= columns[col][0] + .015 and c not in used)
        old = next(m for m in instance.column_mappings if m["source_col_idx"] == old_col)
        sources = _header_sources(previous, old, artifact.source_file_hash)
        if not labels or not sources or any(not (s["native_text_sources"] or s["ocr_fragment_sources"]) for s in sources):
            return None
        mappings.append({**deepcopy(old), "source_col_idx": col, "source_label_cols": labels,
                         "source_col_name": old.get("source_col_name", header),
                         "mapping_source": "confirmed_headless_continuation",
                         "inherited_header_sources": sources})
        used.add(col)
    if not mappings:
        return None
    # 编码顺序及金额类型验证真实业务边界；空金额行本身仍保留。
    labels = set(mappings[0]["source_label_cols"])
    codes, numeric = [], False
    for row in sorted({c["row_idx"] for c in candidates}):
        row_cells = [c for c in candidates if c["row_idx"] == row]
        code_text = "".join(normalize_text(c["raw_text"]) for c in sorted(row_cells, key=lambda c:c["col_idx"])
                            if c["col_idx"] in labels and c["raw_text"].strip().isdigit()
                            and c["status"] == "resolved")
        if re.fullmatch(r"\d{3}(?:\d{2}){0,2}", code_text):
            codes.append(code_text)
        for cell in row_cells:
            if cell["col_idx"] in used:
                token = lex_numeric(cell["raw_text"])
                if token.kind not in {"number", "amount", "blank", "dash", "not_applicable", "placeholder"}:
                    return None
                numeric |= token.kind in {"number", "amount"}
    before = _body_codes(previous, instance)
    if not before or not codes or not numeric or before[-1].ljust(7,"0") >= codes[0].ljust(7,"0"):
        return None
    profile = deepcopy(local_profile)
    fields = [field for field in ("org_name", "report_year", "fiscal_year", "report_kind", "unit")
              if profile.get(field) is None]
    for field in fields:
        profile[field] = prior.get(field)
    if title is None or not re.search(r"20\d{2}年", title["selected"]["text"]):
        profile["amount_year"] = prior.get("amount_year")
    profile["reasons"] = []
    for field in ("title", "organization", "unit", "document"):
        if not profile["sources"].get(field):
            profile["sources"][field] = deepcopy(prior["sources"].get(field))
    profile["sources"]["continuation"] = {"version": HEADLESS_VERSION,
        "physical_grid_uid": previous.cells[0].origin_fragment_uid,
        "physical_page": previous.page_number, "fields": fields,
        "profile_identity": {field: prior.get(field) for field in
            ("org_name", "report_year", "amount_year", "report_kind", "unit", "table_serial")}}
    return {"version": HEADLESS_VERSION, "table_code": previous.table_code,
            "title": title or deepcopy(prior["sources"]["title"]),
            "profile": profile, "mappings": mappings}
