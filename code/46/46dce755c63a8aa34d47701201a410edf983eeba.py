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

GRID_RECONSTRUCTION_VERSION = "image-grid-logical-v8-source-headless-reference"
PREFIX = "image-logical-"
UNIT = re.compile(r"(?:金额单位|单位)\s*[:：]\s*(百万元|亿元|万元|千元|元)")
LABEL = re.compile(r"(?:编码|代码|科目|名称|项目|支出功能分类)")
ORG_DECLARATION = re.compile(r"(?:单位名称|编制单位|部门\s*[（(]\s*单位\s*[）)])\s*[:：]\s*(.+)")


def cover_organization_alias(local, full):
    """仅接受封面明示全名省略行政前缀；部门与下属机构不能按包含关系混同。"""
    local, full = normalize_text(local), normalize_text(full)
    if local == full:
        return True
    if not local or not full.endswith(local):
        return False
    prefix = full[:-len(local)]
    return bool(re.fullmatch(
        r"(?:[\u4e00-\u9fff]{2,7}(?:省|市|自治区|自治州|地区|特别行政区)){1,3}", prefix
    ))


def empty_summary_dimension_on_observed_edge(cell):
    """仅明确汇总行可不依赖空编码；弱墨迹必须全部位于已实测边线带内。"""
    measured = cell.ocr_provenance["physical_grid"]["cell"]
    if measured.get("content_ink_pixels") != 0:
        return False
    edges = measured.get("boundary_measurements", [])
    if not edges:
        return False
    for y, start, end in measured.get("observed_soft_ink_runs_px", []):
        for x in range(start, end):
            on_edge = any(
                (line["axis"] == "horizontal"
                 and abs(y - line["coordinate"]) <= line["thickness"] / 2 + 3
                 and line["start"] <= x <= line["end"])
                or (line["axis"] == "vertical"
                    and abs(x - line["coordinate"]) <= line["thickness"] / 2 + 3
                    and line["start"] <= y <= line["end"])
                for line in edges
            )
            # 扫描表线交点的淡色残影可落在两条实测边线的交会范围；独立的
            # 格内弱编码不能借此解释为空。原始斑点和未识别格仍原样保存。
            near_junction = any(
                (x - v["coordinate"]) ** 2 + (y - h["coordinate"]) ** 2
                <= (v["thickness"] + h["thickness"]) ** 2
                for h in edges if h["axis"] == "horizontal"
                for v in edges if v["axis"] == "vertical"
            )
            if not on_edge and not near_junction:
                return False
    return True


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
    index_rows = {
        row for row, cells in by_row.items() if is_index_header(c["raw_text"] for c in cells)
    }
    paths = {
        col: sorted(
            [
                c
                for c in candidates
                if c["row_idx"] < depth
                and c["row_idx"] not in index_rows
                and c["col_idx"] <= col < c["col_idx"] + c["col_span"]
            ],
            key=lambda c: c["row_idx"],
        )
        for col in range(column_count)
    }
    return depth, paths


