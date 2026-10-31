"""试运行能力：未进正式规则清单/义务账本。

模板固定语句试运行检查（2025 决算模板对照最小试运行闭环，专用模块）。

**调用边界（2026-10-31 复审后再次确认）**：本模块只由试运行 harness
（``scripts/phase1_prep/run_template_trial.py``）调用；不从 api/ 导入、不在
正式规则注册表注册。**发布模板或打开 GBC_TEMPLATE_SCOPE 线上开关只会启用
material_writing / template_anchor_checks 等已有生产消费者，不会自动把本模块
接入上传主链**；本模块的判定也不会出现在任何正式审校报告里。

本模块只消费试运行导出包 config 里的 ``trial_fixed_sentences`` 候选条目：
这些条目来自 uploads/.templates 两份 2025 决算模板 extracted.txt，状态为
"候选（待人工确认）"，不冒充已发布标准，也不写入义务账本或正式规则清单。
判定口径全部为试运行判据：命中结果以"改动候选/缺失候选"呈现，供人工复核。

输入约定（与生产锚点检查同一取数路径）：

- ``doc.document_profile.writing_scope``：material_writing.load 的统一裁决，
  其中 ``snapshot.available`` 须为真，``snapshot.content.config.trial_fixed_sentences``
  为候选条目列表；
- ``doc.document_profile.organization_name``：机构名槽位来源，``source`` 为
  filename 时视为不可用（文件名不可作已核实机构名）；
- ``doc.fact_context``（可选）：合格事实快照；条目配置 ``fact_indicators``
  且事实可用时金额判定优先走事实，否则回退章节文本模式；
- ``doc.writing_page_texts``（优先）或 ``doc.page_texts``：材料逐页原生文本。

判定流（v2，按 outputs/tpl_trial_review_20261031/review.json F1 修复重写）：

1. 模板侧资格：来源哈希/行号/原文/核对核心齐全，否则"未检查"；
2. 层级：``writing_scope`` 裁决与条目 scope 不匹配 → "不适用"；
   裁决未确认且 scope='all' 仍可运行（固定语句与写法无关）；
3. 先定位目标章节：条目配置的所属章节锚（``section_anchor``，缺省回退
   ``context_heading``）用与 template_anchor_checks 同源的正文标题定位
   （扁平流含折行容忍），并复用其目录区排除（toc_region_pages，目录续页
   条目不算正文）；章节未定位或多处出现 → "未检查（上级章节未定位）"；
   材料文本层不足 → "未检查"；
4. 章节内条件判定（仅用章节片段文本，禁止跨章节）：
   - 出现"无……支出"标准空表表述（核对核心命中）→ 条件成立（空表成立）；
   - 章节内金额全部为 0.00/0 万元 → 条件成立（零值成立；0.00 不能当作
     有实际支出）；
   - 存在非零金额（如 10.00万元）→ 条件不成立 → "不适用"；
   - 标准空表表述与非零金额同时出现（信号混叠）→ 条件未知 → "待复核"；
   - 两者都取不到（如"本节内容待核实"）→ 条件未知 → "未检查"，绝不报缺失。
   所用依据写入 ``condition_basis``（合格事实 / 章节文本模式）；
5. 句比对限定在章节片段内（第四部分等引用/示例中的相同语句不算）：
   条件成立后，槽位实例化句（模板句按已审核槽位替换机构名/目标年度，机构名
   取 profile.organization_name 且 source 非 filename，取不到 → "未检查"）
   精确出现 → "正常"；出现近似（去空白后不一致，如"支出安排。"）→
   "命中/改动候选"（规划 CMM-028，low，附双侧证据）；完全缺失 →
   "命中/缺失候选"（规划 CMM-029，medium）。不得统一 low。

槽位说明（试运行判据）：候选条目的机构名前缀（核对尾串年度令牌之前的文本）
与报告年度令牌即两处已审核槽位（与 annual_templates.reviewed_slot_changes 的
organization_name/report_year 两类一致）；实例化只替换这两处，金额、编码与
其他文字保持模板原文。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from src.services.template_anchor_checks import toc_region_pages

# ---------------------------------------------------------------------------
# 中文正则集中区（试运行固定算法；候选条目不能注入任意正则，只能引用此处
# 已审核的守卫模式，与 annual_templates 固定算法同一纪律）。
# ---------------------------------------------------------------------------

#: 近似行守卫：'无'与核对核心关键词近距离同现（间隔内不得出现句读）。
#: 三公固定语句：无……"三公"经费；国资固定语句：无……国有资本。
_PROXIMITY_GUARDS: Dict[str, re.Pattern[str]] = {
    "sangong": re.compile(r"无[^，。；：！？\n]{0,8}“?三公”?经?费"),
    "state_capital": re.compile(r"无[^，。；：！？\n]{0,8}国有资本"),
}

#: 金额陈述（万元口径；含千分位逗号，与 AGENTS.md R001 换算前识别同一形态）。
_AMOUNT_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?\s*万元")

#: 报告年度槽位令牌（固定语句核对尾串的起点）。
_YEAR_TOKEN_RE = re.compile(r"20\d{2}")

#: 章节标题起始（第X部分 / 中文或数字顿号小节）。用于章节边界探测。
_SECTION_HEAD_RE = re.compile(r"第([一二三四五六七八九十百\d]+)部分|([一二三四五六七八九十百\d]+)、")

#: 中文序号数值表（决算说明小节序号实际范围 一~九十九）。
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}

#: 上下文章节标题前缀（决算第三部分：决算情况说明；仅作模板侧记录展示）。
_PART_HEADING_PREFIX = "第三部分"

#: 候选条目允许的层级取值（与锚点 scope 同表）。
_ALLOWED_SCOPES = ("all", "unit", "department")

#: 条件状态取值（新增字段 condition_state 的固定枚举）。
CONDITION_STATES = ("空表成立", "零值成立", "条件不成立", "条件未知")

#: 主链接入状态常量：本模块与试运行锚点均"尚未接入主链"。
CHAIN_STATUS = "尚未接入主链（仅试运行 harness 调用；发布模板或开线上开关也不会自动接入）"

#: 证据片段半径（去空白视图上的字符数，与 v1 报告可读性一致）。
_SNIPPET_RADIUS = 16


def _flat(text: str) -> str:
    """比较用归一（v2）：NFKC 全半角折叠 + 去全部空白。

    全半角按 AGENTS.md 通用归一规则折叠（机构名槽位实测：封面画像产出
    “上海市统计局(本级)”半角括号，正文写“（本级）”全角——不折叠会把经批准
    的槽位放行误判成改动候选）。弯引号“”、句读不受 NFKC 影响，仍按原文
    参与近似比对，改动候选（如“支出安排。”）照常检出。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def _snippet(flat_text: str, start: int, end: int, radius: int = _SNIPPET_RADIUS) -> str:
    """在去空白视图上截取命中片段及少量上下文，供报告阅读定位。"""
    left = max(0, start - radius)
    right = min(len(flat_text), end + radius)
    return flat_text[left:right]


