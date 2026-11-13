"""正文的章节、条目、段落及有来源的数值声明；不跨未证明的页界借数。"""

import hashlib
import re
import unicodedata

from src.engine.fiscal_lexeme import amount_in_wan, lex_numeric, resolve_unit

NARRATIVE_VERSION = "source-narrative-v2-spatial-alternates"
HEADING = re.compile(r"^(?:第[一二三四五六七八九十]+部分|[一二三四五六七八九十]+[、．.])")
ITEM = re.compile(r"^(?:[（(]([一二三四五六七八九十百\d]+)[）)]|(\d+)[、．.])")
UNIT = re.compile(
    r"(?:单位\s*[:：为]?|金额[^。；\n]{0,8}单位\s*[:：为]?)\s*(百万元|亿元|万元|千元|元)"
)
TOKEN = re.compile(
    r"\d{4}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?"
    r"|\d{3}(?:[-－]\d{2}){1,2}"
    r"|[（(]?[+＋−\-－—–]?\s*(?:\d[\d,，.．\s]*\d|\d)[）)]?"
    r"(?:\s*(?:百万元|亿元|万元|千元|元|[%％]|人|个|辆|项|次|台|套|件|月|年))?"
)
FINANCIAL = re.compile(r"收入|支出|预算|决算|经费|资金|拨款|结余|结转|金额|采购|费用")
CLAUSE_BOUNDARY = re.compile(r"[。；;\n]|(?<!\d)[，,]|[，,](?!\d)")


def uid(kind, sources):
    text = "|".join(
        f"{s['page']}:{s['block_uid']}:{s.get('text', s.get('quote', ''))}" for s in sources
    )
    return f"{kind}-" + hashlib.sha256(text.encode()).hexdigest()[:24]


def source_refs(start, end, sources):
    return [
        {
            "block_uid": s["block_uid"],
            "page": s["page"],
            "bbox": s["bbox"],
            "start_in_block": max(start, s["start"]) - s["start"],
            "end_in_block": min(end, s["end"]) - s["start"],
            "quote": s["text"][
                max(start, s["start"]) - s["start"] : min(end, s["end"]) - s["start"]
            ],
            "granularity": "fragment",
        }
        for s in sources
        if max(start, s["start"]) < min(end, s["end"])
    ]


def declarations(text, sources, scope_units):
    mentions, claims = [], []
    for match in TOKEN.finditer(text):
        raw = match.group().strip()
        # 声明身份只来自本分句；前章或下一条目的词不能给当前数字贴标签。
        boundaries = list(CLAUSE_BOUNDARY.finditer(text))
        left = max((b.end() for b in boundaries if b.end() <= match.start()), default=0)
        right = min((b.start() for b in boundaries if b.start() >= match.end()), default=len(text))
        clause = text[left:right]
        prefix = text[left : match.start()]
        normalized = unicodedata.normalize("NFKC", raw)
        expected = (
            "code"
            if re.search(r"编码\s*(?:为|是|[:：])?\s*$", prefix)
            or re.fullmatch(r"\d{3}(?:[-－]\d{2}){1,2}", raw)
            else None
        )
        quantity = re.search(r"[人个辆项次台套件月年]$", normalized)
        if quantity and not normalized.endswith("元"):
            token = lex_numeric(normalized[:-1], expected="quantity")
            kind = (
                "date"
                if normalized.endswith("年") and re.fullmatch(r"\d{4}年", normalized)
                else token.kind
            )
            unit = normalized[-1]
        else:
            token = lex_numeric(raw, expected=expected)
            kind, unit = token.kind, token.unit
        source = source_refs(match.start(), match.start() + len(match.group().rstrip()), sources)
        record = {
            "raw_text": raw,
            "kind": kind,
            "value": str(token.value) if token.value is not None else None,
            "unit": unit,
            "span": [match.start(), match.start() + len(match.group().rstrip())],
            "sources": source,
        }
        mentions.append(record)
        marker = ITEM.match(text)
        if marker and match.start() < marker.end():
            record["kind"] = "ordinal"
            continue
        is_money = kind == "amount" or (
            kind in {"number", "invalid"}
            and FINANCIAL.search(clause)
            and (scope_units or token.unit)
        )
        if not is_money:
            continue
        scope = resolve_unit(
            cell=token.unit, column=scope_units[0] if len(scope_units) == 1 else None
        )
        conflicts = list(scope.conflicts)
        if len(scope_units) > 1:
            conflicts.append(("paragraph", list(scope_units)))
        value = amount_in_wan(raw, scope.unit) if not conflicts else None
        years = list(re.finditer(r"(20\d{2})年", prefix))
        stages = [
            (m.start(), m.group())
            for m in re.finditer(r"调整后预算|全年预算|年初预算|决算", prefix)
        ]
        claims.append(
            {
                **record,
                "claim_id": uid("claim", source) + f":{match.start()}",
                "normalized_value_wan": str(value) if value is not None else None,
                "unit": scope.unit,
                "unit_conflicts": [list(c) for c in conflicts],
                "unit_source": "explicit" if token.unit else "paragraph_scope",
                "amount_year": int(years[-1][1]) if years else None,
                "period": "上年"
                if "上年" in prefix
                else "年末"
                if "年末" in prefix
                else "年初"
                if "年初" in prefix and "年初预算" not in prefix
                else "本年",
                "budget_stage": stages[-1][1] if stages else None,
                "status": "parsed" if value is not None else "requires_regional_read",
            }
        )
    for match in re.finditer(
        r"无此项|不涉及|不适用|(?<![A-Za-z])[Xx×]{2,}|(?<![\d−-])[—–](?!\d)", text
    ):
        token = lex_numeric(match.group())
        mentions.append(
            {
                "raw_text": match.group(),
                "kind": token.kind,
                "value": None,
                "unit": None,
                "span": [match.start(), match.end()],
                "sources": source_refs(match.start(), match.end(), sources),
            }
        )
    return sorted(mentions, key=lambda m: m["span"]), claims


