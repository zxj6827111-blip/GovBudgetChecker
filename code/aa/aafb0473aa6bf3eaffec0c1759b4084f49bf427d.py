"""只从同一原件的明确封面取报告身份；金额年度和局部单位另行解析。"""

from copy import deepcopy
import re
import unicodedata

from src.services.fiscal_business_rows import valid_samples, lines, proofs, compact
from src.services.ocr_grid_fragments import collect_page_fragments, group_projections

SOURCE_PROFILE_VERSION = "source-cover-profile-v4-geometric-name-block"
HEADING = re.compile(
    r"(20\d{2})年(?:度)?(?P<organization>[\u4e00-\u9fff]*?)(?:部门|单位)?(预算|决算)(?:公开)?(?:报告)?$"
)
ORGANIZATION = re.compile(r"(?:单位名称|编制单位|部门[（(]单位[）)])\s*[:：]\s*(.+)")
ORG_END = re.compile(
    r"[\u4e00-\u9fff]{2,}(?:局|委员会|办公室|人民代表大会|政府|中心|学校|院|馆|站|队|所|厅|室|会)$"
)


def cover_lines(artifact, page):
    native = []
    for samples in lines(valid_samples(artifact).get(page, [])):
        text = "".join(s["text"] for s in samples)
        if re.search(r"\ufffd|\x00|\(cid:", text):
            continue
        source = proofs(samples, page)
        native.append(
            {
                "text": text,
                "page": page,
                "bbox": [
                    min(s["bbox"][0] for s in samples),
                    min(s["bbox"][1] for s in samples),
                    max(s["bbox"][2] for s in samples),
                    max(s["bbox"][3] for s in samples),
                ],
                "sources": [{**p, "file_hash": artifact.source_file_hash} for p in source],
                "method": "native_character_indexes",
            }
        )
    visual = []
    for group in group_projections(collect_page_fragments(artifact, page)):
        if group["status"] != "resolved":
            continue
        f = group["selected"]
        visual.append(
            {
                "text": f["text"],
                "page": page,
                "bbox": f["page_bbox"],
                "sources": deepcopy(group),
                "method": "bound_ocr_fragment",
            }
        )
    visual_lines = []
    for item in sorted(visual, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        box = item["bbox"]
        if (
            visual_lines
            and 0 <= box[0] - visual_lines[-1]["bbox"][2] <= 2 * (box[3] - box[1])
            and abs(box[1] - visual_lines[-1]["bbox"][1])
            <= min(box[3] - box[1], visual_lines[-1]["bbox"][3] - visual_lines[-1]["bbox"][1])
            * 0.35
        ):
            line = visual_lines[-1]
            line["text"] += item["text"]
            line["bbox"] = [
                min(line["bbox"][0], box[0]),
                min(line["bbox"][1], box[1]),
                max(line["bbox"][2], box[2]),
                max(line["bbox"][3], box[3]),
            ]
            line["sources"].append(item["sources"])
        else:
            visual_lines.append({**item, "sources": [item["sources"]]})
    visual = visual_lines
    # 保留两个通道的声明，显式不同身份会在下游形成冲突，而非整页覆盖。
    return sorted(native + visual, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def _normalized(text):
    return compact(unicodedata.normalize("NFKC", text))


def _preceding_organization(observed, start):
    """按封面的真实相邻行回接名称，括号里的挂牌名和范围另存。"""
    parts, refs, aliases, scopes = [], [], [], []
    conflicting_names = False
    next_line = observed[start]
    for previous in reversed(observed[max(0, start - 5):start]):
        text = _normalized(previous["text"])
        height = max(next_line["bbox"][3] - next_line["bbox"][1],
                     previous["bbox"][3] - previous["bbox"][1])
        # 报告标题与机构名之间常有封面留白；名称自身折行须更紧邻。
        gap_limit = 8 if not refs else 3
        if (next_line["bbox"][1] - previous["bbox"][3] > gap_limit * height
                or re.search(r"目录|说明|公开|年度|第.*[章部分]", text)):
            break
        parenthetical = re.fullmatch(r"\(([^()]+)\)", text)
        if parenthetical and not parts:
            inner = parenthetical[1]
            if inner in {"本级", "汇总", "部门汇总", "含下属单位"}:
                scopes.append(inner)
            elif ORG_END.fullmatch(inner):
                aliases.append(inner)
            else:
                break
        elif re.fullmatch(r"[\u4e00-\u9fff]+", text):
            if (parts and ORG_END.fullmatch(text) and ORG_END.fullmatch("".join(parts))
                    and re.match(r"(?:中共|.{1,12}(?:省|市|自治区|自治州|县|区|旗|镇|街道))", parts[0])):
                conflicting_names = True
            parts.insert(0, text)
        else:
            break
        refs.insert(0, previous)
        next_line = previous
    name = "".join(parts)
    if not ORG_END.fullmatch(name):
        return None, [], [], [], False
    return None if conflicting_names else name, refs, aliases, scopes, conflicting_names


def source_document_profile(artifact):
    first_table = min(
        [g.page_number for g in artifact.source_grids]
        + [t.page_number for t in artifact.table_fragments if not t.table_code.startswith("UNKNOWN")],
        default=4,
    )
    declarations = []
    for page in range(1, min(3, first_table - 1, artifact.page_count) + 1):
        all_lines = cover_lines(artifact, page)
        # 两个通道各自重建行块；相同画面的重复投影不打断真实相邻关系。
        for method in sorted({item["method"] for item in all_lines}):
            observed = [item for item in all_lines if item["method"] == method]
            if len(observed) > 30 or sum(len(item["text"]) for item in observed) > 500:
                continue
            for index, line in enumerate(observed):
                text = _normalized(line["text"])
                refs, start = [line], index
                match = HEADING.search(text)
                if not match and index:
                    previous = observed[index - 1]
                    if (re.match(r"20\d{2}年", _normalized(previous["text"]))
                            and re.fullmatch(r"(?:部门|单位)?(?:预算|决算)(?:公开|报告)?", text)):
                        text = _normalized(previous["text"]) + text
                        match = HEADING.search(text)
                        refs.insert(0, previous)
                        start -= 1
                if not match or "表" in text:
                    continue
                prefix = text[:match.start()].strip("：:")
                organization = prefix or match["organization"] or None
                explicit = [(ORGANIZATION.search(item["text"]), item) for item in observed]
                names = {m[1].strip() for m, _ in explicit if m}
                aliases, scopes, name_conflict = [], [], False
                if not organization and len(names) == 1:
                    organization = next(iter(names))
                    refs += [item for m, item in explicit if m]
                if not organization and start:
                    organization, name_refs, aliases, scopes, name_conflict = _preceding_organization(observed, start)
                    refs = name_refs + refs
                declarations.append({
                    "report_year": int(match[1]),
                    "report_kind": "budget" if match[3] == "预算" else "final",
                    "org_name": organization, "org_aliases": aliases, "org_scopes": scopes,
                    "org_name_conflict": name_conflict,
                    "sources": deepcopy(refs),
                })
    result = {"version": SOURCE_PROFILE_VERSION, "sources": declarations, "reasons": []}
    for field in ("report_year", "report_kind", "org_name"):
        values = {d[field] for d in declarations if d[field] is not None}
        result[field] = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1 or field == "org_name" and any(d["org_name_conflict"] for d in declarations):
            result[field] = None
            result["reasons"].append("explicit_cover_" + field + "_conflict")
    for field in ("org_aliases", "org_scopes"):
        result[field] = sorted({value for d in declarations for value in d[field]})
    return result


def bind_document_profile(profile, context):
    """展示及规则画像回接已物化上下文，落选的调用/文件名候选仍可审计。"""
    from src.schemas.document_profile import ProfileField

    if context is None or context.input_status == "build_failed":
        return profile
    result = profile.model_copy(deep=True)
    proof = next((d for d in context.input_diagnostics or []
                  if d.get("kind") == "source_document_profile"), {})
    source = proof.get("profile", {})
    for field, value in (("organization_name", context.org_name),
                         ("report_year", context.fiscal_year),
                         ("report_kind", context.report_kind)):
        previous = getattr(result, field)
        rejected = list(previous.rejected)
        if previous.value != value and previous.value not in (None, "", "unknown"):
            rejected.insert(0, previous.to_dict())
        setattr(result, field, ProfileField(value=value,
            source="bound_fiscal_context", evidence=f"{context.file_hash}:{source.get('version', '')}",
            rejected=rejected))
    # 明确原件冲突仍未解决；较弱文件名/调用参数的差异则保留为落选证据。
    source_conflicts = list(source.get("reasons", []))
    result.conflicts = [c for c in result.conflicts
                        if c.get("field") not in {"report_kind", "report_year", "organization_name"}]
    result.conflicts.extend({"field": reason.removeprefix("explicit_cover_").removesuffix("_conflict"),
                             "note": reason, "source": "bound_original_declarations"}
                            for reason in source_conflicts)
    if source_conflicts:
        result.profile_status, result.unsupported_reason = "partial", "source_identity_conflict"
    elif result.kind == "unknown":
        result.profile_status, result.unsupported_reason = "unresolved", "report_kind_unresolved"
    elif result.level == "unknown":
        result.profile_status, result.unsupported_reason = "partial", None
    elif result.unsupported_reason != "government_mixed_period_report":
        result.profile_status, result.unsupported_reason = "resolved", None
    return result


def bind_rule_document(document):
    """每个规则入口都消费同一报告身份；缺失/损坏快照不冒充已解析版本。"""
    from src.services.document_profile_resolver import resolve_document_profile

    context = getattr(document, "fact_context", None)
    if (context is None or context.context_version != "fact-context-v2"
            or context.input_status in {"build_failed", "snapshot_missing"}):
        return document
    profile = getattr(document, "document_profile", None) or resolve_document_profile(
        path=document.path, page_texts=document.page_texts)
    document.document_profile = bind_document_profile(profile, context)
    document.dominant_year = context.fiscal_year
    document.org_name = context.org_name
    document.report_kind = context.report_kind or "unknown"
    return document