def _cn_to_int(text: str) -> Optional[int]:
    """中文/阿拉伯序号转整数（一~九十九；解析失败返回 None，不作后继判定）。"""
    text = str(text or "").strip()
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS or right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def _entry_qualified(entry: Any) -> Tuple[bool, str]:
    """资格检查：模板侧记录（哈希/行号/原文/核对核心）齐全且内部一致。"""
    if not isinstance(entry, dict):
        return False, "候选条目不是对象"
    template_side = entry.get("template_side")
    if not isinstance(template_side, dict):
        return False, "缺少模板侧记录（template_side）"
    for field in ("source_id", "line", "source_sha256", "extracted_sha256"):
        if not template_side.get(field):
            return False, "模板侧记录缺少字段：" + field
    if not isinstance(template_side.get("line"), int) or template_side["line"] < 1:
        return False, "模板侧行号无效"
    for field in ("source_sha256", "extracted_sha256"):
        value = str(template_side.get(field) or "")
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            return False, "模板侧哈希格式无效：" + field
    for field in ("original", "check_core", "tail"):
        if not str(entry.get(field) or "").strip():
            return False, "缺少原文/核对核心/核对尾串：" + field
    if entry.get("guard") not in _PROXIMITY_GUARDS:
        return False, "近似守卫模式不在固定算法清单内"
    if entry.get("scope") not in _ALLOWED_SCOPES:
        return False, "适用层级无效"
    tail = _flat(entry.get("tail"))
    if _flat(entry.get("check_core")) not in tail:
        return False, "核对核心不在核对尾串内，条目内部不一致"
    if not str(entry.get("section_anchor") or entry.get("context_heading") or "").strip():
        return False, "缺少所属章节锚（section_anchor/context_heading）"
    return True, ""


