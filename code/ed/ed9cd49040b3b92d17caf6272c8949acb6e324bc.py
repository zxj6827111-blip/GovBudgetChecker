"""原图网格与真实图块片段的逻辑副本；不改原格，不按金额配对。"""

from copy import deepcopy
from dataclasses import replace
import re

from src.engine.fiscal_lexeme import lex_numeric, resolve_unit
from src.schemas.parse_artifact import LogicalTableInstance, CellPresence
from src.services.fiscal_row_roles import is_index_header
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text
from src.services.ocr_grid_fragments import (
    align_grid_fragments,
    collect_bound_fragments,
    group_projections,
    intersection,
    area,
)

GRID_RECONSTRUCTION_VERSION = "image-grid-logical-v2-ink-coverage"
PREFIX = "image-logical-"
UNIT = re.compile(r"(?:金额单位|单位)\s*[:：]\s*(百万元|亿元|万元|千元|元)")
LABEL = re.compile(r"(?:编码|代码|科目|名称|项目|支出功能分类)")


def selected_fragments(cells):
    return [group["selected"] for cell in cells for group in cell["fragment_groups"]]


def references(cells):
    return [deepcopy(fragment) for fragment in selected_fragments(cells)]


def nearby_context(artifact, grid):
    # 只在同一表区上方取声明；同页其他表的单位/标题不能成为缺省。
    top = min(c.ocr_provenance["physical_grid"]["cell"]["bbox_px"][1] for c in grid.cells)
    extent = grid.cells[0].ocr_provenance["physical_grid"]["bbox_px"]
    return group_projections(
        [
            f
            for f in collect_bound_fragments(artifact, grid)
            if f["grid_bbox"][3] <= top + 2
            and f["grid_bbox"][1] >= top - 420
            and min(f["grid_bbox"][2], extent[2]) > max(f["grid_bbox"][0], extent[0])
        ]
    )


def title_identity(groups):
    options = []
    for group in groups:
        text = normalize_text(group["selected"]["text"])
        for code, rule in NINE_TABLE_RULES.items():
            for alias in rule.aliases:
                alias = normalize_text(alias)
                if text.endswith(alias):
                    options.append((len(alias), code, group))
    if not options:
        return None, None
    size = max(item[0] for item in options)
    best = [item for item in options if item[0] == size]
    if len({item[1] for item in best}) != 1:
        return None, None
    return best[0][1], best[0][2]


def header_paths(candidates, row_count, column_count):
    by_row = {row: [c for c in candidates if c["row_idx"] == row] for row in range(row_count)}
    depth = None
    declared_name_columns = set()
    for row, cells in by_row.items():
        if is_index_header(c["raw_text"] for c in cells):
            continue
        numeric = [
            c
            for c in cells
            if c["col_idx"] > 0 and lex_numeric(c["raw_text"]).kind in {"number", "amount"}
        ]
        labels = [c for c in cells if re.search(r"[\u4e00-\u9fff]", c["raw_text"])]
        numeric_like = any(
            c["col_idx"] > 0
            and re.match(r"^[+−－(（-]?\d", c["raw_text"].strip())
            and not re.search(r"[\u4e00-\u9fff]", c["raw_text"])
            for c in cells
        )
        body_label = any(
            c["col_idx"] in declared_name_columns
            and re.search(r"[\u4e00-\u9fff]", c["raw_text"])
            and not LABEL.search(c["raw_text"])
            and normalize_text(c["raw_text"]) not in {"类", "款", "项", "栏次", "行次"}
            for c in cells
        )
        if (numeric or numeric_like) and labels or body_label:
            depth = row
            break
        declared_name_columns.update(
            c["col_idx"]
            for c in cells
            if re.search(r"名称|项目", c["raw_text"]) and not re.search(r"编码|代码", c["raw_text"])
        )
    if depth is None or not depth:
        return None, {}
    paths = {
        col: sorted(
            [
                c
                for c in candidates
                if c["row_idx"] < depth and c["col_idx"] <= col < c["col_idx"] + c["col_span"]
            ],
            key=lambda c: c["row_idx"],
        )
        for col in range(column_count)
    }
    return depth, paths