def semantic_mappings(code, paths):
    mappings = []
    measure_columns = set()
    for col, cells in paths.items():
        leaf = normalize_text(cells[-1]["raw_text"]) if cells else ""
        header = "".join(normalize_text(c["raw_text"]) for c in cells)
        if any(
            normalize_text(alias) in {leaf, header}
            for aliases in NINE_TABLE_RULES[code].measure_aliases.values()
            for alias in aliases
        ):
            measure_columns.add(col)
    label_columns = {
        col
        for col, cells in paths.items()
        if col not in measure_columns and any(LABEL.search(c["raw_text"]) for c in cells)
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


def context_profile(groups, title, requested, document=None):
    document = document or {}
    resolved = [g for g in groups if g["status"] == "resolved"]
    values = [g["selected"]["text"] for g in resolved]
    serials = {
        int(match[1] or match[2])
        for value in values
        for match in re.finditer(r"(?:公开\s*0*(\d+)\s*表|表\s*0*(\d+)(?!\d))", value)
    }
    title_text = title["selected"]["text"] if title else ""
    years = {int(y) for y in re.findall(r"(20\d{2})年", title_text)}
    table_year = next(iter(years)) if len(years) == 1 else None
    year = document.get("report_year") or table_year
    kind = "final" if "决算" in title_text else "budget" if "预算" in title_text else None
    declarations = [
        (ORG_DECLARATION.search(g["selected"]["text"]), g)
        for g in resolved
    ]
    names = {m[1].strip() for m, _ in declarations if m}
    organization = next(iter(names)) if len(names) == 1 else None
    organization_refs = [g["selected"] for m, g in declarations if m]
    # 参数只能帮助匹配原件上的同一主体，不能凭参数填入不存在的名字。
    if not names and requested.get("org_name"):
        organization_refs = [
            g["selected"]
            for g in resolved
            if normalize_text(requested["org_name"]) in normalize_text(g["selected"]["text"])
        ]
        if organization_refs:
            organization = requested["org_name"]
    reasons = list(document.get("reasons", []))
    if len(names) > 1:
        reasons.append("explicit_nearby_organization_conflict")
    organization_alias = None
    if organization and document.get("org_name") and organization != document["org_name"]:
        if cover_organization_alias(organization, document["org_name"]):
            organization_alias = {"source_local_name": organization,
                                  "source_cover_name": document["org_name"],
                                  "relation": "explicit_cover_administrative_prefix"}
            organization = document["org_name"]
        else:
            reasons.append("explicit_table_and_cover_organization_conflict")
    if not organization and not names and document.get("org_name"):
        organization = document["org_name"]
        organization_refs = deepcopy(document.get("sources", []))
    if not organization_refs:
        reasons.append("source_organization_not_bound")
    if year is None or not title or title["status"] != "resolved":
        reasons.append("source_year_or_title_not_bound")
    if len(years) > 1:
        reasons.append("explicit_table_amount_year_conflict")
    if kind and document.get("report_kind") and kind != document["report_kind"]:
        reasons.append("explicit_table_and_cover_stage_conflict")
    if kind is None:
        reasons.append("source_report_kind_not_bound")
    mismatches = [
        field
        for field, source in (
            ("fiscal_year", year),
            ("org_name", organization),
            ("report_kind", kind),
        )
        if requested.get(field) and source is not None and requested[field] != source
    ]
    # 辅助画像冲突只做诊断；已绑定的原件身份不被错误参数覆盖或整表拒绝。
    unit_groups = [g for g in groups if UNIT.search(g["selected"]["text"])]
    units = {UNIT.search(g["selected"]["text"])[1] for g in unit_groups}
    unit = next(iter(units)) if len(units) == 1 else None
    if len(units) > 1:
        reasons.append("explicit_nearby_unit_conflict")
    if any(g["status"] != "resolved" for g in unit_groups):
        reasons.append("unit_declaration_requires_regional_read")
    return {
        "org_name": organization if organization_refs else None,
        "organization_alias": organization_alias,
        "fiscal_year": year,
        "report_year": year,
        "amount_year": table_year or year,
        "report_kind": kind,
        "unit": unit,
        "table_serial": next(iter(serials)) if len(serials) == 1 else None,
        "reasons": reasons,
        "requested_profile_mismatches": mismatches,
        "sources": {
            "title": deepcopy(title),
            "organization": deepcopy(organization_refs),
            "unit": deepcopy(unit_groups),
            "document": deepcopy(document),
        },
        "observed_context_text": values,
    }


def project_grid(artifact, grid, requested, *, mapping_overrides=(), document_profile=None):
    aligned = align_grid_fragments(artifact, grid)
    candidates = aligned["cells"]
    groups = nearby_context(artifact, grid)
    code, title = title_identity(groups)
    depth, paths = header_paths(candidates, len(grid.rows), max(map(len, grid.rows)))
    mappings = semantic_mappings(code, paths) if code and paths else []
    profile = context_profile(groups, title, requested, document_profile)
    inheritance = None
    if not mappings and not paths:
        from src.services.fiscal_headless_continuation import inherited_template
        inheritance = inherited_template(artifact, grid, candidates, profile, code, title)
        if inheritance:
            code, title, profile, mappings = (inheritance[k] for k in
                                             ("table_code", "title", "profile", "mappings"))
            depth, paths = 0, {m["source_col_idx"]: [] for m in mappings}
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
    physical_by_position = {(c.row_idx, c.col_idx): c for c in grid.cells}
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
            explicit_total_label = any(
                normalize_text(c["raw_text"]) in {"合计", "总计"}
                and c["status"] == "resolved"
                for c in related[1:] if c["row_idx"] == original.row_idx
            )
            required = [c for c in related if not (
                explicit_total_label and c["row_idx"] == original.row_idx
                and c["col_idx"] in mapping["source_label_cols"]
                and not c["raw_text"].strip() and not c["fragment_groups"]
                and empty_summary_dimension_on_observed_edge(
                    physical_by_position[c["row_idx"], c["col_idx"]])
            )]
            # 明确的合计标签已经建立特殊业务角色。空编码格的淡色边线残留
            # 继续保留未识别状态，不把它补成编码/零，也不借它否定已绑定的合计身份。
            if any(c["status"] not in {"resolved", "observed_blank"} for c in required):
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
        if inheritance and mapping:
            header_units.update(u for ref in mapping["inherited_header_sources"]
                                for u in re.findall(r"百万元|亿元|万元|千元|元", ref["quote"]))
        column_unit = next(iter(header_units)) if len(header_units) == 1 else None
        scope = resolve_unit(cell=token.unit, column=column_unit, table=table.unit_hint)
        if mapping and original.row_idx >= depth:
            from src.services.fiscal_temporal_scope import resolve_temporal_scope
            temporal = resolve_temporal_scope(mapping.get("source_col_name"),
                report_year=profile["report_year"], default_amount_year=profile["amount_year"])
            reasons.extend(temporal.reasons)
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
            "non_text_mark_observations": deepcopy(candidate.get("non_text_mark_observations", [])),
            "qualification": {
                "status": "rejected" if reasons else "qualified",
                "reasons": sorted(set(reasons)),
            },
            "context_profile": deepcopy(profile),
            "identity_fragments": references(related[1:]),
            **({"header_inheritance": {"version": inheritance["version"],
                "sources": deepcopy(mapping["inherited_header_sources"])}}
               if inheritance and mapping else {}),
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
        fragment_uid: [
            deepcopy(m)
            for m in instance.column_mappings
            if m.get("mapping_source") == "human_correction"
        ]
        for instance in artifact.logical_tables
        if instance.instance_uid.startswith(PREFIX)
        for fragment_uid in instance.fragment_uids
    }
    artifact.table_fragments = [
        t for t in artifact.table_fragments if not t.fragment_uid.startswith(PREFIX)
    ]
    artifact.logical_tables = [
        t for t in artifact.logical_tables if not t.instance_uid.startswith(PREFIX)
    ]
    from src.services.fiscal_source_profile import source_document_profile

    document_profile = source_document_profile(artifact)
    candidates, diagnostics = [], []
    for grid in sorted(artifact.source_grids, key=lambda g:(g.page_number, g.bbox or (0,0,0,0))):
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
            document_profile=document_profile,
        )
        candidates.extend(records)
        diagnostics.append(diagnostic)
        if table is not None:
            artifact.table_fragments.append(table)
            artifact.logical_tables.append(instance)
    from src.services.fiscal_image_continuation import stitch_image_instances

    stitch_image_instances(artifact, diagnostics)
    # 逻辑行号由合并视图统一产生；原格/原页行号仍在 physical_source_id 中。
    positions = {}
    for instance in artifact.logical_tables:
        offset = 0
        for uid in instance.fragment_uids:
            table = next((t for t in artifact.table_fragments if t.fragment_uid == uid), None)
            if table is not None and uid.startswith(PREFIX):
                positions[uid.removeprefix(PREFIX)] = (instance.instance_uid, offset)
                offset += len(table.rows)
    for candidate in candidates:
        original = candidate["physical_source_id"].rsplit(":r", 1)[0]
        if original in positions:
            instance_uid, offset = positions[original]
            candidate["instance_uid"] = instance_uid
            candidate["row"] = offset + candidate["row_idx"]
            candidate["logical_cell_id"] = (
                f"{instance_uid}:p{candidate['page']}:r{candidate['row']}:c{candidate['col_idx']}"
            )
    return candidates, diagnostics
