"""只从同一原件的明确封面取报告身份；金额年度和局部单位另行解析。"""

from copy import deepcopy
import re

from src.services.fiscal_business_rows import valid_samples, lines, proofs, compact
from src.services.ocr_grid_fragments import collect_page_fragments, group_projections

SOURCE_PROFILE_VERSION = "source-cover-profile-v2-multiline-source-name"
HEADING = re.compile(
    r"(20\d{2})年(?:度)?(?P<organization>[\u4e00-\u9fff]*?)(?:部门|单位)?(预算|决算)(?:公开)?(?:报告)?$"
)
ORGANIZATION = re.compile(r"(?:单位名称|编制单位)\s*[:：]\s*(.+)")
ORG_END = re.compile(
    r"[\u4e00-\u9fff]{2,}(?:局|委员会|办公室|人民代表大会|政府|中心|学校|院|馆|站|队|所)$"
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


def source_document_profile(artifact):
    first_table = min(
        [g.page_number for g in artifact.source_grids]
        + [
            t.page_number
            for t in artifact.table_fragments
            if not t.table_code.startswith("UNKNOWN")
        ],
        default=4,
    )
    declarations = []
    for page in range(1, min(3, first_table - 1, artifact.page_count) + 1):
        observed = cover_lines(artifact, page)
        if len(observed) > 30 or sum(len(item["text"]) for item in observed) > 500:
            continue
        for index, line in enumerate(observed):
            text = compact(line["text"])
            refs = [line]
            match = HEADING.search(text)
            # 年份与“部门决算”可以分行；只续接同通道紧邻行，保留两个真实来源。
            if not match and index:
                previous = observed[index - 1]
                if (
                    previous["method"] == line["method"]
                    and re.match(r"20\d{2}年", compact(previous["text"]))
                    and re.fullmatch(r"(?:部门|单位)?(?:预算|决算)(?:公开|报告)?", text)
                ):
                    text = compact(previous["text"]) + text
                    match = HEADING.search(text)
                    refs.insert(0, previous)
            if not match or "表" in text:
                continue
            prefix = text[: match.start()].strip("：:")
            organization = prefix or match["organization"] or None
            explicit = [(ORGANIZATION.search(item["text"]), item) for item in observed]
            names = {m[1].strip() for m, _ in explicit if m}
            if not organization and len(names) == 1:
                organization = next(iter(names))
                refs += [item for m, item in explicit if m]
            preceding = index - len(refs)
            if not organization and preceding >= 0:
                previous = observed[preceding]
                if ORG_END.fullmatch(compact(previous["text"])):
                    organization = compact(previous["text"])
                    refs.append(previous)
                    # 封面的机构名常分两三行居中排版，不能只取最后的“管理委员会”。
                    for prior_index in range(preceding - 1, max(-1, preceding - 4), -1):
                        prior = observed[prior_index]
                        current = observed[prior_index + 1]
                        if (
                            prior["method"] != line["method"]
                            or not re.fullmatch(r"[\u4e00-\u9fff]{2,}", compact(prior["text"]))
                            or re.search(r"目录|说明|公开|决算|预算|部分|第.*章", prior["text"])
                            or current["bbox"][1] - prior["bbox"][3]
                            > 3
                            * max(
                                current["bbox"][3] - current["bbox"][1],
                                prior["bbox"][3] - prior["bbox"][1],
                            )
                        ):
                            break
                        organization = compact(prior["text"]) + organization
                        refs.append(prior)
            declarations.append(
                {
                    "report_year": int(match[1]),
                    "report_kind": "budget" if match[3] == "预算" else "final",
                    "org_name": organization,
                    "sources": deepcopy(refs),
                }
            )
    result = {"version": SOURCE_PROFILE_VERSION, "sources": declarations, "reasons": []}
    for field in ("report_year", "report_kind", "org_name"):
        values = {d[field] for d in declarations if d[field] is not None}
        result[field] = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1:
            result["reasons"].append("explicit_cover_" + field + "_conflict")
    return result