def semantic_mappings(code, paths):
    mappings = []
    label_columns = {
        col for col, cells in paths.items() if any(LABEL.search(c["raw_text"]) for c in cells)
    }
    for col, cells in paths.items():
        if col in label_columns:
            continue
        header = "".join(normalize_text(c["raw_text"]) for c in cells)
        leaf = normalize_text(cells[-1]["raw_text"]) if cells else ""
        matches = []
        for measure, aliases in NINE_TABLE_RULES[code].measure_aliases.items():
            for alias in aliases:
                alias = normalize_text(alias)
                if alias == leaf or alias == header or (alias and header.endswith(alias)):
                    matches.append((int(alias == leaf), len(alias), measure))
        if not matches:
            continue
        strength = max((match[0], match[1]) for match in matches)
        winners = {match[2] for match in matches if match[:2] == strength}
        if len(winners) != 1:
            continue
        # 原页横向范围不变；合并的编码三列不会让金额按列序号平移。
        preceding = sorted(c for c in label_columns if c < col)
        if not preceding:
            continue
        last_measure = max((m["source_col_idx"] for m in mappings), default=-1)
        local_labels = [c for c in preceding if c > last_measure]
        if not local_labels and mappings:
            local_labels = mappings[-1]["source_label_cols"]
        mappings.append(
            {
                "source_col_idx": col,
                "canonical_measure": next(iter(winners)),
                "source_col_name": "\n".join(c["raw_text"] for c in cells),
                "source_label_cols": local_labels,
                "confidence": 1.0,
                "source_header_cells": [c["physical_source_id"] for c in cells],
            }
        )
    return mappings


def context_profile(groups, title, requested):
    values = [g["selected"]["text"] for g in groups if g["status"] == "resolved"]
    title_text = title["selected"]["text"] if title else ""
    years = {int(y) for y in re.findall(r"(20\d{2})年", title_text)}
    year = next(iter(years)) if len(years) == 1 else None
    kind = "final" if "决算" in title_text else "budget" if "预算" in title_text else None
    organization = requested.get("org_name")
    organization_refs = [
        g["selected"]
        for g in groups
        if g["status"] == "resolved"
        and organization
        and normalize_text(organization) in normalize_text(g["selected"]["text"])
    ]
    if not organization:
        # 明确的“单位名称：...”声明可以提供主体；不按文件名或科目名称猜主体。
        declarations = [
            (re.search(r"(?:单位名称|编制单位)\s*[:：]\s*(.+)", text), g)
            for text, g in [(g["selected"]["text"], g) for g in groups if g["status"] == "resolved"]
        ]
        names = {m[1].strip() for m, _ in declarations if m}
        if len(names) == 1:
            organization = next(iter(names))
            organization_refs = [g["selected"] for m, g in declarations if m]
    reasons = []
    if not organization_refs:
        reasons.append("source_organization_not_bound")
    if year is None or not title or title["status"] != "resolved":
        reasons.append("source_year_or_title_not_bound")
    if requested.get("fiscal_year") and year and requested["fiscal_year"] != year:
        reasons.append("explicit_source_year_conflicts_with_requested_profile")
    if requested.get("report_kind") and kind != requested["report_kind"]:
        reasons.append("explicit_source_stage_conflicts_with_requested_profile")
    if kind is None:
        reasons.append("source_report_kind_not_bound")
    unit_groups = [g for g in groups if UNIT.search(g["selected"]["text"])]
    units = {UNIT.search(g["selected"]["text"])[1] for g in unit_groups}
    unit = next(iter(units)) if len(units) == 1 else None
    if len(units) > 1:
        reasons.append("explicit_nearby_unit_conflict")
    if any(g["status"] != "resolved" for g in unit_groups):
        reasons.append("unit_declaration_requires_regional_read")
    return {
        "org_name": organization if organization_refs else None,
        "fiscal_year": year,
        "report_kind": kind,
        "unit": unit,
        "reasons": reasons,
        "sources": {
            "title": deepcopy(title),
            "organization": deepcopy(organization_refs),
            "unit": deepcopy(unit_groups),
        },
        "observed_context_text": values,
    }