def align_text_layers(blocks):
    """同一位置的原生/OCR 文字是候选，不能串接成“收入收入1212万元”。"""
    if not any(b.extraction_method == "ocr" for b in blocks):
        return list(blocks), {}
    parents = list(range(len(blocks)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    by_page = {}
    for index, block in enumerate(blocks):
        if block.bbox:
            by_page.setdefault(block.page_number, []).append(index)
    for indexes in by_page.values():
        for offset, index in enumerate(indexes):
            a = blocks[index]
            for other in indexes[offset + 1 :]:
                b = blocks[other]
                if a.extraction_method != "ocr" and b.extraction_method != "ocr":
                    continue
                aa, bb = a.bbox, b.bbox
                intersection = max(0, min(aa[2], bb[2]) - max(aa[0], bb[0])) * max(
                    0, min(aa[3], bb[3]) - max(aa[1], bb[1])
                )
                area = min((aa[2] - aa[0]) * (aa[3] - aa[1]), (bb[2] - bb[0]) * (bb[3] - bb[1]))
                if area > 0 and intersection / area >= 0.8:
                    parents[root(other)] = root(index)
    groups = {}
    for index, block in enumerate(blocks):
        groups.setdefault(root(index), []).append(block)
    selected, alternates = [], {}
    for group in groups.values():
        if len(group) == 1:
            selected.extend(group)
            continue
        layers = {}
        for b in group:
            layer = "native" if b.extraction_method != "ocr" else b.block_uid.split(":block-", 1)[0]
            layers.setdefault(layer, []).append(b)
        chosen = (
            "native"
            if "native" in layers
            else max(
                layers,
                key=lambda k: sum(
                    (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) for b in layers[k]
                ),
            )
        )
        primary = sorted(layers.pop(chosen), key=lambda b: (b.bbox[1], b.bbox[0]))
        primary_text = "".join(b.text for b in primary)

        def normalize(text):
            return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("−", "-")

        candidates = []
        for layer, source in layers.items():
            source = sorted(source, key=lambda b: (b.bbox[1], b.bbox[0]))
            candidates.append(
                {
                    "layer": layer,
                    "text": "".join(b.text for b in source),
                    "sources": [
                        {
                            "block_uid": b.block_uid,
                            "page": b.page_number,
                            "bbox": list(b.bbox),
                            "text": b.text,
                            "granularity": "fragment",
                        }
                        for b in source
                    ],
                    "status": "agreed"
                    if normalize(primary_text) == normalize("".join(b.text for b in source))
                    else "requires_regional_read",
                }
            )
        selected.extend(primary)
        for b in primary:
            alternates[b.block_uid] = candidates
    return selected, alternates


def reconstruct_narrative(blocks, *, tables=()):
    """同页按文字几何重建段落；跨页需要重复条目标记和明确的续接字样。"""
    boxes = [
        (c.page_number, table.bbox)
        for table in tables
        if table.extraction_method != "page_text_fallback"
        for c in table.cells[:1]
        if table.bbox
    ]
    blocks, alternate_sources = align_text_layers(blocks)
    ordered = sorted(
        blocks,
        key=lambda b: (
            b.page_number,
            b.bbox[1] if b.bbox else 0,
            b.bbox[0] if b.bbox else 0,
            b.block_uid,
        ),
    )
    lines = []
    for block in ordered:
        if block.bbox and any(
            page == block.page_number
            and box[0] <= block.bbox[0]
            and box[1] <= block.bbox[1]
            and box[2] >= block.bbox[2]
            and box[3] >= block.bbox[3]
            for page, box in boxes
        ):
            continue
        if (
            not lines
            or block.page_number != lines[-1][0].page_number
            or not block.bbox
            or not lines[-1][0].bbox
            or abs(block.bbox[1] - lines[-1][0].bbox[1]) > 3
        ):
            lines.append([])
        lines[-1].append(block)
    entries = []
    section, section_uid, item, item_key, item_uid = None, None, None, None, None
    previous_page = None
    scope_units = []
    for line in lines:
        sources, text = [], ""
        for block in sorted(line, key=lambda b: b.bbox[0] if b.bbox else 0):
            if sources and block.bbox and sources[-1]["bbox"]:
                prior = sources[-1]["bbox"]
                if block.bbox[0] - prior[2] > (block.bbox[3] - block.bbox[1]) * 0.5:
                    text += " "
            start = len(text)
            text += block.text
            sources.append(
                {
                    "block_uid": block.block_uid,
                    "page": block.page_number,
                    "bbox": list(block.bbox) if block.bbox else None,
                    "text": block.text,
                    "start": start,
                    "end": len(text),
                    "granularity": "fragment",
                }
            )
        alignment_candidates = [
            candidate
            for source in sources
            for candidate in alternate_sources.get(source["block_uid"], [])
        ]
        heading, start = HEADING.match(text), ITEM.match(text)
        page = line[0].page_number
        continuation = "same_page"
        new_key = next((part for part in start.groups() if part), None) if start else None
        if previous_page is not None and page != previous_page:
            continuation = (
                "explicit_same_item"
                if start and new_key == item_key and re.search(r"[（(]续[）)]|续上页", text)
                else "unproven_page_boundary"
            )
            if continuation != "explicit_same_item":
                item, item_key, item_uid = None, None, None
                scope_units = []
        if heading:
            section, section_uid = text, uid("section", sources)
            item, item_key, item_uid = None, None, None
            scope_units = []
        if start and continuation != "explicit_same_item":
            item, item_key, item_uid = text, new_key, uid("item", sources)
            scope_units = []
        units = UNIT.findall(text)
        if units:
            scope_units = sorted(set(units))
        previous = entries[-1] if entries else None
        # 标题、条目起点、明确句末和页界都是段落边界，不能把后项原因借给前项。
        join = bool(
            previous
            and not heading
            and not start
            and continuation == "same_page"
            and (not units or previous["unit_scope"] == scope_units)
            and previous["section_uid"] == section_uid
            and previous["item_uid"] == item_uid
            and not re.search(r"[。；;！!？?]$", previous["text"])
            and sources[0]["bbox"]
            and previous["sources"][-1]["bbox"]
            and 0
            <= sources[0]["bbox"][1] - previous["sources"][-1]["bbox"][3]
            <= (sources[0]["bbox"][3] - sources[0]["bbox"][1]) * 1.5
        )
        if join:
            offset = len(previous["text"]) + 1
            previous["text"] += "\n" + text
            previous["sources"].extend(
                {**s, "start": s["start"] + offset, "end": s["end"] + offset} for s in sources
            )
            previous["alignment_candidates"].extend(alignment_candidates)
            previous["numeric_mentions"], previous["amount_claims"] = declarations(
                previous["text"], previous["sources"], scope_units
            )
        else:
            mentions, claims = declarations(text, sources, scope_units)
            entries.append(
                {
                    "entry_uid": uid("paragraph", sources),
                    "section": section,
                    "section_uid": section_uid,
                    "item": item,
                    "item_key": item_key,
                    "item_uid": item_uid,
                    "text": text,
                    "sources": sources,
                    "continuation_status": continuation,
                    "role": "heading" if heading else "body",
                    "unit_scope": list(scope_units),
                    "numeric_mentions": mentions,
                    "amount_claims": claims,
                    "alignment_candidates": alignment_candidates,
                }
            )
        previous_page = page
    for entry in entries:
        uncertain = any(
            c["status"] == "requires_regional_read" for c in entry["alignment_candidates"]
        )
        if uncertain:
            for claim in entry["amount_claims"]:
                claim["status"] = "requires_regional_read"
                claim["recognition_conflict"] = True
    return entries
