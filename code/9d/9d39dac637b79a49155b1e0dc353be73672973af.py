"""把多个保真图块的真实 OCR 片段归入同一原图物理格；金额不参与配对。"""

from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import json
import re
from statistics import median

from ocr_service.protocol import PROTOCOL_VERSION, valid_bbox, valid_score
from src.services.input_geometry import Affine, raster_dpi
from src.services.ocr_qualification import (
    bound_response,
    page_binding_reasons,
    complete_first_pass_cell,
)

FRAGMENT_STITCH_VERSION = "source-bound-grid-fragments-v9-explicit-image-mark-roles"


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def same_visible_region(a, b):
    small = min(area(a), area(b))
    return (
        small > 0
        and intersection(a, b) / small >= 0.8
        and abs((a[1] + a[3] - b[1] - b[3]) / 2) <= max(a[3] - a[1], b[3] - b[1]) * 0.45
    )


def collect_bound_fragments(artifact, grid):
    if not grid.cells:
        return []
    physical = grid.cells[0].ocr_provenance["physical_grid"]
    if physical["file_hash"] != artifact.source_file_hash:
        return []
    identity_keys = (
        "version",
        "file_hash",
        "page",
        "image_sha256",
        "parent_image_sha256",
        "pixel_to_pdf",
    )
    if any(
        any(
            cell.ocr_provenance.get("physical_grid", {}).get(key) != physical.get(key)
            for key in identity_keys
        )
        for cell in grid.cells
    ):
        return []
    return _collect_page_fragments(
        artifact,
        grid.page_number,
        physical["parent_image_sha256"],
        Affine(*physical["pixel_to_pdf"]).inverse(),
    )


def collect_page_fragments(artifact, page):
    """正文/封面没有物理网格，也必须核对原页栅格根身份及完整响应。"""
    roots = {
        t.get("page_render", {}).get("original_parent_image_sha256")
        for t in artifact.coordinate_transforms.values()
        if t.get("physical_page") == page and t.get("ocr_response")
    } - {None}
    if len(roots) != 1:
        return []
    # 页坐标直接作为此视图的局部坐标，不虚构物理格或字符框。
    return _collect_page_fragments(artifact, page, next(iter(roots)), Affine())


def _collect_page_fragments(artifact, page, root_hash, pdf_to_grid):
    fragments = []
    for transform_id, transform in artifact.coordinate_transforms.items():
        source = transform.get("ocr_response", {})
        context = transform.get("tile_context", {})
        if (
            source.get("schema_version") != PROTOCOL_VERSION
            or source.get("model_fingerprint") != artifact.ocr_model_fingerprint
            or transform.get("physical_page") != page
        ):
            continue
        if context.get("root_image_sha256", context.get("parent_image_sha256")) != root_hash:
            continue
        stub = SimpleNamespace(
            page_number=page, coordinate_transform_id=transform_id, provenance=source
        )
        if page_binding_reasons(stub, artifact):
            continue
        bound = bound_response(source)
        if not bound:
            continue
        pixel_to_pdf = Affine(*context["pixel_to_pdf"])
        pixel_to_grid = pixel_to_pdf.then(pdf_to_grid)
        tokens = [("block", index, block) for index, block in enumerate(bound.get("blocks", []))]
        for table_index, table in enumerate(bound.get("tables", [])):
            for cell in table.get("cells", []):
                for index, fragment in enumerate(cell.get("fragments", [])):
                    tokens.append(
                        (
                            "table_fragment",
                            [table_index, cell["row_idx"], cell["col_idx"], index],
                            fragment,
                        )
                    )
        for kind, index, token in tokens:
            bbox = valid_bbox(token.get("bbox") or token.get("render_bbox"))
            score = valid_score(token.get("score"))
            text = str(token.get("text") or "")
            if not bbox or score is None or not text.strip():
                continue
            source_ref = {
                "file_hash": artifact.source_file_hash,
                "physical_page": page,
                "transform_id": transform_id,
                "tile_id": context["tile_id"],
                "input_sha256": source["input_sha256"],
                "parent_image_sha256": context["parent_image_sha256"],
                "root_image_sha256": context.get(
                    "root_image_sha256", context["parent_image_sha256"]
                ),
                "raw_prediction_sha256": source["raw_prediction_sha256"],
                "model_fingerprint": source["model_fingerprint"],
                "pipeline_fingerprint": source["pipeline_fingerprint"],
                "kind": kind,
                "index": index,
            }
            fid = (
                "ocr-fragment-"
                + hashlib.sha256(json.dumps(source_ref, sort_keys=True).encode()).hexdigest()[:24]
            )
            reasons = []
            if score < 0.98:
                reasons.append("recognition_score_below_existing_threshold")
            if not complete_first_pass_cell({"fragments": [{"render_bbox": bbox}]}, context):
                reasons.append("fragment_clipped_at_tile_boundary")
            pdf_box = pixel_to_pdf.box(bbox)
            source_region_pending = any(
                intersection(pdf_box, region) > 0
                for region in transform.get("page_recovery", {}).get("unresolved_pdf_regions", [])
            )
            # 页/调度区域仍待恢复，不代表这个真实片段的字面本身无效。
            # 下游必须核对独占物理格的正文墨迹及全部身份字段；不能一相交就整格拒绝。
            fragments.append(
                {
                    "fragment_id": fid,
                    "text": text,
                    "score": score,
                    "render_bbox": bbox,
                    "grid_bbox": pixel_to_grid.box(bbox),
                    "page_bbox": pdf_box,
                    "source": source_ref,
                    "granularity": "fragment",
                    "source_region_pending": source_region_pending,
                    "raster_dpi": raster_dpi(context),
                    "status": "qualified_fragment" if not reasons else "requires_regional_read",
                    "reasons": reasons,
                }
            )
    return fragments