def project_grid(artifact, grid, requested, *, mapping_overrides=()):
    aligned = align_grid_fragments(artifact, grid)
    candidates = aligned["cells"]
    groups = nearby_context(artifact, grid)
    code, title = title_identity(groups)
    depth, paths = header_paths(candidates, len(grid.rows), max(map(len, grid.rows)))
    mappings = semantic_mappings(code, paths) if code and paths else []
    automatic = deepcopy(mappings)
    for override in mapping_overrides:
        col = override["source_col_idx"]
        auto = next((m for m in automatic if m["source_col_idx"] == col), {})
        replacement = {**auto, **deepcopy(override)}
        mappings = [
            m
            for m in mappings
            if m["source_col_idx"] != col
            and m["canonical_measure"] != replacement["canonical_measure"]
        ]
        mappings.append(replacement)
    profile = context_profile(groups, title, requested)
    diagnostic = {
        "physical_grid_uid": grid.fragment_uid,
        "version": GRID_RECONSTRUCTION_VERSION,
        "table_code": code,
        "header_depth": depth,
        "mappings": deepcopy(mappings),
        "profile": profile,
        "unowned_fragments": aligned["unowned_fragments"],
        "status": "logical_projection"
        if code and mappings
        else "semantic_identity_requires_regional_read",
    }
    for candidate in candidates:
        candidate.update(
            {
                "logical_cell_id": f"{PREFIX}{candidate['physical_source_id']}",
                "instance_uid": PREFIX + grid.fragment_uid,
                "page": grid.page_number,
                "row": candidate["row_idx"],
                "role": "header" if depth and candidate["row_idx"] < depth else "body",
            }
        )
    if not code or not mappings:
        return None, None, candidates, diagnostic
    table = deepcopy(grid)
    table.fragment_uid = PREFIX + grid.fragment_uid
    table.table_code = code
    table.title = title["selected"]["text"]
    table.header_row_count = depth
    table.extraction_method = "ocr"
    table.confidence = 1.0
    table.unit_hint = profile["unit"]
    by_position = {(c["row_idx"], c["col_idx"]): c for c in candidates}
    map_by_col = {m["source_col_idx"]: m for m in mappings}
    cells = []
    for original in grid.cells:
        candidate = by_position[original.row_idx, original.col_idx]
        mapping = map_by_col.get(original.col_idx)
        related = [candidate]
        reasons = list(profile["reasons"])
        if mapping and original.row_idx >= depth:
            related += paths[original.col_idx]
            related += [
                c
                for c in candidates
                if c["row_idx"] == original.row_idx and c["col_idx"] in mapping["source_label_cols"]
            ]
            # 只有原图已确认白格才可作为缺省的空编码，仍不制造金额 0。
            if any(c["status"] not in {"resolved", "observed_blank"} for c in related):
                reasons.append("amount_or_business_identity_requires_regional_read")
            label_text = [c["raw_text"] for c in related[1:] if c["row_idx"] == original.row_idx]
            if not any(re.search(r"[\u4e00-\u9fff]", text) for text in label_text):
                reasons.append("business_row_label_not_bound")
        elif candidate["status"] not in {"resolved", "observed_blank"}:
            reasons.append("source_cell_requires_regional_read")
        token = lex_numeric(candidate["raw_text"])
        header_units = {
            u
            for c in paths.get(original.col_idx, [])
            for u in re.findall(r"百万元|亿元|万元|千元|元", c["raw_text"])
        }
        column_unit = next(iter(header_units)) if len(header_units) == 1 else None
        scope = resolve_unit(cell=token.unit, column=column_unit, table=table.unit_hint)
        if mapping and original.row_idx >= depth:
            if scope.unit is None or scope.conflicts or len(header_units) > 1:
                reasons.append("amount_unit_missing_or_conflicting")
            if token.kind not in {
                "number",
                "amount",
                "blank",
                "dash",
                "not_applicable",
                "placeholder",
            }:
                reasons.append("amount_literal_requires_regional_read")
        selected = selected_fragments(related)
        provenance = {
            "version": GRID_RECONSTRUCTION_VERSION,
            "physical_grid": deepcopy(original.ocr_provenance["physical_grid"]),
            "fragment_groups": deepcopy(candidate["fragment_groups"]),
            "qualification": {
                "status": "rejected" if reasons else "qualified",
                "reasons": sorted(set(reasons)),
            },
            "context_profile": deepcopy(profile),
            "identity_fragments": references(related[1:]),
        }
        cell = replace(
            original,
            raw_text=candidate["raw_text"],
            normalized_text=candidate["raw_text"],
            numeric_value=float(token.value) if token.kind in {"number", "amount"} else None,
            is_header=original.row_idx < depth,
            unit_hint=column_unit or table.unit_hint,
            extraction_method="ocr",
            confidence=min((f["score"] for f in selected), default=0),
            presence=CellPresence.UNRECOGNIZED
            if candidate["status"] == "requires_regional_read"
            else CellPresence.detect(candidate["raw_text"]),
            origin_fragment_uid=grid.fragment_uid,
            origin_row_idx=original.row_idx,
            origin_col_idx=original.col_idx,
            ocr_provenance=provenance,
        )
        cells.append(cell)
        table.rows[original.row_idx][original.col_idx] = cell.raw_text
        candidate["semantic_qualification"] = deepcopy(provenance["qualification"])
    table.cells = cells
    instance = LogicalTableInstance(
        instance_uid=table.fragment_uid,
        table_code=code,
        title=table.title,
        fragment_uids=[table.fragment_uid],
        page_start=grid.page_number,
        page_end=grid.page_number,
        org_name=profile["org_name"],
        fiscal_year=profile["fiscal_year"],
        row_count=len(table.rows),
        confidence=table.confidence,
        column_mappings=mappings,
    )
    return table, instance, candidates, diagnostic