# ---------------------------------------------------------------------------
# 章节定位：正文扁平流 + 目录区排除 + 折行容忍（与 template_anchor_checks 同源）。
# ---------------------------------------------------------------------------


def _body_stream(pages: List[str], toc_pages: set) -> Tuple[str, List[Tuple[int, int]]]:
    """正文扁平流：跳过目录区页与空页，逐字符 NFKC 折叠拼接并记录 (页码, 页内偏移)。

    折行标题与跨页句子在流上连续；命中位置经映射回到页码，供证据定位。
    全半角按 _flat 同一规则折叠（机构名槽位实测：profile 产出半角括号、
    正文原文是全角括号，不折叠会把经批准的槽位放行误判成改动候选）。
    """
    chars: List[str] = []
    positions: List[Tuple[int, int]] = []
    for index, text in enumerate(pages, start=1):
        if index in toc_pages:
            continue
        for offset, char in enumerate(str(text or "")):
            if char.isspace():
                continue
            for folded in unicodedata.normalize("NFKC", char):
                if folded.isspace():
                    continue
                chars.append(folded)
                positions.append((index, offset))
    return "".join(chars), positions


def _parse_section_number(heading: str) -> Tuple[Optional[int], Optional[int]]:
    """解析章节锚的 (小节序号, 部分序号)；"九、…" → (9, None)。"""
    head = _flat(heading)[:12]
    match = _SECTION_HEAD_RE.match(head)
    if not match:
        return None, None
    item = _cn_to_int(match.group(2)) if match.group(2) else None
    part = _cn_to_int(match.group(1)) if match.group(1) else None
    return item, part


def _section_end(stream: str, content_start: int, item_number: Optional[int],
                 part_number: Optional[int]) -> int:
    """章节片段终点：内容起点之后第一个后继小节/后继部分标题处；找不到到流尾。

    中文小节锚只把"更大中文序号小节"当后继（"10、"式阿拉伯子目不打断章节）；
    "第X部分"以更大部分序号为界。探测不到边界时章节延伸到材料末尾（试运行
    判据，宁大勿小，不据此放宽条件判定）。
    """
    for match in _SECTION_HEAD_RE.finditer(stream, content_start):
        part_text, item_text = match.group(1), match.group(2)
        if part_text:
            part = _cn_to_int(part_text)
            if part is not None and part_number is not None and part > part_number:
                return match.start()
            if part is not None and part_number is None:
                return match.start()
        elif item_text:
            item = _cn_to_int(item_text)
            if item is not None and item_number is not None and item > item_number:
                return match.start()
            if item is not None and item_number is None:
                return match.start()
    return len(stream)


def _locate_section_span(stream: str, positions: List[Tuple[int, int]], heading_flat: str,
                         item_number: Optional[int], part_number: Optional[int]
                         ) -> Tuple[Optional[Tuple[int, int, List[int]]], str]:
    """定位目标章节唯一正文出现；返回 ((内容起点, 终点, 页码列表), 失败原因)。"""
    starts = [match.start() for match in re.finditer(re.escape(heading_flat), stream)]
    if not starts:
        return None, "未在正文（跳目录区）定位到所属章节标题"
    if len(starts) > 1:
        return None, "所属章节标题在正文出现多处（" + str(len(starts)) + "处），无法唯一归位"
    content_start = starts[0] + len(heading_flat)
    seg_end = _section_end(stream, content_start, item_number, part_number)
    upper = min(seg_end, len(positions))
    lower = min(content_start, upper)
    seg_pages = sorted({positions[i][0] for i in range(lower, upper)}) if upper > lower else []
    return (content_start, seg_end, seg_pages), ""


# ---------------------------------------------------------------------------
# 条件判定（章节片段内；金额判定优先合格事实，否则章节文本模式）。
# ---------------------------------------------------------------------------


def _amounts_from_text(segment_flat: str) -> List[Decimal]:
    values: List[Decimal] = []
    for match in _AMOUNT_RE.finditer(segment_flat):
        raw = match.group(0)[: match.group(0).find("万元")].replace(",", "").replace("，", "")
        try:
            values.append(Decimal(raw))
        except InvalidOperation:
            continue
    return values