def group_projections(fragments):
    """只按原图位置聚合重复投影；不同格/位置的同额文字始终独立。"""
    parents = list(range(len(fragments)))

    def owner(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for i, a in enumerate(fragments):
        for j in range(i + 1, len(fragments)):
            if (same_visible_region(a["grid_bbox"], fragments[j]["grid_bbox"])
                    or clipped_subspan_reference(a, fragments[j])
                    or clipped_subspan_reference(fragments[j], a)):
                parents[owner(j)] = owner(i)
    groups = defaultdict(list)
    for i, fragment in enumerate(fragments):
        groups[owner(i)].append(fragment)
    results = []
    for options in groups.values():
        eligible = complete_region_projections(options)
        # 更高分辨率且未截断的原图裁剪优先；同一分辨率的合法冲突仍补读。
        # 这是来源质量排序，不是同模型多次返回的独立投票，也不是准确率证明。
        maximum = max((f.get("raster_dpi") or 0 for f in eligible), default=0)
        best_resolution = [f for f in eligible if abs((f.get("raster_dpi") or 0) - maximum) <= 0.1]
        selected = max(best_resolution or options, key=lambda f: f["score"])
        status = (
            "resolved"
            if best_resolution and len({f["text"] for f in best_resolution}) == 1
            else "requires_regional_read"
        )
        results.append(
            {
                "selected": selected,
                "alternates": options,
                "projection_candidates": eligible,
                "coverage_support": [f for f in eligible if f["text"] == selected["text"]],
                "status": status,
                "adjudication_basis": "complete_source_region_then_raster_resolution",
                "accuracy_independently_verified": False,
            }
        )
    return results


def complete_region_projections(options):
    """在同一原始响应内组成真实词序列，再比较对同一可见区的完整覆盖。

    同一行在不同分辨率可能返回一个词或两个相邻词。不能让更高分辨率的
    半句替代完整句，也不能把不同请求的零散词拼成一条并不存在的 OCR 返回。
    合成逻辑视图显式保存 members，覆盖判据继续逐个真实框计算。
    """
    eligible = [f for f in options if f["status"] == "qualified_fragment"]
    if not eligible:
        return []
    by_response = defaultdict(list)
    for index, fragment in enumerate(eligible):
        source = fragment.get("source", {})
        key = (source.get("input_sha256"), source.get("raw_prediction_sha256"))
        if not all(key):
            key = ("unbound_control", index)
        by_response[key].append(fragment)
    candidates = list(eligible)
    for parts in by_response.values():
        unique = {}
        for part in parts:
            unique.setdefault((part["text"], tuple(part["grid_bbox"])), part)
        parts = sorted(unique.values(), key=lambda f: f["grid_bbox"][0])
        if len(parts) < 2 or any(re.search(r"\d", f["text"]) for f in parts):
            continue
        # 只组合同一基线的相邻真实片段。重叠的两份完整读数是替代候选，不能相加。
        if any(
            b["grid_bbox"][0] - a["grid_bbox"][2] > min(a["grid_bbox"][3] - a["grid_bbox"][1], b["grid_bbox"][3] - b["grid_bbox"][1]) * .35
            or a["grid_bbox"][2] - b["grid_bbox"][0] > min(a["grid_bbox"][2] - a["grid_bbox"][0], b["grid_bbox"][2] - b["grid_bbox"][0]) * .25
            or abs((a["grid_bbox"][1] + a["grid_bbox"][3] - b["grid_bbox"][1] - b["grid_bbox"][3]) / 2) > min(a["grid_bbox"][3] - a["grid_bbox"][1], b["grid_bbox"][3] - b["grid_bbox"][1]) * .35
            for a, b in zip(parts, parts[1:], strict=False)
        ):
            continue
        combined = deepcopy(parts[0])
        combined.update(text="".join(p["text"] for p in parts),
                        granularity="fragment_sequence", members=deepcopy(parts),
                        score=min(p["score"] for p in parts))
        for name in ("grid_bbox", "render_bbox", "page_bbox"):
            if all(p.get(name) for p in parts):
                combined[name] = [min(p[name][0] for p in parts), min(p[name][1] for p in parts),
                                  max(p[name][2] for p in parts), max(p[name][3] for p in parts)]
        source = combined.setdefault("source", {})
        source["kind"] = "derived_fragment_sequence"
        source.pop("index", None)
        source["member_sources"] = [deepcopy(p.get("source", {})) for p in parts]
        combined["fragment_id"] = "ocr-sequence-" + hashlib.sha256(json.dumps(
            [(p.get("fragment_id"), p["text"], p["grid_bbox"]) for p in parts], sort_keys=True
        ).encode()).hexdigest()[:24]
        candidates.append(combined)
    extent = [min(f["grid_bbox"][0] for f in eligible), min(f["grid_bbox"][1] for f in eligible),
              max(f["grid_bbox"][2] for f in eligible), max(f["grid_bbox"][3] for f in eligible)]
    complete = []
    for candidate in candidates:
        box = candidate["grid_bbox"]
        padding = (box[3] - box[1]) / 4
        if (box[0] <= extent[0] + padding and box[1] <= extent[1] + padding
                and box[2] >= extent[2] - padding and box[3] >= extent[3] - padding):
            complete.append(candidate)
    return complete


def clipped_subspan_reference(partial, complete):
    """截在图块边界的汉字尾词只引用覆盖它的完整片段，不再次拼接。

    检测框的外扩边缘可能比完整词框多几像素，面积交并不足以判定词粒度。
    此关系同时要求已知切块截断、完整字面包含、同一行和近似包含的原图框。
    不拆造字符框，也不据金额相等建立关系；完整片段仍须通过独立墨迹覆盖。
    """
    if ("fragment_clipped_at_tile_boundary" not in partial.get("reasons", [])
            or complete.get("status") != "qualified_fragment"
            or not re.fullmatch(r"[\u4e00-\u9fff]+", partial["text"])
            or partial["text"] not in complete["text"]
            or partial["text"] == complete["text"]):
        return False
    a, b = partial["grid_bbox"], complete["grid_bbox"]
    padding = min(a[3] - a[1], b[3] - b[1]) / 4
    return (b[0] <= (a[0] + a[2]) / 2 <= b[2]
            and abs((a[1] + a[3] - b[1] - b[3]) / 2) <= padding
            and b[0] - padding <= a[0] < a[2] <= b[2] + padding
            and b[1] - padding <= a[1] < a[3] <= b[3] + padding)


def compose_literal(groups):
    ordered = sorted(
        groups, key=lambda g: (g["selected"]["grid_bbox"][1], g["selected"]["grid_bbox"][0])
    )
    lines = []
    for group in ordered:
        box = group["selected"]["grid_bbox"]
        if (
            not lines
            or abs(box[1] - lines[-1][0]["selected"]["grid_bbox"][1]) > (box[3] - box[1]) * 0.35
        ):
            lines.append([])
        lines[-1].append(group)
    texts = []
    for line in lines:
        line.sort(key=lambda g: g["selected"]["grid_bbox"][0])
        text = ""
        for group in line:
            value = group["selected"]["text"]
            # 数字片段之间的间距可能代表两笔数；不能删除后制造一个合法金额。
            if text and (re.search(r"\d", text) or re.search(r"\d", value)):
                text += " "
            text += value
        texts.append(text)
    return "\n".join(texts)


def align_grid_fragments(artifact, grid):
    assigned = defaultdict(list)
    unowned = []
    for fragment in collect_bound_fragments(artifact, grid):
        box = fragment["grid_bbox"]
        matches = [
            cell
            for cell in grid.cells
            if intersection(box, cell.ocr_provenance["physical_grid"]["cell"]["bbox_px"])
            / area(box)
            >= 0.8
        ]
        if len(matches) == 1:
            assigned[matches[0].row_idx, matches[0].col_idx].append(fragment)
        else:
            unowned.append({**fragment, "reason": "not_uniquely_owned_by_one_physical_cell"})
    candidates = []
    for cell in grid.cells:
        groups = group_projections(assigned[cell.row_idx, cell.col_idx])
        blank = cell.ocr_provenance["physical_grid"]["cell"]["image_blank_verified"]
        image_marks = []
        missing_ink = uncovered_ink_pixels(cell, groups, image_marks=image_marks)
        status = (
            "resolved"
            if groups
            and not blank
            and not missing_ink
            and all(g["status"] == "resolved" for g in groups)
            else "observed_blank"
            if blank and not groups
            else "requires_regional_read"
        )
        candidates.append(
            {
                "physical_source_id": f"{grid.fragment_uid}:r{cell.row_idx}c{cell.col_idx}",
                "row_idx": cell.row_idx,
                "col_idx": cell.col_idx,
                "row_span": cell.row_span,
                "col_span": cell.col_span,
                "bbox": cell.bbox,
                "raw_text": compose_literal(groups),
                "status": status,
                "fragment_groups": groups,
                "source_relation": "image_grid_cell",
                "version": FRAGMENT_STITCH_VERSION,
                "uncovered_ink_pixels": missing_ink,
                "non_text_mark_observations": image_marks,
            }
        )
    return {"cells": candidates, "unowned_fragments": unowned, "version": FRAGMENT_STITCH_VERSION}


def uncovered_ink_pixels(cell, groups, *, image_marks=None):
    """以真实词框覆盖原图像素游程；不因一个大框命中中心就认为读全。"""
    missing_pixels = set()
    # 同一原页、同一字面的真实框可共同证明几何覆盖；不把抖动较窄的高 DPI
    # 检测框当成唯一边界。它们仍是同模型稳定性证据，不是独立准确率投票。
    boxes = [part["grid_bbox"] for group in groups
             for support in group.get("coverage_support", [group["selected"]])
             for part in support.get("members", [support])]
    measured = cell.ocr_provenance["physical_grid"]["cell"]
    for y, start, end in measured.get(
        "content_ink_runs_px", measured.get("observed_ink_runs_px", [])
    ):
        for x in range(start, end):
            if not any(box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1 for box in boxes):
                missing_pixels.add((x, y))
    if not boxes or not groups or any(g["status"] != "resolved" for g in groups):
        return len(missing_pixels)
    # 已识别文字以外的墨迹也需要布局角色。只登记远离已确认文字基线的
    # 微小栅格斑点；基线附近的小数点、负号、百分号、括号和漏字仍须补读。
    # 不改变 0.98、原图像素或金额字面。每个斑点保留原图像素位置，供原件验收。
    height = median(box[3] - box[1] for box in boxes)
    remaining = set(missing_pixels)
    while remaining:
        seed = remaining.pop()
        component, queue = {seed}, [seed]
        while queue:
            x, y = queue.pop()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                point = (x + dx, y + dy)
                if point in remaining:
                    remaining.remove(point)
                    component.add(point)
                    queue.append(point)
        left, top = min(p[0] for p in component), min(p[1] for p in component)
        right, bottom = max(p[0] for p in component) + 1, max(p[1] for p in component) + 1
        tiny = max(right - left, bottom - top) <= height / 10
        outside_baselines = all(
            (vertical := max(box[1] - bottom, top - box[3])) > height * .08
            and (vertical > height or max(box[0] - right, left - box[2]) > height)
            for box in boxes
        )
        if tiny and outside_baselines:
            missing_pixels.difference_update(component)
            if image_marks is not None:
                image_marks.append({"role": "off_baseline_raster_mark", "bbox_px": [left, top, right, bottom],
                                    "original_ink_pixels": [list(p) for p in sorted(component)],
                                    "basis": "tiny_component_outside_bound_text_baselines",
                                    "accuracy_independently_verified": False})
    return len(missing_pixels)


def grid_recovery_regions(artifact, grids, *, requested_profile=None):
    """独立物理格触发补读；首轮没有片段也必须提出请求。"""
    from src.engine.fiscal_lexeme import lex_numeric
    from src.services.ocr_grid_reconstruction import (
        header_paths,
        semantic_mappings,
        title_identity,
        nearby_context,
    )

    requests = []
    for grid in grids:
        physical = grid.cells[0].ocr_provenance["physical_grid"]
        width, height = physical.get(
            "image_size_px", [physical["bbox_px"][2], physical["bbox_px"][3]]
        )
        originals = {(cell.row_idx, cell.col_idx): cell for cell in grid.cells}
        aligned = align_grid_fragments(artifact, grid)["cells"]
        context_groups = nearby_context(artifact, grid)
        code, title = title_identity(context_groups)
        for group in context_groups:
            fragment = group["selected"]
            text = fragment["text"]
            reasons = []
            role = None
            if (
                re.search(r"预算|决算", text)
                and "表" in text
                and (code is None or group is title and group["status"] != "resolved")
            ):
                role = "table_title"
                reasons.append("source_table_title_not_semantically_resolved")
            organization_declaration = bool(re.search(r"单位名称|编制单位", text))
            if organization_declaration and group["status"] != "resolved":
                role = "organization_declaration"
                reasons.append("source_organization_declaration_incomplete")
            if (
                not organization_declaration
                and re.search(r"(?:金额单位|单位)\s*[:：]", text)
                and (
                    group["status"] != "resolved"
                    or not re.search(
                        r"(?:金额单位|单位)\s*[:：]\s*(百万元|亿元|万元|千元|元)", text
                    )
                )
            ):
                role = "unit_declaration"
                reasons.append("source_unit_declaration_not_semantically_resolved")
            if reasons:
                x0, y0, x1, y1 = fragment["grid_bbox"]
                requests.append(
                    {
                        "region_key": grid.fragment_uid + ":" + role,
                        "box": [
                            max(0, int(x0) - 12),
                            max(0, int(y0) - 12),
                            min(width, int(x1) + 13),
                            min(height, int(y1) + 13),
                        ],
                        "reasons": reasons,
                    }
                )
        depth, paths = header_paths(aligned, len(grid.rows), max(map(len, grid.rows)))
        amount_columns = (
            {m["source_col_idx"] for m in semantic_mappings(code, paths)}
            if code and paths
            else set()
        )
        for candidate in aligned:
            text = candidate["raw_text"]
            reasons = []
            if candidate["status"] == "requires_regional_read":
                reasons.append("physical_cell_incomplete_or_conflicting")
            token = lex_numeric(text)
            if (
                token.kind == "invalid"
                and any(c.isdigit() for c in text)
                and not re.search(r"[\u4e00-\u9fff]", text)
            ):
                reasons.append("illegal_numeric_literal_in_physical_cell")
            if (
                candidate["col_idx"] in amount_columns
                and depth is not None
                and candidate["row_idx"] >= depth
                and token.kind
                not in {
                    "number",
                    "amount",
                    "blank",
                    "dash",
                    "not_applicable",
                    "placeholder",
                }
            ):
                reasons.append("typed_amount_lane_contains_non_amount_literal")
            if not reasons:
                continue
            source = originals[candidate["row_idx"], candidate["col_idx"]]
            x0, y0, x1, y1 = source.ocr_provenance["physical_grid"]["cell"]["bbox_px"]
            requests.append(
                {
                    "region_key": candidate["physical_source_id"],
                    "box": [
                        max(0, int(x0) - 4),
                        max(0, int(y0) - 4),
                        min(width, int(x1) + 5),
                        min(height, int(y1) + 5),
                    ],
                    "reasons": reasons,
                }
            )
    return requests