def rebuild_image_tables(artifact, **profile):
    """刷新可再生逻辑投影；source_grids 和全部原始响应始终只读。"""
    overlays = {
        instance.instance_uid: [
            deepcopy(m)
            for m in instance.column_mappings
            if m.get("mapping_source") == "human_correction"
        ]
        for instance in artifact.logical_tables
        if instance.instance_uid.startswith(PREFIX)
    }
    artifact.table_fragments = [
        t for t in artifact.table_fragments if not t.fragment_uid.startswith(PREFIX)
    ]
    artifact.logical_tables = [
        t for t in artifact.logical_tables if not t.instance_uid.startswith(PREFIX)
    ]
    candidates, diagnostics = [], []
    for grid in artifact.source_grids:
        if grid.extraction_method != "image_grid":
            continue
        # 原生表仍走逐字段联合裁定，不能另造一套同区域业务行。
        if any(
            t.extraction_method != "ocr"
            and t.page_number == grid.page_number
            and t.bbox
            and intersection(t.bbox, grid.bbox) / min(area(t.bbox), area(grid.bbox)) >= 0.8
            for t in artifact.table_fragments
        ):
            continue
        table, instance, records, diagnostic = project_grid(
            artifact,
            grid,
            profile,
            mapping_overrides=overlays.get(PREFIX + grid.fragment_uid, ()),
        )
        candidates.extend(records)
        diagnostics.append(diagnostic)
        if table is not None:
            artifact.table_fragments.append(table)
            artifact.logical_tables.append(instance)
    return candidates, diagnostics