def _amounts_from_facts(doc: Any, entry: Dict[str, Any]) -> Optional[List[Decimal]]:
    """条目配置 fact_indicators 且 fact_context 全部可解析时返回金额列表；否则 None。

    任一指标取不到确定操作数（best_value 返回 None）即视为事实不可裁决，
    回退章节文本模式，不把 None 当 0。
    """
    indicators = entry.get("fact_indicators") or []
    if not indicators:
        return None
    context = getattr(doc, "fact_context", None)
    if context is None:
        return None
    values: List[Decimal] = []
    for indicator in indicators:
        value, _fact = context.best_value(str(indicator))
        if value is None:
            return None
        values.append(value)
    return values


def _evaluate_condition(segment_flat: str, core: str, doc: Any, entry: Dict[str, Any]
                        ) -> Tuple[str, str]:
    """章节内条件判定；返回 (condition_state, condition_basis)。

    优先级（review F1 修复）：标准空表表述与非零金额同现属信号混叠 → 条件未知；
    其余按 空表表述 > 全零金额 > 非零金额 > 取不到 的顺序裁决。
    """
    core_present = core in segment_flat
    fact_values = _amounts_from_facts(doc, entry)
    if fact_values is not None:
        mode = "合格事实（doc.fact_context）"
        amounts = fact_values
    else:
        mode = "章节文本模式（万元金额陈述）"
        amounts = _amounts_from_text(segment_flat)
    nonzero = [value for value in amounts if value != 0]
    zero_count = len(amounts) - len(nonzero)
    if core_present and nonzero:
        return "条件未知", (
            "信号混叠：章节内同时出现标准空表表述与非零金额叙述（" + mode +
            "，非零金额 " + str(len(nonzero)) + " 笔）；条件无法裁决，待人工复核")
    if core_present:
        return "空表成立", ("章节内出现与核对核心同源的“无……支出”标准空表表述；" +
                            mode + "，零值金额 " + str(zero_count) + " 笔")
    if amounts and not nonzero:
        return "零值成立", ("章节内金额全部为 0.00/0 万元（0.00 不当作有实际支出）；" +
                            mode + "，零值金额 " + str(zero_count) + " 笔")
    if nonzero:
        return "条件不成立", ("章节内存在非零金额叙述（" + str(len(nonzero)) + " 笔）；" + mode)
    return "条件未知", ("章节内既无标准空表表述也无金额陈述（如“本节内容待核实”）；" +
                        mode + "，条件事实不足")


# ---------------------------------------------------------------------------
# 句比对（限定章节片段；槽位实例化 + 近似行两种形态）。
# ---------------------------------------------------------------------------


def _instantiated_tail(entry: Dict[str, Any], target_year: Any) -> str:
    """核对尾串按报告年度槽位实例化（仅替换第一处年度令牌）。"""
    tail = _flat(entry["tail"])
    match = _YEAR_TOKEN_RE.search(tail)
    if isinstance(target_year, int) and match:
        tail = tail[: match.start()] + str(target_year) + tail[match.end():]
    return tail


def _organization_name(profile: Any) -> Optional[str]:
    """机构名槽位：profile.organization_name 且 source 非 filename；取不到返回 None。"""
    field = getattr(profile, "organization_name", None)
    value = getattr(field, "value", None)
    if getattr(field, "source", None) == "filename":
        return None
    name = str(value or "").strip()
    return name or None


def _sentence_outcome(segment_flat: str, positions: List[Tuple[int, int]], seg_start: int,
                      seg_end: int, entry: Dict[str, Any], org_name: str, tail: str,
                      guard: re.Pattern[str]) -> Dict[str, Any]:
    """章节片段内的句比对；返回材料侧证据与判定要素（不直接写 verdict）。"""
    sentence = _flat(org_name) + tail
    exact = segment_flat.find(sentence)
    core = _flat(entry["check_core"])
    if exact >= 0:
        index = min(seg_start + exact, len(positions) - 1)
        return {"state": "精确出现", "page": positions[index][0],
                "text": _snippet(segment_flat, exact, exact + len(sentence)),
                "expected": sentence, "actual": sentence, "overlaps": []}
    overlaps: List[Dict[str, Any]] = []
    core_at = segment_flat.find(core)
    if core_at >= 0:
        index = min(seg_start + core_at, len(positions) - 1)
        overlaps.append({"page": positions[index][0], "form": "核心片段外围被改",
                         "text": _snippet(segment_flat, core_at, core_at + len(core))})
    guard_match = guard.search(segment_flat)
    if guard_match:
        index = min(seg_start + guard_match.start(), len(positions) - 1)
        overlaps.append({"page": positions[index][0], "form": "无与核心关键词近距离同现",
                         "text": _snippet(segment_flat, guard_match.start(), guard_match.end())})
    if overlaps:
        return {"state": "近似出现", "page": overlaps[0]["page"], "text": overlaps[0]["text"],
                "expected": sentence,
                "actual": overlaps[0]["text"], "overlaps": overlaps}
    index = min(seg_start, len(positions) - 1)
    return {"state": "未出现", "page": positions[index][0],
            "text": _snippet(segment_flat, 0, min(len(segment_flat), 2 * _SNIPPET_RADIUS)),
            "expected": sentence, "actual": "章节片段内未找到实例化句", "overlaps": []}


def trial_fixed_sentence_findings(doc: Any) -> List[Dict[str, Any]]:
    """对候选固定语句逐条给出试运行判定（结构化条目，不产出正式 Issue）。"""
    resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
    snapshot = resolution.get("snapshot") or {}
    config = snapshot.get("effective_config") or (snapshot.get("content") or {}).get("config") or {}
    pages = [str(text or "") for text in
             (getattr(doc, "writing_page_texts", None) or getattr(doc, "page_texts", None) or [])]
    entries = config.get("trial_fixed_sentences") or []
    profile = getattr(doc, "document_profile", None)
    org_name = _organization_name(profile)
    target_year = snapshot.get("target_year")

    findings: List[Dict[str, Any]] = []
    for position, entry in enumerate(entries, start=1):
        identifier = str(entry.get("id") or "F#" + str(position)) if isinstance(entry, dict) else "F#" + str(position)
        template_side = entry.get("template_side") if isinstance(entry, dict) else None
        base: Dict[str, Any] = {
            "id": identifier,
            "rule_scope": "CMM-028(规划)/CMM-029(规划)",
            "trial_only": True,
            "chain_status": CHAIN_STATUS,
            "execution_status": "实际执行",
            "condition": entry.get("condition") if isinstance(entry, dict) else None,
            "condition_state": None,
            "section_pages": None,
            "condition_basis": None,
            "template_side": {
                "source_id": (template_side or {}).get("source_id"),
                "material_type": (template_side or {}).get("material_type"),
                "line": (template_side or {}).get("line"),
                "context_line": (template_side or {}).get("context_line"),
                "original": entry.get("original") if isinstance(entry, dict) else None,
                "source_sha256": (template_side or {}).get("source_sha256"),
                "extracted_sha256": (template_side or {}).get("extracted_sha256"),
            } if isinstance(entry, dict) else None,
            "material_side": None,
            "verdict": None,
            "reason": "",
        }

        qualified, quality_reason = _entry_qualified(entry)
        if not qualified:
            base["verdict"] = "未检查"
            base["execution_status"] = "未检查"
            base["reason"] = "模板侧记录不完整，" + quality_reason
            findings.append(base)
            continue

        if not snapshot.get("available"):
            base["verdict"] = "未检查"
            base["execution_status"] = "未检查"
            base["reason"] = "年度模板快照不可用：" + str(snapshot.get("reason") or "")
            findings.append(base)
            continue

        scope = entry.get("scope")
        status = resolution.get("status")
        value = resolution.get("value")
        if scope != "all":
            if status != "confirmed":
                base["verdict"] = "未检查"
                base["execution_status"] = "未检查"
                base["reason"] = "写法层级未确认（scope=" + str(scope) + "），固定语句层级适用性无法裁决"
                findings.append(base)
                continue
            if value != scope:
                base["verdict"] = "不适用"
                base["reason"] = "材料写法层级为" + str(value) + "，候选语句适用层级为" + str(scope)
                findings.append(base)
                continue

        if not any(text.strip() for text in pages):
            base["verdict"] = "未检查"
            base["execution_status"] = "未检查"
            base["reason"] = "材料文本层不足（无逐页文本），固定语句无法核验"
            findings.append(base)
            continue

        # ---- 先定位目标章节（所属章节锚；目录区排除、折行容忍） ----
        heading = str(entry.get("section_anchor") or entry.get("context_heading") or "")
        heading_flat = _flat(heading)
        item_number, part_number = _parse_section_number(heading)
        parent_flat = _flat(str(entry.get("parent_section") or ""))
        if part_number is None and parent_flat:
            _parent_item, part_number = _parse_section_number(parent_flat)
        stream, positions = _body_stream(pages, toc_region_pages(pages))
        span, span_error = _locate_section_span(stream, positions, heading_flat,
                                                item_number, part_number)
        if span is None:
            base["verdict"] = "未检查"
            base["execution_status"] = "未检查"
            base["reason"] = "上级章节未定位（" + heading + "）：" + span_error + "，固定语句无法归位，不判缺失"
            findings.append(base)
            continue
        seg_start, seg_end, seg_pages = span
        segment_flat = stream[seg_start:seg_end]
        base["section_pages"] = seg_pages

        # ---- 章节内条件判定（禁止跨章节） ----
        core = _flat(entry["check_core"])
        guard = _PROXIMITY_GUARDS[entry["guard"]]
        state, basis = _evaluate_condition(segment_flat, core, doc, entry)
        base["condition_state"] = state
        base["condition_basis"] = basis

        if state == "条件不成立":
            base["verdict"] = "不适用"
            base["material_side"] = {"page": seg_pages[0] if seg_pages else None,
                                     "text": _snippet(segment_flat, 0, min(len(segment_flat), 2 * _SNIPPET_RADIUS))}
            base["reason"] = ("材料在“" + heading + "”章节（第" + "、".join(str(p) for p in seg_pages) +
                              "页）存在非零金额支出叙述，不属于空表说明语境；固定语句缺席符合模板条件。依据：" + basis)
            findings.append(base)
            continue
        if state == "条件未知":
            mixed = "信号混叠" in basis
            base["verdict"] = "待复核" if mixed else "未检查"
            base["execution_status"] = "未检查" if not mixed else "实际执行"
            base["material_side"] = {"page": seg_pages[0] if seg_pages else None,
                                     "text": _snippet(segment_flat, 0, min(len(segment_flat), 2 * _SNIPPET_RADIUS))}
            base["reason"] = ("条件事实不足（" + basis + "）；不能因章节存在就认定应有无支出语句，"
                              "绝不报缺失，待人工复核")
            findings.append(base)
            continue

        # ---- 条件成立：句比对限定在章节片段（机构名槽位先决） ----
        if not org_name:
            base["verdict"] = "未检查"
            base["execution_status"] = "未检查"
            base["reason"] = ("条件成立（" + state + "）但机构名槽位不可用：profile.organization_name 缺失或"
                              "source 为 filename，无法实例化模板句，不判缺失")
            findings.append(base)
            continue

        tail = _instantiated_tail(entry, target_year)
        outcome = _sentence_outcome(segment_flat, positions, seg_start, seg_end, entry,
                                    org_name, tail, guard)
        base["material_side"] = {"page": outcome["page"], "text": outcome["text"]}
        base["overlaps"] = outcome["overlaps"]
        line_note = "模板侧第" + str(template_side["line"]) + "行原文为：" + str(entry["original"])
        if outcome["state"] == "精确出现":
            base["verdict"] = "正常"
            base["reason"] = ("条件成立（" + state + "）且槽位实例化句在所属章节精确出现（机构名取自"
                              "profile.organization_name，年度按目标报告年度替换）；" + line_note +
                              "。依据：" + basis)
            findings.append(base)
            continue
        if outcome["state"] == "近似出现":
            base["verdict"] = "命中"
            base["candidate_type"] = "改动候选"
            base["rule_scope"] = "CMM-028(规划)"
            base["severity_suggestion"] = "low"
            base["reason"] = ("条件成立（" + state + "）但章节内出现与实例化句近似而不一致的写法；期望「" +
                              outcome["expected"] + "」。改动候选为试运行判据，与缺失候选存在重叠范围，" +
                              line_note + "，待人工复核。依据：" + basis)
            findings.append(base)
            continue
        base["verdict"] = "命中"
        base["candidate_type"] = "缺失候选"
        base["rule_scope"] = "CMM-029(规划)"
        base["severity_suggestion"] = "medium"
        base["reason"] = ("条件成立（" + state + "）但所属章节片段内未找到模板固定语句；期望「" +
                          outcome["expected"] + "」。" + line_note +
                          "。缺失候选为试运行判据，待人工复核。依据：" + basis)
        findings.append(base)
    return findings


__all__ = ["trial_fixed_sentence_findings", "CHAIN_STATUS", "CONDITION_STATES"]
