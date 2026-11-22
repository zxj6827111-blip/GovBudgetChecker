"""检查点清单：决算专用规则与预决算通用的空表说明反向检查（2026-10-22 全系统默认）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| V33-251 | 决算 A-09/C-11/C-12 | 一般公共预算财政拨款支出决算具体情况：逐项细化到项、年初预算与支出决算分项加总等于总额、不漏列科目 |
| V33-252 | 决算 C-04 | 财政拨款收支总表“政府性基金/国有资本经营”列本年支出 = 对应决算表本年支出合计 |
| CMM-026 | 预算 A-07、决算 A-07 | 有数据的表不得保留“本表无数据/空表”说明 |
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .amount_math import classify_amount_diff
from .rule_policy import AMOUNT_TOLERANCE_WAN
from .checklist_support import (
    compact,
    display_half_unit,
    flat_span,
    issue_at,
    iter_pages,
    page_flat,
    page_has_table,
    unique_quote,
)
from .fact_context import _canonical_budget_stage
from .native_table_arithmetic import table_facts
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import Document, Issue, Rule, _unit_error_explains_amount
from src.schemas.parse_artifact import FiscalFact


def _runtime_defect_key(doc: Any, page: int, suffix: str) -> str:
    """2A：运行期缺陷键（文档指纹+页码+操作数），不依赖 GS-* 金标 ID。"""
    fingerprint = ""
    for attr in ("source_file_hash", "file_sha256", "sha256", "doc_sha256"):
        value = getattr(doc, attr, None)
        if value:
            fingerprint = str(value)
            break
    return f"{fingerprint}|p{page}|{suffix}"
from src.utils.narration import normalize_amount_punctuation

# R3（2026-11-20 独立复核）：先取得**完整**数字词，再决定能否取数。
# 旧模式 ``\d[\d,]*(?:\.\d+)?`` 会把 “5,465.41.00”截成“5,465.41”，
# 直接去逗号又会把“54,65.41”读成 5465.41——两种非法书写都能得到正式通过。
# 这里把数字样式的连续片段整段捕获，交给 fiscal_lexeme 校验；非法词保留
# 原貌并只影响依赖它的算式，不做修补、截断或按总额反推。
_AMOUNT_LEXEME = r"[+\-−]?\d[\d,]*(?:\.\d+)*"
_AMOUNT = rf"({_AMOUNT_LEXEME})"


def _dec(text: Any) -> Optional[Decimal]:
    """按 fiscal_lexeme 严格取数；非法千分位/双小数点等一律返回 None。"""
    from .fiscal_lexeme import lex_numeric

    lexeme = lex_numeric(text)
    if lexeme.kind not in {"number", "amount"} or lexeme.value is None:
        return None
    return lexeme.value


def _wan_display(value: Decimal) -> str:
    """万元数值的显示格式：至少两位小数，去掉第三位以后的全零尾数。

    ``54,654,100 元 × 0.0001`` 会得到 ``5465.4100``，数值正确但显示成
    “168959.5400”会被误读为精度不同；``0.017982`` 这类真实精细差异保留。
    """
    text = format(value, "f")
    if "." not in text:
        return text + ".00"
    whole, fraction = text.split(".", 1)
    fraction = fraction.rstrip("0")
    if len(fraction) < 2:
        fraction = fraction.ljust(2, "0")
    return f"{whole}.{fraction}"


def _wan_trim(value: Decimal) -> str:
    """逐项操作数保留原精度：整数不带小数位，``5465.4100`` 只去掉多余零。

    回执里的逐项 ``value_wan`` 沿用原设计（``40`` 不写成 ``40.00``）；
    只有合计/差额等展示字段使用 :func:`_wan_display` 的两位小数格式。
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# V33-251 支出决算具体情况
# ---------------------------------------------------------------------------

_DETAIL_START_RE = re.compile(r"(?:[（(]三[)）])?一般公共预算财政拨款支出决算具体情况")
_DETAIL_STOP_RE = re.compile(r"六、|一般公共预算财政拨款(?:基)?本支出决算情况说明")
_DETAIL_TOTAL_RE = re.compile(
    r"年初预算(?:数)?(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元[，,、\s]*"
    r"支出决算(?:数)?(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元"
)
#: 「年初预算 → 调整后预算 → 决算执行数」是常见变体。只认这三段式，
#: 且“调整后预算”金额用非捕获组，保证 group(1)=年初预算、group(2)=决算
#: 执行数，与标准总额匹配的取值口径一致。若决算执行数与分项之和不一致，
#: 仍按真实差额出 finding，不因为换了标签就放过。
_DETAIL_TOTAL_ADJUSTED_RE = re.compile(
    r"年初预算(?:数)?\s*(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元"
    r"[\s\S]{0,160}?调整\s*后\s*预\s*算(?:\s*数)?\s*(?:为|是)?\s*[:：]?\s*\d[\d,]*(?:\.\d+)?\s*万元"
    r"[\s\S]{0,80}?(?:年终)?决算执行数\s*(?:为|是)?\s*[:：]?\s*" + _AMOUNT + r"\s*万元"
)
_ITEM_START_RE = re.compile(r"(?:^|(?<=[。；;：:]))(\d{1,2})[、.．](?=[一-鿿“\"])")
# 声明角色：年初预算及其省略“年初”的写法（只认句首/分句起点的“预算为”，
# 避免把“决算数大于预算数2.39万元”“追加预算”“调整后预算”当成预算声明），
# 支出决算、决算支出、单说“决算数”。单位可缺省——缺单位不是“没披露”，
# 而是已披露但不可归一，二者在完整性清单里必须分开。
_DECL_RE = re.compile(
    r"(?P<kind>年初支出预算|年初预算|支出决算|决算支出|决算数|"
    r"(?:(?<=[。；;：:，,])[ \t]*预算))"
    r"(?:数)?(?:总计|合计)?(?:为|是)?\s*[:：]?\s*"
    r"(?P<amount>" + _AMOUNT + r")\s*(?:(?P<bad_unit>万万元|亿亿元|元元|万元万)|(?P<unit>亿元|万元|元))?"
)
_AMOUNT_UNIT_FACTOR = {
    "亿元": Decimal("10000"),
    "万元": Decimal("1"),
    "元": Decimal("0.0001"),
}


def _record_display_half_unit(record: Dict[str, Any]) -> Decimal:
    """按原文末位精度 × 来源单位到万元的系数计算半个末位（万元）。

    例如：“0.0986 亿元”末位 0.0001 亿元 → 半个末位 0.00005 × 10000 = 0.5 万元；
    “986.00 万元” → 0.005 万元；“179.82 元” → 0.005 × 0.0001 = 0.0000005 万元。
    显式零声明（``explicit_zero``）与整写零（如“0 万元”“0 亿元”）按精确零计（半步长为 0）；
    带小数位的零（如“0.00 万元”）保留其显示末位精度。
    """
    if record.get("source") == "explicit_zero" and record.get("value_wan") == 0:
        return Decimal(0)
    raw = str(record.get("raw") or "").strip()
    if not raw:
        return Decimal(0)
    if record.get("value_wan") == 0 and "." not in raw:
        return Decimal(0)
    unit = str(record.get("unit") or "万元")
    factor = _AMOUNT_UNIT_FACTOR.get(unit, Decimal("1"))
    return display_half_unit(raw) * factor


def _records_display_envelope(
    records: Sequence[Dict[str, Any]],
    total_record: Optional[Dict[str, Any]] = None,
    extra_raws: Sequence[str] = (),
) -> Decimal:
    """按各项原文末位与单位系数折算的累计舍入包络 + 0.01 万元政策容差。"""
    half_sum = sum((_record_display_half_unit(record) for record in records), Decimal(0))
    if total_record is not None:
        half_sum += _record_display_half_unit(total_record)
    for raw in extra_raws:
        if raw:
            half_sum += display_half_unit(raw)
    return half_sum + AMOUNT_TOLERANCE_WAN


_DECL_BUDGET_KINDS = {"年初支出预算", "年初预算", "预算"}
_SUBJECT_MARK_RE = re.compile(r"[（(]\s*项\s*[)）]")
_LEVEL_MARK_RE = re.compile(r"[（(]\s*(?:类|款|项)\s*[)）]")
_ITEM_NUM_RE = re.compile(r"^\d{1,2}[、.．]\s*")
_SUBJECT_SUFFIX_RE = re.compile(r"^[^，。；：,;:]{1,40}支出[。]?$")
_SUBJECT_DASH_RE = re.compile(r"^[^，。；：,;:]{1,40}支出\s*[—\-－]")
_NARRATIVE_MARK_RE = re.compile(r"(?:上述|该|本|此|各|相关|前述|以上|列入|除外)[\s“”\"']*$")
# 项级科目后直接跟的金额（“（项）39.99万元”“（项）支出843.41万元”）。
_HEADER_AMOUNT_RE = re.compile(
    r"[（(]\s*项\s*[)）]\s*(?:支出(?:数)?(?:为|是)?\s*[:：]?\s*)?"
    r"(?P<amount>" + _AMOUNT + r")\s*(?P<unit>亿元|万元|元)?"
)
# 例外声明（无此项/零值/依法不予公开）必须绑定到科目和预算阶段：预算限定语
# 不能顺带豁免决算，决算限定语也不能豁免预算。通用“无此项”按业务约定覆盖
# 整个科目，但必须显式记录 scope=subject；带阶段限定语的声明记录 scope=stage。
_STAGE_BUDGET_RE = re.compile(r"年初支出预算|年初预算|年初|预算数|预算")
_STAGE_FINAL_RE = re.compile(r"支出决算|决算数|决算|支出")
_SUBITEM_SCOPE_RE = re.compile(
    r"各\s*[“\"']?\s*项\s*[”\"']?\s*中|下\s*[“\"']?\s*项\s*[”\"']?\s*中"
    r"|下各\s*[“\"']?\s*项|子项|明细"
)
# 否定或未确认的声明不能作为零值或不公开证据，也不能推断真实金额。
_EXCEPTION_UNCERTAINTY_RE = re.compile(
    r"(?:并不是|并不属于|尚未证实|尚未确认|并非|不是|不属于|并未|并不|"
    r"未证实|未确认|未必|不能|不应|是否|可能|疑似)[^。；;，,、]{0,6}$"
)
_EXCEPTION_UNCONFIRMED_SUFFIX_RE = re.compile(
    r"\s*[，,]?\s*[（(]?(?:的(?:说法|情况))?"
    r"(?:尚未确认|尚待确认|未确认|待确认|待核实|待核对|待复核|仍需复核)"
)
_EXCEPTION_PATTERNS = (
    (re.compile(
        r"年初(?:无预算|无此项预算|无此项|预算不含此项|未安排预算|未编制预算|预算为零)"
        r"|无年初预算|未安排年初预算"
    ), "zero", "budget"),
    (re.compile(
        r"无(?:此|该项|本项)?(?:支出|决算)"
        r"|未(?:发生|安排|列支|支出)(?:此|该项|本项)?支出"
    ), "zero", "final"),
    (re.compile(r"(?:本项|该项目|该科目|此科目)?无此项|无此科目|本项不涉及"), "zero", "auto"),
    (re.compile(r"涉密(?:(?![。；;但而却]|不过).){0,30}(?:不予公开|不公开)|依法不予公开"), "withheld", "auto"),
)


def _detail_section(doc: Document):
    """跨页拼接“具体情况”段：返回 [(页, 原文, 平铺, 偏移, 起, 止)]。"""
    blocks = []
    active = False
    for page, text in iter_pages(doc):
        flat, positions = page_flat(doc, page, text)
        flat = normalize_amount_punctuation(flat)
        begin = 0
        if not active:
            match = _DETAIL_START_RE.search(flat)
            if not match:
                continue
            active, begin = True, match.end()
        stop = _DETAIL_STOP_RE.search(flat, begin)
        end = stop.start() if stop else len(flat)
        blocks.append((page, text, flat, positions, begin, end))
        if stop:
            break
    return blocks


def _decl_kind(kind: str) -> str:
    return "budget" if kind.strip() in _DECL_BUDGET_KINDS else "final"


def _is_subject_item(body: str) -> bool:
    """编号条目是否为项级科目条目；说明原因的编号列表不算应披露集合。"""
    head = re.split(r"主要用于|。", body, maxsplit=1)[0].strip()
    head = head.replace("“", "").replace("”", "").replace('"', "")
    head = _ITEM_NUM_RE.sub("", head)
    if _LEVEL_MARK_RE.search(head):
        return True
    if _SUBJECT_DASH_RE.match(head):
        return True
    return bool(_SUBJECT_SUFFIX_RE.match(head.rstrip("。")))


def _subject_clauses(joined: str, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按真正的项级科目头拆槽位，不把“除列入上述（项）外”当成新科目。"""
    clauses: List[Dict[str, Any]] = []
    for item in items:
        start, stop = int(item["pos"]), int(item["stop"])
        body = joined[start:stop]
        if not _is_subject_item(body):
            continue
        accepted = []
        for mark in _SUBJECT_MARK_RE.finditer(body):
            boundary = accepted[-1].start() if accepted else 0
            sentence_tail = re.split(r"[。；;]", body[boundary:mark.start()])[-1]
            if _NARRATIVE_MARK_RE.search(sentence_tail):
                continue
            if (_LEVEL_MARK_RE.search(sentence_tail)
                    or re.search(r"支出\s*$", sentence_tail)
                    or _SUBJECT_DASH_RE.search(sentence_tail)):
                accepted.append(mark)
        if not accepted:
            clauses.append({"start": start, "end": stop, "label": body[:60],
                            "item_start": start, "item_end": stop})
            continue
        for index, mark in enumerate(accepted):
            clause_start = 0 if index == 0 else mark.start()
            clause_end = accepted[index + 1].start() if index + 1 < len(accepted) else len(body)
            clauses.append({
                "start": start + clause_start,
                "end": start + clause_end,
                "label": body[max(0, mark.start() - 60):mark.start() + 40],
                "item_start": start,
                "item_end": stop,
            })
    return clauses


def _segment_bounds(text: str, pos: int) -> Tuple[int, int]:
    """例外声明所在的分句（逗号也切分）：后文另一个阶段的金额不属于本声明。"""
    start = 0
    end = len(text)
    for boundary in re.finditer(r"[。；;，,、]|然而|不过|但是|但|而|却", text):
        if boundary.end() <= pos:
            start = boundary.end()
        elif boundary.start() >= pos:
            end = boundary.start()
            break
    return start, end


def _exception_stages(
    sentence: str,
    match_start: int,
    match_end: int,
    hint: str,
) -> Tuple[List[str], str]:
    """例外声明覆盖哪些阶段；只认离声明最近的阶段词，避免被同句另一阶段带偏。"""
    if hint in {"budget", "final"}:
        return [hint], "stage"
    nearby = set()
    tokens = False
    for regex, stage in ((_STAGE_BUDGET_RE, "budget"), (_STAGE_FINAL_RE, "final")):
        for token in regex.finditer(sentence):
            tokens = True
            if token.end() <= match_start:
                distance = match_start - token.end()
            elif token.start() >= match_end:
                distance = token.start() - match_end
            else:
                distance = 0
            if distance <= 12:
                nearby.add(stage)
    if len(nearby) == 2:
        # 仅邻近不足以证明共同作用域；两个阶段须以并列结构共同修饰声明。
        prefix = sentence[:match_start]
        joint = re.search(
            r"(?P<first>年初预算|预算数|预算|支出决算|决算数|决算)\s*"
            r"(?:和|与|及|以及|、)\s*"
            r"(?P<second>年初预算|预算数|预算|支出决算|决算数|决算)\s*$",
            prefix,
        )
        if joint:
            pair = {
                "budget" if _STAGE_BUDGET_RE.fullmatch(joint.group(name)) else "final"
                for name in ("first", "second")
            }
            if pair == {"budget", "final"}:
                return sorted(pair), "stage"
        return [], "ambiguous"
    if nearby:
        return sorted(nearby), "stage"
    if tokens:
        # 同句有阶段词但都离得太远：作用域无法证明，保留待复核，不自动豁免。
        return [], "ambiguous"
    return ["budget", "final"], "subject"


def _clause_exceptions(text: str, text_start: int, item_head: str) -> List[Dict[str, Any]]:
    claimed: List[Tuple[int, int]] = []
    exceptions: List[Dict[str, Any]] = []
    for pattern, reason, hint in _EXCEPTION_PATTERNS:
        for match in pattern.finditer(text):
            if any(not (match.end() <= start or match.start() >= end) for start, end in claimed):
                continue
            claimed.append((match.start(), match.end()))
            sentence_start, sentence_end = _segment_bounds(text, match.start())
            sentence = text[sentence_start:sentence_end]
            uncertainty = _EXCEPTION_UNCERTAINTY_RE.search(text[sentence_start:match.start()])
            suffix_uncertainty = _EXCEPTION_UNCONFIRMED_SUFFIX_RE.match(text[match.end():])
            stages, scope = _exception_stages(
                sentence, match.start() - sentence_start, match.end() - sentence_start, hint
            )
            if scope == "subject" and (
                re.match(r"[，,]?\s*(?:但|而|不过|然而|却)", text[sentence_end:])
                or re.search(r"(?:但|而|不过|然而|却)[，,]?\s*$", text[:sentence_start])
            ):
                # 转折两侧不能由无阶段限定的片段推定整个科目都被豁免。
                stages, scope = [], "ambiguous"
            if reason == "withheld" and not (uncertainty or suffix_uncertainty):
                # “年初预算涉密，决算不公开”这类跨逗号声明：声明自身提到的阶段
                # 也要算进来，不能只看第一个分句。
                phrase = text[match.start():match.end()]
                # 跨逗号仅在两个阶段各有明确保密谓语时共同覆盖。
                if re.fullmatch(r"涉密[，,]\s*(?:支出决算|决算数|决算|年初预算|预算数|预算)不公开", phrase):
                    if _STAGE_BUDGET_RE.search(phrase) and "budget" not in stages:
                        stages.append("budget")
                        scope = "stage"
                    if _STAGE_FINAL_RE.search(phrase) and "final" not in stages:
                        stages.append("final")
                        scope = "stage"
            if uncertainty or suffix_uncertainty:
                # 若不确定声明明确限定单一阶段，另一阶段仍可独立检查。
                if scope != "stage" or len(stages) != 1:
                    stages = []
                scope = "ambiguous"
            if scope != "ambiguous" and _SUBITEM_SCOPE_RE.search(sentence):
                # 子项限定只改变科目层级，不得扩大已经确定的预算阶段。
                scope = "subitems"
            evidence_start = sentence_start + uncertainty.start() if uncertainty else match.start()
            evidence_end = match.end() + suffix_uncertainty.end() if suffix_uncertainty else match.end()
            exceptions.append({
                "reason": reason, "stages": stages, "scope": scope,
                "text": text[evidence_start:evidence_end], "pos": text_start + evidence_start,
                "item_head": item_head,
            })
    return exceptions


def _exception_evidence(exceptions: Sequence[Dict[str, Any]], locate) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    for exception in exceptions:
        page, text, start, end = locate(int(exception["pos"]))
        record = {
            "kind": exception["reason"],
            "stages": list(exception["stages"]),
            "scope": exception["scope"],
            "item_head": exception["item_head"],
            "text": exception["text"],
            "page": page,
            "span": text[start:end],
        }
        for flag in ("conflict", "consistent_zero", "applies_to"):
            if exception.get(flag):
                record[flag] = exception[flag]
        evidence.append(record)
    return evidence


_GROUP_ENUM_RE = re.compile(r"其中\s*[:：]")
# 共享“其中”分项：分项金额前显式写明的阶段词（年初预算/支出决算…）必须保留，
# 组级合计的存在不能倒推分项角色；裸金额按本段落（支出决算具体情况）的决算口径记账。
_GROUP_ITEM_AMOUNT_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fff“\"'（(][\u4e00-\u9fff“”\"'（）()]{1,39}?)"
    r"(?P<stage>年初支出预算|年初预算|支出决算|决算数|决算支出|决算)?"
    r"(?:数)?(?:为|是)?\s*[:：]?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)"
)
#: 枚举句中的任意金额出现位置：用于核对每个金额都已归入分项或组级合计。
_GROUP_AMOUNT_OCCURRENCE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NON_AMOUNT_NUM_SUFFIX_RE = re.compile(
    r"^\s*(?:[%％]|年度?|月份?|日|分?项|个|名|人|辆|台|次|批次?|团组|家|户)"
)
_ENUM_ORDINAL_FRAGMENT_RE = re.compile(
    r"^(?:[（(]\s*(?:\d{1,2}|[一二三四五六七八九十]+)\s*[)）]"
    r"|(?:\d{1,2}|[一二三四五六七八九十]+)[、.．])$"
)
_SHARE_ANNOTATION_RE = re.compile(
    r"^(?:占(?:比|该类|该款|本项|本组|总计|合计|总额|支出|预算|决算)?(?:为)?"
    r"|占比(?:为)?|完成(?:年初|全年)?预算的)\d[\d,]*(?:\.\d+)?[%％]$"
)


def _is_non_amount_number(text: str, start: int, end: int) -> bool:
    """判断枚举句中的数字是否属百分比/年份/计数/序号，而非缺失单位的金额。"""
    token = text[start:end]
    before = text[:start]
    after = text[end:]
    if _NON_AMOUNT_NUM_SUFFIX_RE.match(after):
        return True
    if len(token) <= 2 and "." not in token:
        if re.search(r"[（(]\s*$", before) and re.match(r"^\s*[)）]", after):
            return True
        if (
            not before or re.search(r"[，,；;：:、\s]$", before)
        ) and re.match(r"^[、.．][\u4e00-\u9fff“\"（(]", after):
            return True
    return False


_GROUP_ENUM_PREFIX_STAGE_RE = re.compile(
    r"^\s*(年初支出预算|年初预算|支出决算|决算数|决算支出|决算)数?\s*[:：]"
)


def _clause_subject_name(joined: str, clause: Dict[str, Any]) -> str:
    """从一个项级子句原文取科目名。

    子句边界切在下一个“（项）”标记处，因此：
    - 正文含首个“（项）”标记：科目名在该标记之前；
    - 正文以“（项）”开头（上一子句的标记落在本段开头）：科目名在标记之后。
    """
    body = joined[int(clause["start"]):int(clause["end"])]
    markers = list(re.finditer(r"[（(]项[)）]", body))
    if markers:
        first = markers[0]
        head = body[:first.start()] if first.start() > 0 else body[first.end():]
    else:
        return ""
    for sep in ("（款）", "(款)"):
        index = head.rfind(sep)
        if index >= 0:
            head = head[index + len(sep):]
    for sep in ("、", "。", "；", ";"):
        index = head.rfind(sep)
        if index >= 0:
            head = head[index + len(sep):]
    head = re.sub(r"^\s*\d{1,2}[、.．]\s*", "", head)
    return re.sub(r"[\s，,。；;：:]+", "", head)


# 已核准的科目同义片段：差异必须落在名称结尾、且前缀完全一致才可绑定。
# 这是固定词表，不是通用前缀相似度阈值；新变体必须逐对核准并检查歧义。
_GROUP_SUBJECT_VARIANTS = frozenset({("养老", "离退休")})


def _group_subject_norm(value: str) -> str:
    return re.sub(r"(?:支出|经费)$", "", re.sub(r"\s+", "", value or ""))


def _group_subject_variant_match(left: str, right: str) -> bool:
    """同义片段替换：两侧共同前缀之后、名称结尾处的片段在核准表内。"""
    common = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        common += 1
    for cut in range(1, common + 1):
        pair = (left[cut:], right[cut:])
        if pair in _GROUP_SUBJECT_VARIANTS or pair[::-1] in _GROUP_SUBJECT_VARIANTS:
            return True
    return False


def _group_subject_name_matches(left: str, right: str) -> bool:
    """共享“其中”分项的科目名与子句科目名：只认文本上可证明的对应。

    可证明的形态（全部满足才绑定金额身份）：
    - 归一（去空白、去句尾“支出/经费”）后完全一致；
    - 一方是另一方的前缀省略（较短名 ≥4 字），如“机关事业单位职业年金”
      省略“缴费支出”后缀；
    - 差异是已核准同义片段（``_GROUP_SUBJECT_VARIANTS``）在结尾的替换。
    名称相似但不属于上述形态的，只作候选提示（见
    ``_group_subject_name_candidate``），不确认金额身份。
    """
    left, right = _group_subject_norm(left), _group_subject_norm(right)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 4 and longer.startswith(shorter):
        return True
    return _group_subject_variant_match(left, right)


def _group_subject_name_candidate(left: str, right: str) -> bool:
    """名称相似但身份未证实的候选（仅用于提示，不参与金额绑定）。"""
    left, right = _group_subject_norm(left), _group_subject_norm(right)
    if not left or not right or left == right:
        return False
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 4 and longer.startswith(shorter):
        return False
    if _group_subject_variant_match(left, right):
        return False
    common = 0
    for a, b in zip(shorter, longer, strict=False):
        if a != b:
            break
        common += 1
    return len(shorter) >= 6 and common >= 6 and common / len(shorter) >= 0.75


_LEADING_CONNECTOR_RE = re.compile(
    r"^(?:其中|主要(?:包括|包含|为|有)|具体(?:包括|包含|为)|包括|包含|分别为|有)"
)
_TRAILING_STAGE_RE = re.compile(
    r"(?:年初支出预算|年初预算|支出决算|决算数|决算支出|决算)(?:数)?(?:为|是)?$"
)
_TRAILING_QUALIFIER_RE = re.compile(
    r"[（(]\s*(?:类|款|项|年初支出预算|年初预算|支出决算|决算数|决算支出|决算|预算|决算)\s*[)）]$"
)
_TEXT_PLACEHOLDER_WORDS = (
    r"待定|待填|待核实|待核|待确认|待补充|待补|待查|暂未确定|暂未明确|"
    r"暂缺|暂空|暂无|未列明|未列|未填|未定|未知|未明|不详|空缺|空白|缺省"
)
_SINGLE_CHAR_PLACEHOLDERS = frozenset({"无", "略", "空", "缺"})
_PLACEHOLDER_ONLY_RE = re.compile(
    rf"^[“”\"'（）()]*(?:[—–―\-－/×*?？]+(?:亿元|万元|元)?|"
    rf"[Nn]/[Aa]|NA|[无略空缺]|{_TEXT_PLACEHOLDER_WORDS})[“”\"'（）()]*$"
)
_PLACEHOLDER_SUFFIX_RE = re.compile(
    rf"(?:[—–―\-－/×*?？]+(?:亿元|万元|元)?"
    rf"|[（(]\s*(?:[—–―\-－/×*?？]+|[Nn]/[Aa]|NA|[无略空缺]|{_TEXT_PLACEHOLDER_WORDS})\s*[)）](?:亿元|万元|元)?"
    rf"|(?:[Nn]/[Aa]|NA|{_TEXT_PLACEHOLDER_WORDS})(?:亿元|万元|元)?)$"
)
_NOTE_PREFIX_RE = re.compile(r"^(?:注|备注|说明)\s*[：:]\s*")
_ENUM_BACKREF_START_RE = re.compile(
    r"^(?:以上|上述|前款|前述|所列|该组|组内|本组)"
    r"(?:各?分?项)?(?:金额|数额|数字|数据|数值|经费|资金|支出|预算|决算)?"
)
_EXPLICIT_SUBITEM_BACKREF_RE = re.compile(
    r"^(?:以上|上述|前款|前述|所列)(?:(?:各|全部|所有|[一二三四五六七八九十两\d]+)?(?:个)?分项|其中(?:各?分?项)?)"
)
_ENUM_BACKREF_IMPLICIT_START_RE = re.compile(
    r"^(?:均(?:为|系|属|含|不含|未含|不包括|未包括|未列|按)|"
    r"全部(?:为|系|属|按)|"
    r"其余|另有|另外|含上年|不含上年|不包括|未包括|未含)"
)
_ENUM_BACKREF_PURPOSE_RE = re.compile(
    r"^(?:以上|上述|前款|前述|所列|该组|组内|本组)"
    r"(?:各?分?项)?(?:支出|经费|资金|项目)?(?:主要)?用于"
)
_ENUM_BACKREF_QUALIFIER_CUE_RE = re.compile(
    r"不含|未含|不包括|未包括|包括|含|未列|未纳入|扣除|剔除|其余|另|估计|暂按|暂列|"
    r"部分|口径|均系|均为|均属|仅|结转|结余|决算|预算|金额|数额|数据|数字|为准|合计|总计"
)
_STAGE_REAFFIRM_RE = re.compile(
    r"^(?:"
    r"(?P<lead>以上|上述|以下|前款|前述|所列|该组|组内|本组)"
    r"(?:(?:各|全部|所有|[一二三四五六七八九十两\d]+)?(?:个)?(?:分?项|科目|条目))?"
    r"(?:金额|数额|数字|数据|数值|经费|资金|支出)?(?:均|全部|皆|都)?"
    r"|(?:(?:各|全部|所有|[一二三四五六七八九十两\d]+)?(?:个)?(?:分?项|科目|条目))?"
    r"(?:金额|数额|数字|数据|数值)(?:均|全部|皆|都)?"
    r"|(?:均|全部|皆|都)"
    r")(?:为|系|属)"
    r"(?:(?:本|当|\d{4})年度?|\d{4}年)?"
    r"(?:一般公共预算)?(?:财政拨款)?"
    r"(?P<stage>年初支出预算|年初预算|预算|支出决算|决算支出|决算)"
    r"(?:数|金额|数字|数据|口径)?$"
)
_PROSE_NOTE_RE = re.compile(
    r"以上|上述|均为|其余|如下|说明|金额|情况|原因|详见|注[：:]|无变动|暂无|另行|决算数|预算数"
    r"|不含|未含|不包括|未包括|结转|结余|口径|扣除|剔除|为准|估列|暂列|暂按|补充|附表|按规定|暂缓|安排|拨付|列支"
    r"|同比|较上年|比上年|持平"
)
_PURE_YOY_STATUS_RE = re.compile(
    r"^(?:(?:以上|上述|前款|前述|所列|该组|组内|本组)(?:(?:各|全部|所有|[一二三四五六七八九十两\d]+)?(?:个)?(?:分?项|各项|科目|条目|金额|数额|数字|数据|数值|支出))?"
    r"|(?:各?分?项|各项|金额|数额|数字|数据|数值))?"
    r"(?:均|皆|都)?"
    r"(?:同比(?:(?:与|较|比|同)[上去]年(?:同期)?(?:相比)?)?|(?:与|较|比|同)[上去]年(?:同期)?(?:相比)?)"
    r"(?:基本|大体|大致|略有|有所|略微|稍有)?"
    r"(?:持平|保持一致|一致|相同|相等|无变化|无变动|无增减|未变化|未变动|未增减|没有变化|没有变动|未变|增长|增加|上升|减少|下降|降低)$"
)
_EXTRA_SUBJECT_SUFFIX_RE = re.compile(
    r"^(?:(?:缴费|运行|基本|专项|业务|行政|事业|补贴|补助|保险|事务|管理|支出|经费)+"
    r"|(?:费|金))$"
)


def _is_pure_yoy_status_note(text: str) -> bool:
    """判断片段是否仅陈述同比/较上年持平或定性增减状态，不涉及范围、口径或分项缺漏。"""
    clean = _strip_outer_delims(text)
    return bool(clean and _PURE_YOY_STATUS_RE.fullmatch(clean))


def _match_stage_reaffirm_note(text: str, *, is_leading: bool) -> Optional[str]:
    """仅识别只重申同阶段口径、不增减分项的说明句，返回 ``'budget'|'final'``。"""
    clean = _strip_outer_delims(text)
    paren_match = re.search(r"[（(]([^（）()]+)[)）]$", clean)
    if paren_match and _is_pure_yoy_status_note(paren_match.group(1)):
        clean = clean[:paren_match.start()].strip()
    match = _STAGE_REAFFIRM_RE.fullmatch(clean)
    if not match:
        return None
    lead = match.group("lead") or ""
    if is_leading and lead in {"以上", "上述", "前款", "前述", "所列"}:
        return None
    if not is_leading and lead == "以下":
        return None
    return _decl_kind(match.group("stage"))


def _strip_outer_delims(text: str) -> str:
    """剥离整句外层引号与最外层配对括号（以及被句号切开的单侧前括号），保留句尾内嵌括号附注。"""
    cleaned = text.strip().strip("“”\"'")
    while len(cleaned) >= 2 and (
        (cleaned[0] == "（" and cleaned[-1] == "）")
        or (cleaned[0] == "(" and cleaned[-1] == ")")
    ):
        inner = cleaned[1:-1]
        depth = 0
        balanced = True
        for ch in inner:
            if ch in "（(":
                depth += 1
            elif ch in "）)":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        cleaned = inner.strip().strip("“”\"'")
    if cleaned and cleaned[0] in "（(" and not any(ch in "）)" for ch in cleaned):
        cleaned = cleaned.lstrip("（(").strip()
    return cleaned


def _sentence_has_enum_backref(body: str, *, explicit_subitem_only: bool) -> bool:
    """按子句检查附注是否包含明确指向本组枚举的口径说明，不被同句其他同比子句连带丢弃。"""
    raw_clauses = re.split(r"[，,]+", body)
    start_re = (
        _EXPLICIT_SUBITEM_BACKREF_RE if explicit_subitem_only else _ENUM_BACKREF_START_RE
    )
    for index, raw_clause in enumerate(raw_clauses):
        clause = _strip_outer_delims(raw_clause)
        if index == 0:
            note_match = _NOTE_PREFIX_RE.match(clause)
            if note_match:
                clause = _strip_outer_delims(clause[note_match.end():])
        if not clause:
            continue
        core = re.sub(r"[（(][^（）()]{1,20}[)）]", "", clause).strip()
        if (
            not core
            or _ENUM_BACKREF_PURPOSE_RE.match(core)
            or re.match(r"^(?:主要)?用于", core)
            or re.search(r"(?:较|比)上年|同比", core)
        ):
            continue
        if (
            start_re.match(core)
            or (not explicit_subitem_only and _ENUM_BACKREF_IMPLICIT_START_RE.match(core))
        ) and _ENUM_BACKREF_QUALIFIER_CUE_RE.search(core):
            return True
    return False


def _trailing_enum_backrefs(joined: str, enum_end: int, item_end: int) -> List[Tuple[int, str]]:
    """紧接“其中：…。”之后、同一编号条目内明确回指该枚举的独立句，返回 ``[(起偏移, 原句)]``。"""
    sentences: List[Tuple[int, str]] = []
    cursor = enum_end
    while cursor < item_end:
        while cursor < item_end and joined[cursor] in "。；;”\"')） \t\r\n":
            cursor += 1
        if cursor >= item_end:
            break
        sep_match = re.search(r"[。；;]", joined[cursor:item_end])
        stop = cursor + sep_match.start() if sep_match is not None else item_end
        raw_sentence = joined[cursor:stop]
        decl = _DECL_RE.search(raw_sentence)
        decl_truncated = False
        if decl is not None:
            raw_sentence = raw_sentence[:decl.start()].rstrip("，,、 \t\r\n")
            decl_truncated = True
            if not raw_sentence:
                break
        cleaned = _strip_outer_delims(raw_sentence)
        note_match = _NOTE_PREFIX_RE.match(cleaned)
        body = cleaned[note_match.end():] if note_match else cleaned
        if not body:
            break
        if _sentence_has_enum_backref(body, explicit_subitem_only=False):
            sentences.append((cursor, raw_sentence))
            if decl_truncated:
                cursor = stop + 1
                break
            cursor = stop + 1
            continue
        break
    # 组级合计之后若仍有明确指向“分项/其中”的同组附注，一并收回该组枚举，避免漏掉后置分项口径说明。
    seen_offsets = {pos for pos, _ in sentences}
    scan = cursor
    while scan < item_end:
        while scan < item_end and joined[scan] in "。；;”\"')） \t\r\n":
            scan += 1
        if scan >= item_end:
            break
        sep_match = re.search(r"[。；;]", joined[scan:item_end])
        stop = scan + sep_match.start() if sep_match is not None else item_end
        if scan in seen_offsets:
            scan = stop + 1
            continue
        raw_sentence = joined[scan:stop]
        decl = _DECL_RE.search(raw_sentence)
        if decl is not None:
            raw_sentence = raw_sentence[:decl.start()].rstrip("，,、 \t\r\n")
        if raw_sentence:
            cleaned = _strip_outer_delims(raw_sentence)
            note_match = _NOTE_PREFIX_RE.match(cleaned)
            body = cleaned[note_match.end():] if note_match else cleaned
            if body and _sentence_has_enum_backref(body, explicit_subitem_only=True):
                sentences.append((scan, raw_sentence))
                seen_offsets.add(scan)
        scan = stop + 1
    return sentences


def _iter_gap_fragments(
    gap: str, gap_start: int, *, is_leading: bool
) -> List[Tuple[str, bool, int, int]]:
    """将缺口拆为 ``(片段, 是否带注前缀, joined起偏移, joined止偏移)``。"""
    items: List[Tuple[str, bool, int, int]] = []
    for clause_match in re.finditer(r"[^，,；;、。]+", gap):
        raw_clause = clause_match.group(0)
        left_trim = len(raw_clause) - len(raw_clause.lstrip())
        right_trim = len(raw_clause) - len(raw_clause.rstrip())
        c_start = clause_match.start() + left_trim
        c_end = clause_match.end() - right_trim
        if c_start >= c_end:
            continue
        clause_text = gap[c_start:c_end]
        compact_clause = re.sub(r"\s+", "", clause_text)
        stripped_clause = _strip_outer_delims(compact_clause)
        note_match = _NOTE_PREFIX_RE.match(stripped_clause)
        if note_match:
            remainder = stripped_clause[note_match.end():]
            if (
                _match_stage_reaffirm_note(remainder, is_leading=is_leading) is not None
                or _is_pure_yoy_status_note(remainder)
            ):
                items.append((compact_clause, True, gap_start + c_start, gap_start + c_end))
                continue
        for piece_match in re.finditer(r"[^：:]+", clause_text):
            piece_raw = piece_match.group(0)
            p_left = len(piece_raw) - len(piece_raw.lstrip())
            p_right = len(piece_raw) - len(piece_raw.rstrip())
            p_start = gap_start + c_start + piece_match.start() + p_left
            p_end = gap_start + c_start + piece_match.end() - p_right
            compact_piece = re.sub(r"\s+", "", piece_raw)
            if compact_piece:
                items.append((compact_piece, False, p_start, p_end))
    return items


def _strip_gap_placeholder(
    fragment: str,
    p_start: int,
    p_end: int,
    space_before: Optional[set[int]],
) -> Tuple[str, bool]:
    """剥离片段尾部的横线/括号占位/文字占位词（含原件空格分隔的单字占位），返回 ``(前缀, 是否剥离)``。"""
    unquoted = fragment.strip("“”\"'")
    lead_q = len(fragment) - len(fragment.lstrip("“”\"'"))
    trail_q = len(fragment) - len(fragment.rstrip("“”\"'"))
    u_start = p_start + lead_q
    u_end = p_end - trail_q
    while len(unquoted) >= 2 and (
        (unquoted[0] == "（" and unquoted[-1] == "）")
        or (unquoted[0] == "(" and unquoted[-1] == ")")
    ):
        unquoted = unquoted[1:-1]
        u_start += 1
        u_end -= 1

    without = _PLACEHOLDER_SUFFIX_RE.sub("", unquoted)
    if without != unquoted:
        return without, True
    if space_before and len(unquoted) >= 2 and u_end - u_start == len(unquoted):
        # 原件以水平空白分隔的单字占位词（如“某科目 无”“某科目 空”）在平铺串里被拼接，
        # 仅当原件该单字前确有水平空白时才剥离，不误伤词尾汉字（如“防空”）。
        unit_trim = re.sub(r"(?:亿元|万元|元)$", "", unquoted)
        char_idx = u_start + len(unit_trim) - 1
        if unit_trim and unit_trim[-1] in _SINGLE_CHAR_PLACEHOLDERS and char_idx in space_before:
            return unit_trim[:-1], True
    return fragment, False


def _group_declared_subjects(
    joined: str,
    item_start: int,
    enum_start: int,
    group_clauses: Sequence[Dict[str, Any]],
) -> List[str]:
    """提取本编号条目内已列示的全部项级科目名，用于核对枚举缺金额片段是否属于已知科目。"""
    names: List[str] = []
    header = joined[item_start:enum_start]
    for mark in _SUBJECT_MARK_RE.finditer(header):
        prefix = header[:mark.start()]
        sentence_tail = re.split(r"[。；;]", prefix)[-1]
        if _NARRATIVE_MARK_RE.search(sentence_tail):
            continue
        head = prefix
        for sep in ("（类）", "(类)", "（款）", "(款)", "（项）", "(项)", "、", "。", "；", ";", "，", ","):
            idx = head.rfind(sep)
            if idx >= 0:
                head = head[idx + len(sep):]
        head = re.sub(r"^\s*\d{1,2}[、.．]\s*", "", head)
        cleaned = re.sub(r"[\s，,。；;：:]+", "", head)
        if cleaned and cleaned not in names:
            names.append(cleaned)
    for clause in group_clauses:
        name = _clause_subject_name(joined, clause)
        if name and name not in names:
            names.append(name)
    return names


def _match_group_subject(fragment: str, group_subject_names: Sequence[str]) -> Optional[str]:
    """判断枚举缺口片段是否对应到本组已列项级科目；仅对应上时返回归属后的科目名。"""
    clean = _TRAILING_QUALIFIER_RE.sub("", fragment).strip("“”\"'（）()")
    if not clean:
        return None
    candidates = [clean]
    no_stage = _TRAILING_QUALIFIER_RE.sub("", _TRAILING_STAGE_RE.sub("", clean)).strip("“”\"'（）()")
    if no_stage and len(no_stage) >= 4:
        candidates.append(no_stage)
    for base in list(candidates):
        stripped = _LEADING_CONNECTOR_RE.sub("", base)
        if stripped and stripped != base and len(stripped) >= 4:
            candidates.append(stripped)
    for cand in candidates:
        norm_cand = _group_subject_norm(cand)
        if not norm_cand:
            continue
        for subj in group_subject_names:
            norm_subj = _group_subject_norm(subj)
            if not norm_subj:
                continue
            if norm_cand == norm_subj:
                return cand
            if len(norm_cand) < len(norm_subj):
                if len(norm_cand) >= 4 and norm_subj.startswith(norm_cand):
                    return cand
            elif len(norm_cand) > len(norm_subj) and norm_cand.startswith(norm_subj):
                extra = norm_cand[len(norm_subj):]
                if len(norm_subj) >= 4 and _EXTRA_SUBJECT_SUFFIX_RE.fullmatch(extra):
                    return cand
            if _group_subject_variant_match(norm_cand, norm_subj):
                return cand
    return None


def _shared_group_disclosures(
    joined: str,
    clauses: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    space_before: Optional[set[int]] = None,
) -> Dict[str, Any]:
    """识别“多个（项）共享一段‘其中：’分项金额”的组级披露。

    只在同一编号条目内、确有“其中：”枚举、且至少两个项级子句时生效。
    返回：
    - ``final_disclosed``：已由共享枚举逐项列示决算金额的项级子句起点；
    - ``budget_not_itemized``：只有组级年初预算、未逐项列示的子句起点；
    - ``components``：组内分项金额记录位置（不计入章节分项加总）；
    - ``totals``：组级合计金额记录位置（计入章节加总一次）；
    - ``untrusted_totals``：组内分项之和与组级合计不一致的合计记录位置；
    - ``group_checks``：逐组、逐阶段的科目绑定与算术核对记录。

    金额角色规则（2026-11-17 第二次独立复核 RR-01）：
    - 分项角色从原文提取并逐项保留；组级合计的存在不倒推分项角色；
    - 枚举句以阶段词开头（“其中：年初预算：…”）时裸金额跟随该阶段；
    - 枚举全部为裸金额时按本段落（支出决算具体情况）的决算口径记账；
    - 显式阶段词与裸金额混排时，裸金额角色无法证明，不参与组内算术，
      单独记入 ``unresolved_role_components``；预算与决算分别生成组回执。
    """
    result: Dict[str, Any] = {
        "final_disclosed": set(),
        "budget_disclosed": set(),
        "budget_not_itemized": set(),
        "components": set(),
        "enum_records": set(),
        "totals": set(),
        "untrusted_totals": set(),
        "section_components": [],
        "group_checks": [],
    }
    by_item: Dict[int, List[Dict[str, Any]]] = {}
    for clause in clauses:
        item_start = clause.get("item_start")
        if isinstance(item_start, int):
            by_item.setdefault(item_start, []).append(clause)
    record_by_pos = {int(record["pos"]): record for record in records}
    for item_start, group in by_item.items():
        if len(group) < 2:
            continue
        item_end = max(int(clause["end"]) for clause in group)
        text = joined[item_start:item_end]
        enum = _GROUP_ENUM_RE.search(text)
        if enum is None:
            continue
        enum_start = item_start + enum.end()
        sentence_end = text.find("。", enum.end())
        enum_end = item_start + (sentence_end if sentence_end >= 0 else len(text))
        enum_text = joined[enum_start:enum_end]
        per_item: List[Dict[str, Any]] = []
        classified_spans: List[Tuple[int, int]] = []
        inline_total_spans: List[Tuple[int, int]] = []
        for match in _GROUP_ITEM_AMOUNT_RE.finditer(enum_text):
            span = (match.start(), match.end())
            value = _dec(match.group("amount"))
            factor = _AMOUNT_UNIT_FACTOR.get(match.group("unit"))
            if value is None or factor is None:
                continue
            name = re.sub(r"^[、，,；;：:\s]+", "", match.group("name") or "")
            name = re.sub(r"\s+", "", name).strip("“”\"'（）()")
            if not name:
                continue
            if name.endswith(("总计", "合计")):
                # 枚举句内夹带的组级合计不是分项：不算共享分项，也不进入
                # 组内算术；保留其跨度以便 total_positions 收录且排除出分项集合。
                classified_spans.append(span)
                inline_total_spans.append((enum_start + span[0], enum_start + span[1]))
                continue
            classified_spans.append(span)
            stage_word = match.group("stage") or ""
            per_item.append({
                "name": name,
                "raw": match.group("amount"),
                "unit": match.group("unit"),
                "value_wan": value * factor,
                "pos": enum_start + match.start(),
                "end": enum_start + match.end(),
                "role": ("budget" if stage_word in _DECL_BUDGET_KINDS else "final")
                if stage_word else None,
                "stage_word": stage_word,
            })
        # 枚举里出现但没能归入分项/组级合计的金额（典型是缺单位）：
        # 分项集合不完整，组内算术不能拿部分和去比组级合计；排除百分比/年份/计数/序号。
        unparsed_amounts = [
            occurrence.group(0)
            for occurrence in _GROUP_AMOUNT_OCCURRENCE_RE.finditer(enum_text)
            if not any(
                start <= occurrence.start() and occurrence.end() <= end
                for start, end in classified_spans
            )
            and not _is_non_amount_number(enum_text, occurrence.start(), occurrence.end())
        ]
        if not per_item:
            continue
        subject_clauses = [
            clause for clause in group
            if int(clause["start"]) < enum_start
        ]
        if len(subject_clauses) < 2:
            subject_clauses = [
                clause for clause in group
                if _clause_subject_name(joined, clause)
            ]
        if len(subject_clauses) < 2:
            continue
        group_subject_names = _group_declared_subjects(
            joined, item_start, enum_start, subject_clauses
        )
        prefix_match = _GROUP_ENUM_PREFIX_STAGE_RE.match(enum_text)
        prefix_end = prefix_match.end() if prefix_match else 0
        # 扫描“其中：”枚举的缺口（含首项前、分项间、末尾及紧跟句号后的同组回指附注）：
        # 1) 无论首/中/尾出现未能归入分项的文本片段或横线等占位符，都不能证明同阶段
        #    分项完整，必须拦截组内差额结论（“—”不得按零算，也不得跳过）；
        # 2) 只有能与本组已列项级科目对应的片段，才确认为“未列金额的科目”；
        # 3) 紧接枚举且明确回指同组的口径附注（无论逗号还是句号引出），同样参与
        #    阶段与可比性判定；
        # 4) 引导语、可能增减分项或改变口径的说明等无法对应的片段，仅保留内部诊断。
        ordered_spans = sorted(classified_spans)
        raw_gaps: List[Tuple[bool, int, str]] = []
        if ordered_spans:
            if ordered_spans[0][0] > prefix_end:
                raw_gaps.append(
                    (True, enum_start + prefix_end, enum_text[prefix_end:ordered_spans[0][0]])
                )
            for previous, following in zip(ordered_spans, ordered_spans[1:], strict=False):
                raw_gaps.append(
                    (False, enum_start + previous[1], enum_text[previous[1]:following[0]])
                )
            raw_gaps.append(
                (False, enum_start + ordered_spans[-1][1], enum_text[ordered_spans[-1][1]:])
            )
            for sent_pos, raw_sentence in _trailing_enum_backrefs(joined, enum_end, item_end):
                raw_gaps.append((False, sent_pos, raw_sentence))
        missing_amount_subjects: List[str] = []
        unparsed_subject_like: List[str] = []
        unparsed_prose: List[str] = []
        unparsed_placeholders: List[str] = []
        stage_reaffirm_notes: List[Dict[str, str]] = []
        for is_leading, gap_start, gap in raw_gaps:
            for fragment, is_prefixed_note, p_start, p_end in _iter_gap_fragments(
                gap, gap_start, is_leading=is_leading
            ):
                if re.fullmatch(r"[“”\"'（）()]+", fragment):
                    continue
                if _ENUM_ORDINAL_FRAGMENT_RE.fullmatch(fragment):
                    continue
                if _SHARE_ANNOTATION_RE.fullmatch(fragment.strip("“”\"'（）()")):
                    continue
                if is_prefixed_note:
                    stripped = _strip_outer_delims(fragment)
                    note_match = _NOTE_PREFIX_RE.match(stripped)
                    note_body = stripped[note_match.end():] if note_match else stripped
                    reaffirmed = _match_stage_reaffirm_note(note_body, is_leading=is_leading)
                    if reaffirmed is not None:
                        stage_reaffirm_notes.append({"text": fragment, "stage": reaffirmed})
                        continue
                    if _is_pure_yoy_status_note(note_body):
                        continue
                # 含数字且已被 unparsed_amounts 记录的片段（如缺单位“492.39”）交由金额缺失链路处理。
                if re.search(r"\d", fragment) and unparsed_amounts:
                    continue
                without_placeholder, had_placeholder = _strip_gap_placeholder(
                    fragment, p_start, p_end, space_before
                )
                if had_placeholder:
                    matched_subject = (
                        _match_group_subject(without_placeholder, group_subject_names)
                        if without_placeholder
                        else None
                    )
                    if matched_subject:
                        missing_amount_subjects.append(matched_subject)
                    elif re.fullmatch(r"[“”\"'（）()\u4e00-\u9fff]{2,}", without_placeholder):
                        if _PROSE_NOTE_RE.search(without_placeholder):
                            unparsed_prose.append(fragment.strip("“”\"'（）()"))
                        else:
                            unparsed_subject_like.append(without_placeholder.strip("“”\"'（）()"))
                    else:
                        unparsed_placeholders.append(fragment)
                    continue
                if _PLACEHOLDER_ONLY_RE.fullmatch(fragment):
                    unparsed_placeholders.append(fragment)
                    continue
                reaffirmed = _match_stage_reaffirm_note(fragment, is_leading=is_leading)
                if reaffirmed is not None:
                    stage_reaffirm_notes.append({"text": fragment, "stage": reaffirmed})
                    continue
                if _is_pure_yoy_status_note(fragment):
                    # 单纯同比/较上年持平或增减状态说明不改变同阶段分项范围与金额完整性，
                    # 不得阻断已完成口径确认的组内加总核对。
                    continue
                if re.fullmatch(r"[“”\"'（）()\u4e00-\u9fff]{2,}", fragment):
                    matched_subject = _match_group_subject(fragment, group_subject_names)
                    if matched_subject:
                        missing_amount_subjects.append(matched_subject)
                    elif _PROSE_NOTE_RE.search(fragment):
                        unparsed_prose.append(fragment.strip("“”\"'（）()"))
                    else:
                        unparsed_subject_like.append(fragment.strip("“”\"'（）()"))
                else:
                    unparsed_placeholders.append(fragment)
        if (
            len(per_item) < 2
            and not missing_amount_subjects
            and not unparsed_amounts
            and not unparsed_placeholders
        ):
            continue
        missing_amount_subjects = list(dict.fromkeys(missing_amount_subjects))
        unparsed_subject_like = list(dict.fromkeys(unparsed_subject_like))
        unparsed_prose = list(dict.fromkeys(unparsed_prose))
        unparsed_placeholders = list(dict.fromkeys(unparsed_placeholders))
        # 组级合计：枚举句之后仍属同一编号条目的预算/决算声明，以及枚举句内夹带的
        # 组级总计/合计；按阶段分开取。
        total_positions = {
            int(record["pos"]) for record in records
            if record.get("kind") in {"budget", "final"}
            and (
                enum_end <= int(record["pos"]) < item_end
                or any(lo <= int(record["pos"]) < hi for lo, hi in inline_total_spans)
            )
        }
        prefix_stage = None
        if prefix_match:
            prefix_stage = (
                "budget" if prefix_match.group(1) in _DECL_BUDGET_KINDS else "final"
            )
        explicit_roles = {item["role"] for item in per_item if item["role"]}
        note_stages = {note["stage"] for note in stage_reaffirm_notes}
        trailing_note_stage = (
            next(iter(note_stages))
            if len(note_stages) == 1 and prefix_stage is None and not explicit_roles
            else None
        )
        for item in per_item:
            if item["role"] is not None:
                item["role_source"] = "explicit"
            elif prefix_stage is not None:
                item["role"] = prefix_stage
                item["role_source"] = "enum_prefix"
            elif explicit_roles:
                # 显式阶段词与裸金额混排：裸金额角色无法证明，不参与组内算术。
                item["role"] = "unresolved"
                item["role_source"] = "mixed_unresolved"
            elif trailing_note_stage is not None:
                # 紧接枚举的同组附注明确指出全部为预算或决算：按附注所指口径核对。
                item["role"] = trailing_note_stage
                item["role_source"] = "trailing_note"
            else:
                # 全部裸金额：按本段落（支出决算具体情况）的决算口径记账。
                item["role"] = "final"
                item["role_source"] = "section_default"
        stage_items: Dict[str, List[Dict[str, Any]]] = {
            "budget": [item for item in per_item if item["role"] == "budget"],
            "final": [item for item in per_item if item["role"] == "final"],
        }
        unresolved_role_items = [
            item for item in per_item if item["role"] == "unresolved"
        ]
        mixed_roles = bool(stage_items["budget"] and stage_items["final"])
        bindings_by_stage: Dict[str, List[Dict[str, Any]]] = {}

        for stage in ("budget", "final"):
            items = stage_items[stage]
            bindings: List[Dict[str, Any]] = []
            for clause in subject_clauses:
                clause_start, clause_end = int(clause["start"]), int(clause["end"])
                name = _clause_subject_name(joined, clause)
                own = [
                    record for record in records
                    if clause_start <= int(record["pos"]) < clause_end
                    and not (enum_start <= int(record["pos"]) < enum_end)
                    and record.get("kind") == stage
                ]
                matched = [
                    item for item in items
                    if name and _group_subject_name_matches(item["name"], name)
                ]
                matched_ids = {id(item) for item in matched}
                candidates = [
                    item for item in items
                    if name and id(item) not in matched_ids
                    and _group_subject_name_candidate(item["name"], name)
                ]
                bindings.append({
                    "clause_start": clause_start,
                    "name": name,
                    "own": bool(own),
                    "matched": matched,
                    "candidates": candidates,
                })
            # 绑定必须唯一：一个分项只对应一个科目、一个科目只对应一个分项；
            # 多对一或重复名称一律判歧义、全部不绑定（核准别名同样要过此关）。
            ambiguous_names: List[str] = []
            component_bindings: Dict[int, List[int]] = {}
            for index, binding in enumerate(bindings):
                for item in binding["matched"]:
                    component_bindings.setdefault(id(item), []).append(index)
            for item_id, indexes in component_bindings.items():
                if len(set(indexes)) > 1:
                    item = next(entry for entry in items if id(entry) == item_id)
                    ambiguous_names.append(item["name"])
                    for index in set(indexes):
                        bindings[index]["matched"] = [
                            entry for entry in bindings[index]["matched"]
                            if id(entry) != item_id
                        ]
            clause_component_count: Dict[int, int] = {}
            for index, binding in enumerate(bindings):
                clause_component_count[index] = len({id(entry) for entry in binding["matched"]})
            for index, count in clause_component_count.items():
                if count > 1:
                    binding = bindings[index]
                    for entry in binding["matched"]:
                        ambiguous_names.append(entry["name"])
                    binding["matched"] = []
            bindings_by_stage[stage] = bindings
            if not items:
                continue
            total_pos = next(
                (pos for pos in sorted(total_positions)
                 if record_by_pos[pos].get("kind") == stage),
                None,
            )
            total_record = record_by_pos.get(total_pos) if total_pos is not None else None
            component_positions = {
                int(record["pos"]) for record in records
                if enum_start <= int(record["pos"]) < enum_end
                and int(record["pos"]) not in total_positions
                and record.get("kind") == stage
            }
            unbound_subjects = [
                binding["name"] for binding in bindings
                if binding["name"] and not binding["own"] and not binding["matched"]
            ]
            bound_component_ids = {
                id(item) for binding in bindings for item in binding["matched"]
            }
            # 未归属分项如实保留，不再因“标题科目都有对应”整体清空；
            # 组算术成立时它们按组内子项留痕，写入回执原因。
            unbound_components = [
                item["name"] for item in items if id(item) not in bound_component_ids
            ]
            name_candidates = [
                {"component": item["name"], "subject": binding["name"]}
                for binding in bindings
                for item in binding["candidates"]
            ]
            group_sum = sum((item["value_wan"] for item in items), Decimal(0))
            stated_total = total_record.get("value_wan") if total_record else None
            arithmetic_status = "unchecked"
            arithmetic_reason = ""
            conflicting_stage_notes = [
                note["text"] for note in stage_reaffirm_notes
                if mixed_roles or unresolved_role_items or note["stage"] != stage
            ]
            stage_unparsed_prose = list(
                dict.fromkeys([*unparsed_prose, *conflicting_stage_notes])
            )
            stage_unparsed_fragments = list(
                dict.fromkeys([*unparsed_subject_like, *stage_unparsed_prose, *unparsed_placeholders])
            )
            if (
                unresolved_role_items
                or unparsed_amounts
                or missing_amount_subjects
                or stage_unparsed_fragments
            ):
                # 分项集合不完整：枚举里有未写明金额阶段的分项，或有金额/占位符/
                # 无法证明不影响算术的文本片段。无法证明当前 stage 的分项之和
                # 覆盖了该阶段全部构成项，算术只能记为未完成，不得用部分
                # 分项与组级合计比较后生成“金额不符”。
                arithmetic_status = "insufficient_data"
                gaps = []
                if unresolved_role_items:
                    gaps.append("有未写明金额阶段的分项")
                if unparsed_amounts:
                    gaps.append("有未能归入分项的金额")
                if missing_amount_subjects or unparsed_subject_like:
                    gaps.append("有未附金额的科目名")
                if unparsed_placeholders:
                    gaps.append("有横线或占位符等未披露数值")
                if stage_unparsed_prose:
                    gaps.append("有未能归入分项的文本片段")
                arithmetic_reason = (
                    "共享分项枚举不完整（" + "、".join(gaps)
                    + "），同阶段分项集合无法证明完整，无法核对组内算术"
                )
            elif stated_total is None:
                arithmetic_status = "insufficient_data"
                arithmetic_reason = (
                    "共享分项缺少同阶段组级合计，无法核对组内分项算术"
                )
            else:
                # 容差按各分项与组级合计在原文中的显示精度（末位 × 来源单位到万元
                # 的系数）计算：例如“0.0986 亿元”半个末位为 0.5 万元，不能按折算
                # 后的万元小数位重算。
                envelope = _records_display_envelope(items, total_record)
                difference = group_sum - stated_total
                if abs(difference) <= envelope:
                    arithmetic_status = "pass"
                else:
                    arithmetic_status = "fail"
                    arithmetic_reason = (
                        f"组内分项合计{format(group_sum, 'f')}与组级合计"
                        f"{format(stated_total, 'f')}不一致，差{_wan_display(abs(difference))}"
                    )
                    if total_pos is not None:
                        result["untrusted_totals"].add(total_pos)

            # 每个有科目名的子句分别判定；重叠切分产生的无名字段不在这里记缺口。
            target_disclosed = (
                result["final_disclosed"] if stage == "final" else result["budget_disclosed"]
            )
            for binding in bindings:
                if not binding["name"]:
                    continue
                if binding["own"] or binding["matched"]:
                    target_disclosed.add(binding["clause_start"])
                elif stage == "final":
                    # 共享枚举里没有能对应到本科目的决算分项：不能按“数量够了”
                    # 认定已披露，保留具体缺口。
                    for clause in subject_clauses:
                        if int(clause["start"]) == binding["clause_start"]:
                            clause["group_component_unbound"] = True
                            break
                elif total_pos is None:
                    for clause in subject_clauses:
                        if int(clause["start"]) == binding["clause_start"]:
                            clause["group_component_unbound_budget"] = True
                            break

            unmatched_group_subjects = [
                subj for subj in group_subject_names
                if not any(_group_subject_name_matches(item["name"], subj) for item in items)
            ]
            if (
                total_pos is None
                and not unresolved_role_items
                and not unparsed_amounts
                and not missing_amount_subjects
                and not stage_unparsed_fragments
                and not unparsed_placeholders
                and not ambiguous_names
                and not unbound_subjects
                and not unmatched_group_subjects
                and all(_match_group_subject(item["name"], group_subject_names) for item in items)
            ):
                # 无同阶段组级合计、但枚举分项完整对应本组全部科目时，直接把逐项金额
                # 交由章节加总核对，并确认重叠无名子句已完成本阶段披露。
                for clause in subject_clauses:
                    if not _clause_subject_name(joined, clause) and int(clause["end"]) >= enum_start:
                        target_disclosed.add(int(clause["start"]))
                for item in items:
                    already_covered = any(
                        int(item["pos"]) <= int(rec["pos"]) < int(item["end"])
                        and rec.get("kind") == stage
                        for rec in records
                    )
                    if not already_covered:
                        result["section_components"].append({
                            "kind": stage,
                            "raw": item["raw"],
                            "unit": item["unit"],
                            "pos": item["pos"],
                            "value_wan": item["value_wan"],
                            "source": "shared_enum_component",
                            "not_included_reason": None,
                        })

            check = {
                "item_start": item_start,
                "kind": stage,
                "anchor_pos": enum_start,
                "component_positions": sorted(component_positions),
                "component_items": items,
                "stated_total_wan": str(stated_total) if stated_total is not None else None,
                "component_sum_wan": format(group_sum, "f"),
                "arithmetic_status": arithmetic_status,
                "reason": arithmetic_reason,
                "unbound_subjects": unbound_subjects,
                "unbound_components": unbound_components,
                "unresolved_role_components": [
                    item["name"] for item in unresolved_role_items
                ],
                "unparsed_amounts": list(dict.fromkeys(unparsed_amounts)),
                "missing_amount_subjects": missing_amount_subjects,
                "unparsed_subject_like": unparsed_subject_like,
                "unparsed_prose": stage_unparsed_prose,
                "unparsed_placeholders": unparsed_placeholders,
                "unparsed_subjects": list(
                    dict.fromkeys([*missing_amount_subjects, *stage_unparsed_fragments])
                ),
                "ambiguous_bindings": list(dict.fromkeys(ambiguous_names)),
                "name_candidates": name_candidates,
                "mixed_roles": mixed_roles,
                "total_pos": total_pos,
            }
            result["group_checks"].append(check)
            result["enum_records"].update(component_positions)
            if total_pos is not None:
                result["components"].update(component_positions)
        result["totals"].update(total_positions)
        # 逐项年初预算缺口：只有组级预算合计、子句既没有自己的预算声明、
        # 也没有绑定到预算分项时才记“未逐项列示”；不把组级预算复制到科目。
        if any(record_by_pos[pos].get("kind") == "budget" for pos in total_positions):
            for binding in bindings_by_stage.get("budget") or []:
                if not binding["name"]:
                    continue
                clause_start = binding["clause_start"]
                clause = next(
                    (item for item in subject_clauses
                     if int(item["start"]) == clause_start),
                    None,
                )
                if clause is None:
                    continue
                clause_end = int(clause["end"])
                has_budget = bool(binding["matched"]) or any(
                    clause_start <= int(record["pos"]) < clause_end
                    and not (enum_start <= int(record["pos"]) < enum_end)
                    and record.get("kind") == "budget"
                    for record in records
                )
                if not has_budget:
                    result["budget_not_itemized"].add(clause_start)
    # 有组级合计时，组内分项只作为合计的构成证据，不重复加总。
    if result["totals"]:
        result["components"] -= result["totals"]
    return result


def _clause_declaration_accounting(
    joined: str,
    clauses: Sequence[Dict[str, Any]],
    records: List[Dict[str, Any]],
    tail_start: int,
    space_before: Optional[set[int]] = None,
) -> Dict[str, Any]:
    """逐科目核对年初预算/支出决算槽位。

    返回 gaps（缺失/例外矛盾）、withheld（依法不予公开、金额未知）、
    conflicts（候选冲突位置）与 exceptions（原文例外证据）。
    例外声明只在它明确覆盖的科目/阶段生效；不让预算例外顺带豁免决算。
    """
    gaps: Dict[str, List[Dict[str, Any]]] = {"budget": [], "final": []}
    withheld: Dict[str, List[Dict[str, Any]]] = {"budget": [], "final": []}
    conflicts = set()
    exceptions: List[Dict[str, Any]] = []
    shared = _shared_group_disclosures(
        joined, clauses, records, space_before=space_before
    )
    record_by_pos = {int(record["pos"]): record for record in records}
    for position in shared["components"]:
        record = record_by_pos.get(int(position))
        if record is not None:
            # 组级“其中”分项只作为组级合计的构成证据，不重复参与分项加总。
            record["group_component"] = True
    for position in shared["untrusted_totals"]:
        record = record_by_pos.get(int(position))
        if record is not None:
            # 组内分项与组级合计不一致：该合计不再是可信的章节加总数。
            record["not_included_reason"] = "group_total_conflict"
    for clause in clauses:
        start, end = int(clause["start"]), int(clause["end"])
        text_start = max(start, tail_start)
        text = joined[text_start:end]
        in_clause = [
            record for record in records
            if start <= record["pos"] < end and not record.get("group_component")
        ]
        clause_exceptions = _clause_exceptions(text, text_start, clause["label"])
        exceptions.extend(clause_exceptions)
        for kind in ("budget", "final"):
            kind_records = [record for record in in_clause if record["kind"] == kind]
            for sentence in re.finditer(r"[^。；;]+", text):
                low, high = text_start + sentence.start(), text_start + sentence.end()
                usable = [
                    record for record in kind_records
                    if record["value_wan"] is not None
                    and low <= record["pos"] < high
                    and int(record["pos"]) not in shared["enum_records"]
                ]
                if len({(record["raw"], record["unit"]) for record in usable}) > 1:
                    for record in usable:
                        record["not_included_reason"] = "candidate_amount_conflict"
                        conflicts.add(record["pos"])
            kind_exceptions = [item for item in clause_exceptions if kind in item["stages"]]
            ambiguous_exceptions = [
                item for item in clause_exceptions
                if item["scope"] == "ambiguous"
                and (not item["stages"] or kind in item["stages"])
            ]
            stage_exceptions = [item for item in kind_exceptions if item["scope"] != "subitems"]
            subitem_exceptions = [item for item in kind_exceptions if item["scope"] == "subitems"]
            has_zero = any(item["reason"] == "zero" for item in stage_exceptions)
            has_withheld = any(item["reason"] == "withheld" for item in stage_exceptions)

            def mark_conflict(reason: str = "exception_amount_conflict",
                              items: Sequence[Dict[str, Any]] = stage_exceptions,
                              kind_records: Sequence[Dict[str, Any]] = kind_records,
                              kind: str = kind,
                              start: Any = start,
                              clause: Dict[str, Any] = clause) -> None:
                if kind_records:
                    for record in kind_records:
                        record["not_included_reason"] = reason
                else:
                    gaps[kind].append({
                        "kind": kind, "raw": "", "unit": None, "value_wan": None,
                        "pos": items[0]["pos"] if items else start,
                        "not_included_reason": reason, "item_head": clause["label"],
                    })

            if ambiguous_exceptions:
                # 金额存在也不能消除例外歧义，否则可用恰好相等的总额误放行。
                mark_conflict("exception_scope_ambiguous", ambiguous_exceptions)
                continue
            if stage_exceptions and has_zero and has_withheld:
                for item in stage_exceptions:
                    item["conflict"] = True
                mark_conflict()
                continue
            if stage_exceptions and has_zero:
                if kind_records:
                    # 同条同阶段又明示“年初无预算”时，字面0不依赖金额单位。
                    # 保留原raw/unit；只把已确认的零声明用于加法，不推断任何非零金额。
                    for record in kind_records:
                        if (record["value_wan"] is None and record.get("unit") is None
                                and _dec(record.get("raw")) == Decimal(0)
                                and record.get("not_included_reason") in (None, "amount_or_unit_unusable")):
                            record.update(value_wan=Decimal(0), source="explicit_zero",
                                          not_included_reason=None,
                                          exception_text=next(item["text"] for item in stage_exceptions
                                                              if item["reason"] == "zero"))
                    values = [record["value_wan"] for record in kind_records]
                    record_positions = {record["pos"] for record in kind_records}
                    if (all(value == Decimal(0) for value in values)
                            and not (conflicts & record_positions)):
                        for item in stage_exceptions:
                            item["consistent_zero"] = True
                        continue
                    for item in stage_exceptions:
                        item["conflict"] = True
                    mark_conflict()
                    continue
                item = next(item for item in stage_exceptions if item["reason"] == "zero")
                records.append({
                    "kind": kind, "raw": "0", "unit": "万元", "pos": item["pos"],
                    "value_wan": Decimal(0), "source": "explicit_zero",
                    "not_included_reason": None, "exception_scope": item["scope"],
                    "exception_text": item["text"],
                })
                item["applied"] = True
                continue
            if stage_exceptions and has_withheld:
                if kind_records:
                    for item in stage_exceptions:
                        item["conflict"] = True
                    mark_conflict()
                    continue
                item = next(item for item in stage_exceptions if item["reason"] == "withheld")
                withheld[kind].append({
                    "kind": kind, "item_head": clause["label"], "pos": item["pos"],
                    "scope": item["scope"], "text": item["text"],
                })
                item["applied"] = True
                continue
            if subitem_exceptions:
                if kind_records:
                    # “此款下各项涉密”约束的是子项明细，不否定该款已披露的合计金额。
                    for item in subitem_exceptions:
                        item["applies_to"] = "subitems_disclosed"
                    continue
                item = next((item for item in subitem_exceptions
                             if item["reason"] == "withheld"), subitem_exceptions[0])
                if item["reason"] == "withheld":
                    withheld[kind].append({
                        "kind": kind, "item_head": clause["label"], "pos": item["pos"],
                        "scope": item["scope"], "text": item["text"],
                    })
                else:
                    records.append({
                        "kind": kind, "raw": "0", "unit": "万元", "pos": item["pos"],
                        "value_wan": Decimal(0), "source": "explicit_zero",
                        "not_included_reason": None, "exception_scope": item["scope"],
                        "exception_text": item["text"],
                    })
                item["applied"] = True
                continue
            if not kind_records and kind == "final" and clause.get("group_component_unbound"):
                # 共享枚举里没有能对应到本科目的分项：不能按“数量够了”
                # 认定已披露，保留具体缺口。
                gaps[kind].append({
                    "kind": kind, "raw": "", "unit": None, "value_wan": None,
                    "pos": start, "not_included_reason": "group_component_unbound",
                    "item_head": clause["label"],
                })
                continue
            if not kind_records and kind == "budget" and clause.get("group_component_unbound_budget"):
                gaps[kind].append({
                    "kind": kind, "raw": "", "unit": None, "value_wan": None,
                    "pos": start, "not_included_reason": "item_disclosure_missing",
                    "item_head": clause["label"],
                })
                continue
            if not kind_records and kind == "final" and start in shared["final_disclosed"]:
                # 同一编号条目内多个项级科目共享“其中：”分项决算枚举，
                # 原文本已逐项列出金额；不能再报“未披露支出决算”。
                continue
            if not kind_records and kind == "budget" and start in shared["budget_disclosed"]:
                # 共享枚举中已逐项列示年初预算；不能再报“未披露年初预算”。
                continue
            if not kind_records and kind == "budget" and start in shared["budget_not_itemized"]:
                # 只列了组级年初预算、未逐项列示：这是披露完整性问题，
                # 不是“缺少全部预算金额”，也不把组级预算复制到每个科目。
                gaps[kind].append({
                    "kind": kind, "raw": "", "unit": None, "value_wan": None,
                    "pos": start, "not_included_reason": "item_budget_not_itemized",
                    "item_head": clause["label"],
                })
                continue
            if any(record["value_wan"] is not None and record["pos"] not in conflicts
                   for record in kind_records):
                continue
            if any(record["pos"] not in conflicts for record in kind_records):
                continue
            if kind == "final":
                bare = _HEADER_AMOUNT_RE.search(text)
                if bare:
                    raw, unit = bare.group("amount"), bare.group("unit")
                    value = _dec(raw)
                    factor = _AMOUNT_UNIT_FACTOR.get(unit)
                    records.append({
                        "kind": "final", "raw": raw, "unit": unit,
                        "pos": text_start + bare.start(),
                        "value_wan": (
                            value * factor if value is not None and factor is not None else None
                        ),
                        "source": "subject_header", "not_included_reason": None,
                    })
                    continue
            gaps[kind].append({
                "kind": kind, "raw": "", "unit": None, "value_wan": None,
                "pos": start, "not_included_reason": "item_disclosure_missing",
                "item_head": clause["label"],
            })
    if shared["section_components"]:
        records.extend(shared["section_components"])
    return {
        "gaps": gaps,
        "withheld": withheld,
        "conflicts": conflicts,
        "exceptions": exceptions,
        "group_components": shared["components"],
        "group_checks": shared["group_checks"],
    }


def _inherit_clause_unit(
    clauses: Sequence[Dict[str, Any]],
    records: List[Dict[str, Any]],
    joined: Optional[str] = None,
    tail_start: int = 0,
) -> None:
    """同条目同槽位缺单位金额的唯一单位继承（D0 RC-3b）。

    继承条件（全部满足才生效，宁缺毋造）：
    - 记录数值本身可解析（value_wan 为 None 仅因缺单位）；
    - 所属条目（clause）内同一槽位（kind）存在唯一单位的干净捐赠声明；
    - 只继承单位，不发明数值；跨条目/跨阶段/单位冲突时不继承（fail-closed）。
    """
    def _clause_start_of(pos: int):
        for clause in clauses:
            if int(clause["start"]) <= pos < int(clause["end"]):
                return int(clause["start"])
        return None

    grouped: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for record in records:
        clause_start = _clause_start_of(int(record["pos"]))
        if clause_start is not None:
            grouped.setdefault((clause_start, str(record["kind"])), []).append(record)

    for record in records:
        if record.get("value_wan") is not None or record.get("unit") is not None:
            continue
        if record.get("not_included_reason") not in (None, "amount_or_unit_unusable"):
            continue
        value = _dec(str(record.get("raw") or ""))
        if value is None:
            continue
        clause_start = _clause_start_of(int(record["pos"]))
        if clause_start is None:
            continue
        donors = [
            other for other in grouped.get((clause_start, str(record["kind"])), [])
            if other is not record
            and other.get("value_wan") is not None
            and other.get("unit")
            and not other.get("not_included_reason")
        ]
        # 条目头金额（"（项）4.19 万元"）是同条目决算槽位的显式单位依据：
        # 声明式记录之外，科目头自带金额带单位时同样可捐单位。
        if joined and record["kind"] == "final":
            for clause in clauses:
                if int(clause["start"]) != clause_start:
                    continue
                body = joined[int(clause["start"]):int(clause["end"])]
                head = _HEADER_AMOUNT_RE.search(body)
                if head and head.group("unit"):
                    donors.append({
                        "unit": head.group("unit"),
                        "pos": tail_start + int(clause["start"]) + head.start("amount"),
                        "source": "subject_header",
                    })
                break
        units = {str(other["unit"]) for other in donors}
        if len(units) != 1:
            continue
        factor = _AMOUNT_UNIT_FACTOR.get(next(iter(units)))
        if factor is None:
            continue
        record["unit"] = next(iter(units))
        record["value_wan"] = value * factor
        record["not_included_reason"] = None
        record["unit_inherited_from"] = int(donors[0]["pos"])


def _completeness_summary(items: Sequence[Dict[str, Any]], label: str) -> str:
    missing = sum(1 for item in items
                  if item.get("not_included_reason") == "item_disclosure_missing")
    not_itemized = sum(1 for item in items
                       if item.get("not_included_reason") == "item_budget_not_itemized")
    conflict = sum(1 for item in items
                   if item.get("not_included_reason") in {
                       "candidate_amount_conflict", "exception_amount_conflict"})
    ambiguous = sum(1 for item in items
                    if item.get("not_included_reason") == "exception_scope_ambiguous")
    dup_unit = sum(1 for item in items
                   if item.get("not_included_reason") == "duplicated_unit")
    unbound = sum(1 for item in items
                  if item.get("not_included_reason") == "group_component_unbound")
    group_conflict = sum(1 for item in items
                         if item.get("not_included_reason") == "group_total_conflict")
    subject_unresolved = sum(1 for item in items
                             if item.get("not_included_reason") == "subject_identity_unresolved")
    unusable = (
        len(items) - missing - not_itemized - conflict - ambiguous - dup_unit
        - unbound - group_conflict - subject_unresolved
    )
    parts = []
    if missing:
        parts.append(f"{missing}处未披露{label}金额")
    if not_itemized:
        parts.append(f"{not_itemized}处年初预算仅列组级合计、未逐项列示")
    if conflict:
        parts.append(f"{conflict}处{label}金额存在候选冲突或与例外声明矛盾")
    if ambiguous:
        parts.append(f"{ambiguous}处{label}例外声明作用域无法证明")
    if dup_unit:
        parts.append(f"{dup_unit}处{label}金额单位重复（如“万万元”）")
    if unbound:
        parts.append(f"{unbound}处共享分项无法对应到项级科目")
    if group_conflict:
        parts.append(f"{group_conflict}处组级合计与组内分项不一致")
    if subject_unresolved:
        parts.append(f"{subject_unresolved}处复核人指定科目无法在表内唯一确认（编码与名称矛盾或落表失败）")
    if unusable:
        parts.append(f"{unusable}处{label}金额缺少可归一单位或格式")
    return "、".join(parts) or "金额无法纳入合计"


def _completeness_codes(items: Sequence[Dict[str, Any]]) -> List[str]:
    codes: List[str] = []
    reasons = {item.get("not_included_reason") for item in items}
    if "item_disclosure_missing" in reasons:
        codes.append("item_disclosure_missing")
    if "item_budget_not_itemized" in reasons:
        # 逐项预算未列示仍属于披露完整性缺口；冻结原因码沿用同一族，
        # 但文案与 not_included_reason 保留“未逐项列示”的准确含义。
        codes.append("item_disclosure_missing")
    if "candidate_amount_conflict" in reasons:
        codes.append("candidate_amount_conflict")
    if "exception_amount_conflict" in reasons:
        codes.append("exception_amount_conflict")
    if "exception_scope_ambiguous" in reasons:
        codes.append("exception_scope_ambiguous")
    if "group_component_unbound" in reasons:
        codes.append("item_disclosure_missing")
    if "group_total_conflict" in reasons:
        codes.append("item_sum_mismatch")
    if reasons - {
        "item_disclosure_missing", "item_budget_not_itemized",
        "candidate_amount_conflict", "exception_amount_conflict",
        "exception_scope_ambiguous", "group_component_unbound",
        "group_total_conflict", None,
    }:
        codes.append("item_amount_not_included")
    return codes


def _operand_admitted(record: Dict[str, Any], rejected_positions: set) -> bool:
    """拒绝原因或冲突槽位均不得参与确定性金额计算。"""
    return (record.get("value_wan") is not None
            and not record.get("not_included_reason")
            and record["pos"] not in rejected_positions)


def _unit_basis_evidence(fact: "FiscalFact") -> Dict[str, Any]:
    """单位恢复凭证的可序列化摘要：只留追溯「同科目原表证实单位」所需字段。"""
    return {
        "fact_uid": fact.fact_uid,
        "table_code": fact.table_code,
        "subject_code": fact.subject_code,
        "subject_name": fact.subject_name,
        "unit": fact.unit,
        "normalized_value": (
            format(fact.normalized_value, "f")
            if fact.normalized_value is not None else None
        ),
    }


def _operand_evidence(record: Dict[str, Any]) -> Dict[str, Any]:
    """证据保留原始数值精度，同时保证分析快照可序列化。"""
    evidence = dict(record)
    if isinstance(evidence.get("value_wan"), Decimal):
        # 问题证据里的金额按报告展示口径（至少两位小数）；回执逐项值用
        # _wan_trim 保留原文精度。
        evidence["value_wan"] = _wan_display(evidence["value_wan"])
    basis = evidence.get("table_unit_basis")
    # FiscalFact 是运行期对象：回执路径（unit_facts）要按 fact 记账，但进入
    # 分析快照会让 dumps_canonical 拒绝整个结果入库（2026-11-19 上海商业
    # 会计学校重跑即因此在质量门禁后的落库阶段报 pending_retry 失败）。
    if isinstance(basis, FiscalFact):
        # R1：失败分支同样要能追溯原表。只留事实 ID 不够，页码、源格、
        # 引文与 bbox 一并保留（FiscalFact 本身仍只存在于运行期）。
        evidence["table_unit_basis"] = _unit_basis_full_evidence(basis)
    subject_row = evidence.get("subject_row_basis")
    if isinstance(subject_row, FiscalFact):
        # R4（2026-11-21 第三轮复核）：加总回执引用复核人指定科目的同一
        # 原表行；运行期对象必须先序列化成凭证再进快照。
        evidence["subject_row_basis"] = _unit_basis_full_evidence(subject_row)
    unresolved = evidence.pop("subject_identity_unresolved", None)
    if unresolved:
        # 指定科目未决的明细（编码/名称/表内名称/原因）保持可读形态。
        evidence["subject_identity_unresolved"] = unresolved
        evidence["not_included_reason"] = evidence.get("not_included_reason")
    return evidence


def _unit_basis_full_evidence(fact: "FiscalFact") -> Dict[str, Any]:
    """单位恢复凭证：科目身份 + 原表格金额、页码、格与引文。

    计划要求「依据所在页及原句或原表格」两端都可追溯：正文端由调用方补
    page/span，原表端由本函数携带 physical_page/source_cell_id/quote/bbox。
    """
    evidence = _unit_basis_evidence(fact)
    refs = [
        ref for ref in ((fact.extra_dims or {}).get("evidence_refs") or [])
        if isinstance(ref, dict)
    ]
    page = next((ref.get("physical_page") for ref in refs if ref.get("physical_page")), None)
    cell = next((ref.get("source_cell_id") for ref in refs if ref.get("source_cell_id")), None)
    quote = next((ref.get("quote") for ref in refs if str(ref.get("quote") or "").strip()), None)
    bbox = next((ref.get("bbox") for ref in refs if ref.get("bbox")), None)
    evidence.update({
        "page": page,
        "source_cell_id": cell,
        "quote": quote,
        "bbox": bbox,
        "file_hash": next(
            (ref.get("file_hash") for ref in refs if ref.get("file_hash")), None
        ),
        "role": "same_subject_table_basis",
    })
    return evidence


def _calc_item_evidence(record: Dict[str, Any], locate) -> Dict[str, Any]:
    """单条分项的原文数字、原文单位、核算依据与依据页。"""
    pos = record.get("pos")
    page = span = None
    if isinstance(pos, int):
        try:
            page, text, start, end = locate(pos)
            span = re.sub(r"\s+", " ", text[start:end]).strip()
        except Exception:
            page = span = None
    evidence: Dict[str, Any] = {
        "page": page,
        "span": span,
        "raw": str(record.get("raw") or ""),
        "unit": record.get("unit"),
        # R2：原文单位与核算单位分别留痕，复核人能看到单位来源。
        "unit_original": record.get("unit_original"),
        "unit_accounting": record.get("unit_accounting", record.get("unit")),
        "unit_source": record.get("unit_source"),
        "kind": record.get("kind"),
    }
    value = record.get("value_wan")
    if value is not None:
        evidence["value_wan"] = _wan_trim(value) if isinstance(value, Decimal) else str(value)
    basis = record.get("table_unit_basis")
    if basis is not None:
        evidence["basis"] = (
            _unit_basis_full_evidence(basis) if isinstance(basis, FiscalFact) else basis
        )
    subject_row = record.get("subject_row_basis")
    if subject_row is not None:
        # R4（2026-11-21 第三轮复核）：复核人指定科目时，加总回执引用单项
        # 所用的同一原表行（页码/编码/名称/金额/原格）。
        evidence["subject_row_basis"] = (
            _unit_basis_full_evidence(subject_row)
            if isinstance(subject_row, FiscalFact) else subject_row
        )
    if record.get("subject_identity_unresolved"):
        evidence["subject_identity_unresolved"] = record["subject_identity_unresolved"]
        evidence["not_included_reason"] = record.get("not_included_reason")
    if record.get("unit_inherited_from") is not None:
        evidence["unit_inherited_from"] = record["unit_inherited_from"]
    if record.get("basis_mode"):
        evidence["basis_mode"] = record["basis_mode"]
    if record.get("source"):
        evidence["source"] = record["source"]
    if record.get("manual_correction"):
        evidence["manual_correction"] = record["manual_correction"]
    if record.get("manual_corrections"):
        # 同一目标可能先确认单位再校正读数，逐条保留依据与人员/版本。
        evidence["manual_corrections"] = list(record["manual_corrections"])
    if record.get("malformed_amount"):
        evidence["malformed_amount"] = True
        evidence["not_included_reason"] = record.get("not_included_reason")
    return evidence


def _calculation_evidence(
    included: Sequence[Dict[str, Any]],
    not_included: Sequence[Dict[str, Any]],
    withheld: Sequence[Dict[str, Any]],
    locate,
) -> Dict[str, Any]:
    """一条算式的逐项证据底座；金额结论由调用方补齐。"""
    return {
        "included_count": len(included),
        "included": [_calc_item_evidence(record, locate) for record in included],
        "not_included": [_calc_item_evidence(record, locate) for record in not_included],
        "withheld_count": len(withheld),
        "unknown_count": len(not_included) + len(withheld),
    }


def _compact_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _compact_raw_token_in(needle: str, raw: str) -> bool:
    """金额原文是否作为**完整数字 token**出现在引文中。

    R5（2026-11-20 复审）：预算行 raw="0" 这类短值不能再靠子串命中
    "支出决算为12,906.51"——数字两侧必须不是数字/小数点/千分位。
    """
    if not raw:
        return False
    return re.search(rf"(?<![\d.,，]){re.escape(raw)}(?![\d.,，])", needle) is not None


def _apply_text_corrections(
    records: Sequence[Dict[str, Any]],
    locate,
    doc: Document,
) -> int:
    """把正文金额人工校正应用到当前条目；返回命中条数。

    只按物理页 + 原文区间/金额匹配，不按金额大小猜条目。一条校正必须
    唯一命中一个条目才应用；同页同金额多处命中（同名多行/重复列示）保持
    待核，不能把一处校正扩散到所有同额条目。

    R5（2026-11-20 复审）：
    - 校正带原句页内位置（pos）时优先按位置唯一落条（同页两笔同额原句
      也能各改各的）；
    - 无位置时按原文片段包含匹配；金额词只作"片段都够不着"时的兜底，
      且必须是完整数字 token；
    - 片段命中多于一条时再用保留空白的原文引文二次区分；仍不唯一保持待核。
    """
    corrections = getattr(doc, "text_corrections", None) or []
    if not corrections:
        return 0
    located: List[Dict[str, Any]] = []
    for record in records:
        pos = record.get("pos")
        if not isinstance(pos, int):
            continue
        try:
            page, text, start, end = locate(pos)
        except Exception:
            continue
        located.append({
            "record": record,
            "page": int(page),
            "span": _compact_match_text(text[start:end]),
            "span_raw": str(text[start:end]),
            "page_start": int(start),
            "page_end": int(end),
            "raw": _compact_match_text(record.get("raw")),
        })
    applied_records: Dict[int, Dict[str, Any]] = {}
    applied_ids: List[str] = []
    unresolved: List[Dict[str, Any]] = []
    for correction in corrections:
        try:
            correction_page = int(correction.get("page") or 0)
        except (TypeError, ValueError):
            continue
        needle = _compact_match_text(correction.get("span"))
        quote_raw = str(correction.get("span") or "")
        try:
            correction_pos = int(correction.get("pos"))
        except (TypeError, ValueError):
            correction_pos = None
        if not needle and correction_pos is None:
            continue
        same_page = [item for item in located if item["page"] == correction_page]
        matches: List[Dict[str, Any]] = []
        if correction_pos is not None:
            # 位置优先：原句起点落在条目声明窗口附近（±64 字，覆盖 2A 分支
            # 的科目名前缀）才算同一处。
            matches = [
                item for item in same_page
                if abs(item["page_start"] - correction_pos) <= 64
                or item["page_start"] <= correction_pos <= item["page_end"]
            ]
        if not matches and needle:
            matches = [
                item for item in same_page
                if needle in item["span"] or item["span"] in needle
            ]
        if not matches and needle:
            matches = [
                item for item in same_page
                if _compact_raw_token_in(needle, item["raw"])
            ]
        if len(matches) > 1 and quote_raw:
            raw_refined = [
                item for item in matches
                if item["span_raw"]
                and (item["span_raw"] in quote_raw or quote_raw in item["span_raw"])
            ]
            if len(raw_refined) == 1:
                matches = raw_refined
        if len(matches) != 1:
            unresolved.append({
                "correction_id": correction.get("correction_id"),
                "reason": "ambiguous_match" if matches else "not_found",
                "match_count": len(matches),
            })
            continue
        record = matches[0]["record"]
        field = str(correction.get("field") or "")
        value = correction.get("value")
        if field == "unit":
            unit = str(value or "")
            if unit not in _AMOUNT_UNIT_FACTOR:
                unresolved.append({
                    "correction_id": correction.get("correction_id"),
                    "reason": "invalid_unit",
                })
                continue
            record["unit"] = unit
        elif field == "raw_value":
            raw_value = str(value or "").replace(",", "").strip()
            if _dec(raw_value) is None:
                unresolved.append({
                    "correction_id": correction.get("correction_id"),
                    "reason": "invalid_raw_value",
                })
                continue
            record["raw"] = str(value).strip()
        elif field == "subject_code":
            record["manual_subject_code"] = str(value or "").strip()
        elif field == "subject_name":
            record["manual_subject_name"] = str(value or "").strip()
        else:
            unresolved.append({
                "correction_id": correction.get("correction_id"),
                "reason": "unsupported_field",
            })
            continue
        record.setdefault("manual_corrections", []).append({
            "correction_id": correction.get("correction_id"),
            "field": field,
            "value": value,
            "actor": correction.get("actor"),
            "analysis_basis_token": correction.get("analysis_basis_token"),
            "original_text": correction.get("span"),
        })
        record["manual_correction"] = (record.get("manual_corrections") or [None])[-1]
        applied_records[id(record)] = record
        if correction.get("correction_id"):
            applied_ids.append(str(correction["correction_id"]))
    for record in applied_records.values():
        value = _dec(str(record.get("raw") or ""))
        factor = _AMOUNT_UNIT_FACTOR.get(record.get("unit"))
        record["value_wan"] = (
            value * factor if value is not None and factor is not None else None
        )
        record["not_included_reason"] = (
            None if record["value_wan"] is not None else "amount_or_unit_unusable"
        )
    try:
        doc.applied_text_correction_ids = list(dict.fromkeys(applied_ids))
        doc.unapplied_text_corrections = unresolved
    except Exception:
        pass
    return len(applied_records)


def _conditional_trial(
    included: Sequence[Dict[str, Any]],
    not_included: Sequence[Dict[str, Any]],
    subtotal: Decimal,
    stated: Decimal,
    locate,
) -> Optional[Dict[str, Any]]:
    """只能提出「按万元理解」时的条件试算；明确标记不计通过率、不解除门禁。"""
    candidates = [
        record for record in not_included
        if str(record.get("raw") or "").strip()
        and record.get("not_included_reason") in {
            "amount_or_unit_unusable", "unit_basis_unconfirmed",
        }
    ]
    if not candidates:
        return None
    values = [_dec(str(record.get("raw") or "")) for record in candidates]
    if any(value is None for value in values):
        return None
    assumed = subtotal + sum(values, Decimal(0))
    delta = assumed - stated
    return {
        "assumption": "未确定单位的金额按万元理解",
        "included_count": len(included) + len(candidates),
        "subtotal_wan": _wan_display(assumed),
        "difference_wan": _wan_display(delta),
        "evidence": [_calc_item_evidence(record, locate) for record in candidates],
        "is_conditional": True,
        "counts_as_pass": False,
        "clears_completion_gate": False,
    }


def _table_general_public_expenditure_total(doc: Document) -> Tuple[Optional[Decimal], Optional[str]]:
    """具体情况段缺少总体句时，回退取表5（一般公共预算财政拨款支出决算表）本年支出合计。"""
    facts, reason = table_facts(doc, "FIN_05_general_public_expenditure")
    if reason or not facts:
        return None, None
    total_fact = _total_fact(facts, "total_actual", ("合计", "总计")) or _total_fact(facts, "total_actual")
    if total_fact is not None and total_fact.normalized_value is not None:
        val = Decimal(str(total_fact.normalized_value))
        raw = str(total_fact.raw_value or "").replace(",", "").replace("，", "").strip() or format(val, "f")
        return val, raw
    top_total = _top_level_rows_total(facts)
    if top_total is not None:
        return top_total, format(top_total, "f")
    return None, None


class R33251_FunctionalDetailCompleteness(Rule):
    code, severity = "V33-251", "warn"
    desc = "支出决算具体情况逐项细化到项、分项加总等于总额、不漏列科目（清单 A-09/C-11/C-12）"

    def apply(self, doc: Document) -> List[Issue]:
        blocks = _detail_section(doc)
        if not blocks:
            raise RuleNotApplicable(self.code, detail="未找到“一般公共预算财政拨款支出决算具体情况”段落")
        joined = ""
        spans: List[Tuple[int, int, int]] = []  # (joined_start, block_index, block_offset)
        space_before: set[int] = set()
        for index, (_, text, flat, positions, begin, end) in enumerate(blocks):
            joined_base = len(joined)
            spans.append((joined_base, index, begin))
            joined += flat[begin:end]
            for offset in range(begin + 1, min(end, len(positions))):
                gap_slice = text[positions[offset - 1] + 1 : positions[offset]]
                if any(ch in " \t\u3000" for ch in gap_slice):
                    space_before.add(joined_base + offset - begin)

        def _locate(pos: int) -> Tuple[int, str, int, int]:
            for start, index, begin in reversed(spans):
                if pos >= start:
                    page, text, flat, positions, *_ = blocks[index]
                    s, e = flat_span(positions, begin + pos - start, begin + pos - start + 16)
                    return page, text, s, e
            page, text, flat, positions, begin, _ = blocks[0]
            s, e = flat_span(positions, begin, begin + 16)
            return page, text, s, e

        def _locate_header() -> Tuple[int, str, int, int]:
            page, text, flat, positions, *_ = blocks[0]
            header_match = _DETAIL_START_RE.search(flat)
            if header_match is not None:
                s, e = flat_span(positions, header_match.start(), header_match.end())
                return page, text, s, e
            return _locate(0)

        def _format_not_included_locations(rows: Sequence[Dict[str, Any]]) -> str:
            refs: List[str] = []
            for row in rows[:3]:
                pos = row.get("pos")
                if pos is None:
                    continue
                page, text, s, e = _locate(int(pos))
                excerpt = re.sub(r"\s+", " ", text[s:e]).strip()
                raw_val = str(row.get("raw") or "").strip()
                if excerpt and raw_val and raw_val not in excerpt:
                    refs.append(f"第{page}页「{excerpt}」（{raw_val}）")
                elif excerpt:
                    refs.append(f"第{page}页「{excerpt}」")
                elif raw_val:
                    refs.append(f"第{page}页（{raw_val}）")
                else:
                    refs.append(f"第{page}页")
            if not refs:
                return ""
            suffix = "等" if len(rows) > 3 else ""
            return f"（定位：{'、'.join(refs)}{suffix}）"

        issues: List[Issue] = []
        unresolved: List[str] = []
        unresolved_codes: List[str] = []
        starts = [m.start() for m in _ITEM_START_RE.finditer(joined)]
        # 总额必须在逐项条目之前；否则命中的只是某项自己的“年初预算…支出决算…”，
        # 不能拿它当该部分总额（否则会把第一项误当总额并重复计缺失）。
        total = _DETAIL_TOTAL_RE.search(joined)
        if total is not None and starts and total.start() >= starts[0]:
            total = None
        if total is None:
            # 标准三段式命中到了某个分项时，再试“年初预算→调整后预算→决算执行数”
            # 的总额变体；同样要求它在首项之前。
            total = _DETAIL_TOTAL_ADJUSTED_RE.search(joined)
            if total is not None and starts and total.start() >= starts[0]:
                total = None
        items: List[Dict[str, Any]] = []
        for index, start in enumerate(starts):
            stop = starts[index + 1] if index + 1 < len(starts) else len(joined)
            head = re.split(r"主要用于|。", joined[start:stop], maxsplit=1)[0]
            items.append({"pos": start, "stop": stop, "head": head})
        if not items:
            raise RuleNotApplicable(self.code, detail="具体情况段落未识别到逐项列示条目")
        for item in items:
            if re.search(r"[（(]\s*类\s*[)）]", item["head"]) and not re.search(r"[（(]\s*项\s*[)）]", item["head"]):
                page, text, s, e = _locate(item["pos"])
                issues.append(issue_at(
                    self, f"支出决算具体情况未细化到项：“{item['head'][:30]}”只列至类/款级科目。",
                    page, text, s, e, severity="warn", checklist_item="A-09",
                ))
        from .native_table_arithmetic import receipt as relation_receipt

        if total is None:
            header_page, header_text, header_s, header_e = _locate_header()
            missing_msg = (
                "支出决算具体情况未按标准模板撰写总体情况说明"
                "（“（三）一般公共预算财政拨款支出决算具体情况”下缺少年初预算、"
                "支出决算总额、预算完成率及变动原因说明）。"
            )
            issues.append(issue_at(
                self,
                missing_msg,
                header_page,
                header_text,
                header_s,
                header_e,
                severity="warn",
                checklist_item="A-09",
                section="一般公共预算财政拨款支出决算具体情况",
                check_relation_id=f"{self.code}:V33-251:total:missing",
            ))
            relation_receipt(
                self, doc, "V33-251:total:missing", [],
                status="fail",
                formula="section intro states initial budget and final expenditure totals",
                reason=missing_msg,
                reason_codes=["stated_total_missing"],
            )

        # 一个编号下可能挂多条“类（类）款（款）项（项）”子条目（每条各有年初预算与支出决算），
        # 所以按总额之后（无总括句时从首项起）出现的全部声明加总，不按编号取第一处。
        # 单位按原文（元/万元/亿元）归一；不能把“元”项按“万元”名义值补入。
        tail_start = total.end() if total is not None else starts[0]
        records: List[Dict[str, Any]] = []
        for match in _DECL_RE.finditer(joined, tail_start):
            raw, unit, bad_unit = match.group("amount"), match.group("unit"), match.group("bad_unit")
            value = _dec(raw)
            factor = _AMOUNT_UNIT_FACTOR.get(unit)
            # R3：数字词本身非法（错位逗号、双小数点等）时保留原文，不取数、
            # 不修补，也不按总额反推；该条目不进入合计，缺陷单独报出。
            malformed = value is None and not bad_unit
            if malformed:
                bad_page, bad_text, bad_s, bad_e = _locate(match.start())
                issues.append(issue_at(
                    self,
                    f"支出决算具体情况中金额格式异常：“{raw}”，无法作为金额取数，"
                    "请核对原文数字（不按总额反推）。",
                    bad_page, bad_text, bad_s, bad_e,
                    severity="warn", checklist_item="C-14",
                    section="一般公共预算财政拨款支出决算具体情况",
                    check_relation_id=f"{self.code}:V33-251:malformed:{match.start()}",
                ))
            if bad_unit:
                bad_page, bad_text, bad_s, bad_e = _locate(match.start())
                issues.append(issue_at(
                    self,
                    f"支出决算具体情况中金额单位重复：“{raw}{bad_unit}”，请核对并修正原文单位。",
                    bad_page, bad_text, bad_s, bad_e,
                    severity="warn", checklist_item="C-14",
                    section="一般公共预算财政拨款支出决算具体情况",
                ))
            records.append({
                "kind": _decl_kind(match.group("kind")),
                "raw": raw, "unit": unit, "pos": match.start(),
                # 原文单位与核算单位分开保存；unit 在单位恢复/人工确认后
                # 表示核算单位，unit_original 始终保留正文原字面单位。
                "unit_original": unit,
                **({"bad_unit": bad_unit} if bad_unit else {}),
                "value_wan": value * factor if value is not None and factor is not None else None,
                **({"malformed_amount": True} if malformed else {}),
                "not_included_reason": (
                    "duplicated_unit" if bad_unit
                    else "invalid_amount_literal" if malformed
                    else None if value is not None and factor is not None
                    else "amount_or_unit_unusable"
                ),
            })
        # 应披露集合来自识别到的项级条目：每条科目都要有年初预算与支出决算
        # 两个槽位。没有数字不是“没有检查义务”，缺失槽位同样进入未纳入清单。
        clauses = _subject_clauses(joined, items)
        # D0 RC-3b：同条目同槽位的缺单位金额，有唯一带单位的同槽位声明时
        # 按该单位归一后参与合计；漏单位缺陷由 CMM-011 独立交付，
        # 不再让整个构成检查被阻断（宜川部门决算 P29 实证）。
        _inherit_clause_unit(clauses, records, joined, tail_start)
        # 2026-11-19：正文金额校正先按原文位置落到条目，再走单位依据与加总。
        _apply_text_corrections(records, _locate, doc)
        from .native_final_disclosure import (
            GENERAL,
            detail_decision_unit_from_table,
            detail_unit_basis_from_table,
            unit_basis_by_subject_code,
        )

        facts, _ = table_facts(doc, GENERAL)
        decision_units = {fact.unit for fact in facts if fact.indicator == "total_actual"}
        # 只补单位，金额一律取正文原数；先确认每个条目的同槽位只有一笔声明，
        # 保留多候选冲突的原判定。
        if decision_units:
            for clause in clauses:
                begin, end = int(clause["start"]), int(clause["end"])
                actuals = [r for r in records if begin <= r["pos"] < end and r["kind"] == "final"]
                if len(actuals) != 1 or actuals[0].get("bad_unit"):
                    continue
                record = actuals[0]
                # R4（2026-11-21 第三轮复核）：复核人指定科目时，单项核对与
                # 分项加总必须引用同一原表行。编码在表内无法唯一确认，或编码
                # 与名称互相矛盾时，科目身份未决——这笔读数不再作为已确认
                # 依据计入分项加总（与单项"待核"一致），其余各笔仍有可查小计。
                manual_code = str(record.get("manual_subject_code") or "").strip()
                manual_name = str(record.get("manual_subject_name") or "").strip()
                subject_row = None
                subject_unresolved = None
                normalize = lambda value: re.sub(r"\s+|支出$", "", str(value or ""))
                if manual_code:
                    designated = unit_basis_by_subject_code(doc, facts, manual_code)
                    if designated is None:
                        subject_unresolved = {
                            "subject_code": manual_code,
                            "subject_name": manual_name or None,
                            "reason": "subject_code_not_found",
                        }
                    elif manual_name and normalize(designated.subject_name) != normalize(manual_name):
                        # 编码与名称由复核人同时给出但不一致：不猜以哪个为准。
                        subject_unresolved = {
                            "subject_code": manual_code,
                            "subject_name": manual_name,
                            "table_subject_name": designated.subject_name,
                            "reason": "subject_name_conflict",
                        }
                    else:
                        subject_row = designated
                        record["subject_row_basis"] = designated
                elif manual_name:
                    basis_row = detail_unit_basis_from_table(
                        doc, facts, joined[begin:record["pos"]]
                    )
                    if basis_row is None or normalize(basis_row.subject_name) != normalize(manual_name):
                        subject_unresolved = {
                            "subject_code": getattr(basis_row, "subject_code", None) if basis_row is not None else None,
                            "subject_name": manual_name,
                            "table_subject_name": getattr(basis_row, "subject_name", None) if basis_row is not None else None,
                            "reason": "subject_name_conflict",
                        }
                if subject_unresolved is not None:
                    record["not_included_reason"] = "subject_identity_unresolved"
                    record["subject_identity_unresolved"] = subject_unresolved
                    continue
                # R2（2026-11-20 独立复核）：正文写明的单位必须按原文换算。
                # 旧逻辑在表头单位为万元、正文写“54,654,100元”时仍以表头
                # 覆盖正文单位，把它当万元加总。这里把原文单位与核算单位分开：
                # 只有原文缺单位且有可追溯依据时才允许恢复；显式单位或人工
                # 确认单位优先，依据只展示来源，不改写已写明的单位。
                if record.get("unit") is not None:
                    manual_units = [
                        item for item in (record.get("manual_corrections") or [])
                        if item.get("field") == "unit"
                    ]
                    record["unit_accounting"] = record["unit"]
                    record["unit_source"] = (
                        "manual_correction" if manual_units else "explicit_text"
                    )
                    continue
                if manual_code:
                    basis = subject_row
                    basis_mode = "manual_subject"
                elif decision_units == {record["unit"]} and not record.get("manual_correction"):
                    continue
                else:
                    basis = detail_decision_unit_from_table(
                        doc, facts, joined[begin:record["pos"]],
                        record["raw"], record["unit"],
                    )
                    basis_mode = "amount_match"
                    if basis is None:
                        # 四类边界：单位依据按科目身份定位，金额差异是核算结果而不是
                        # 单位前提。正文数字写错时仍必须恢复单位并如实算出差额。
                        basis = detail_unit_basis_from_table(
                            doc, facts, joined[begin:record["pos"]]
                        )
                        basis_mode = "identity_only"
                if basis is not None:
                    factor = _AMOUNT_UNIT_FACTOR.get(basis.unit)
                    value = _dec(str(record.get("raw") or ""))
                    if factor is None or value is None:
                        continue
                    record["table_unit_basis"] = basis
                    record["unit"] = basis.unit
                    record["unit_accounting"] = basis.unit
                    record["unit_source"] = f"table_basis:{basis_mode}"
                    # 必须用正文数字：不得以表内金额覆盖正文原数。
                    record["value_wan"] = value * factor
                    record["basis_mode"] = basis_mode
                    record["not_included_reason"] = None
        accounting = _clause_declaration_accounting(
            joined, clauses, records, tail_start, space_before=space_before
        )
        # 共享“其中”分项：组内科目绑定与组级算术分别留回执；
        # 组级合计只有在组内分项可核对时才作为章节加总数使用。
        for check in accounting.get("group_checks") or []:
            kind = str(check.get("kind") or "")
            relation = f"V33-251:{kind}:group:{check.get('item_start')}"
            blocking = []
            formal_parts = []
            hints = []
            missing_amount_subjects = list(check.get("missing_amount_subjects") or [])
            unparsed_subject_like = list(check.get("unparsed_subject_like") or [])
            unparsed_prose = list(check.get("unparsed_prose") or [])
            unparsed_placeholders = list(check.get("unparsed_placeholders") or [])
            unparsed_amounts = list(check.get("unparsed_amounts") or [])
            unresolved_role = list(check.get("unresolved_role_components") or [])
            ambiguous = list(check.get("ambiguous_bindings") or [])
            unbound_subjects = list(check.get("unbound_subjects") or [])

            if check.get("arithmetic_status") == "fail":
                reason_text = str(check.get("reason") or "组内分项与组级合计不一致")
                blocking.append(reason_text)
                formal_parts.append(reason_text)
            elif check.get("arithmetic_status") == "insufficient_data":
                blocking.append(
                    str(check.get("reason") or "组内分项缺少同阶段组级合计，无法核对算术")
                )
            if unbound_subjects:
                blocking.append(
                    "共享分项未能对应到项级科目："
                    + "、".join(str(value) for value in unbound_subjects)
                )
                # 标题科目存在未绑定时，无法归属的分项是错配证据，一并列入。
                if check.get("unbound_components"):
                    blocking.append(
                        "共享分项中存在无法归属的条目："
                        + "、".join(str(value) for value in check["unbound_components"])
                    )
            if ambiguous:
                blocking.append(
                    "共享分项与科目对应存在歧义（重复名称或一名多配）："
                    + "、".join(str(value) for value in ambiguous)
                )
            if unresolved_role:
                blocking.append(
                    "共享分项未写明金额阶段，无法纳入组内核对："
                    + "、".join(str(value) for value in unresolved_role)
                )
            if unparsed_amounts:
                blocking.append(
                    "共享分项枚举中存在未能归入分项的金额（如缺单位）："
                    + "、".join(str(value) for value in unparsed_amounts)
                )
            if missing_amount_subjects:
                joined_missing = "、".join(str(value) for value in missing_amount_subjects)
                blocking.append(f"共享分项枚举中存在未附金额的科目名：{joined_missing}")
                formal_parts.append(f"共享分项中以下科目未列金额：{joined_missing}")
            if unparsed_subject_like:
                blocking.append(
                    "共享分项枚举中存在未附金额的科目名（未匹配到标题科目）："
                    + "、".join(str(value) for value in unparsed_subject_like)
                )
            if unparsed_placeholders:
                blocking.append(
                    "共享分项枚举中存在横线或占位符等未披露数值："
                    + "、".join(str(value) for value in unparsed_placeholders)
                )
            if unparsed_prose:
                blocking.append(
                    "共享分项枚举中存在未能归入分项的文本片段："
                    + "、".join(str(value) for value in unparsed_prose)
                )
            # 不确定的集合（说明句、未匹配片段、占位符、阶段/角色未证实等）只在回执留痕，
            # 不得转成正式材料缺陷；只有可证实的算术不符、已知科目缺金额，或完整
            # 枚举下仍无法对应的标题科目，才写入正式问题清单。
            confirmed_unbound = [
                subj for subj in unbound_subjects
                if not any(
                    _group_subject_name_matches(str(subj), item)
                    for item in missing_amount_subjects
                )
            ]
            unbound_clean = bool(
                confirmed_unbound
                and not unresolved_role
                and not ambiguous
                and not unparsed_amounts
                and not unparsed_subject_like
                and not unparsed_placeholders
                and not unparsed_prose
            )
            if unbound_clean:
                formal_parts.append(
                    "共享分项未能对应到项级科目："
                    + "、".join(str(value) for value in confirmed_unbound)
                )
                if check.get("unbound_components"):
                    formal_parts.append(
                        "共享分项中存在无法归属的条目："
                        + "、".join(str(value) for value in check["unbound_components"])
                    )
            if check.get("name_candidates"):
                hints.append(
                    "名称相似但身份未证实，不作金额绑定："
                    + "、".join(
                        f"{entry.get('component')}↔{entry.get('subject')}"
                        for entry in check["name_candidates"] if isinstance(entry, dict)
                    )
                )
            if blocking:
                message = "；".join([*blocking, *hints]) + "。"
                arithmetic_fail = check.get("arithmetic_status") == "fail"
                only_missing_total = bool(
                    not arithmetic_fail
                    and check.get("stated_total_wan") is None
                    and len(blocking) == 1
                )
                relation_receipt(
                    self, doc, relation, [],
                    status="fail" if arithmetic_fail else "insufficient_data",
                    formula="sum(shared components) == group total (万元)",
                    reason=message,
                    reason_codes=(
                        ["item_sum_mismatch"]
                        if arithmetic_fail
                        else ["stated_total_missing"]
                        if only_missing_total
                        else ["item_disclosure_missing"]
                    ),
                )
                if formal_parts:
                    formal_extras = hints if unbound_clean else []
                    formal_message = "；".join([*formal_parts, *formal_extras]) + "。"
                    page, text, start, end = _locate(int(check.get("anchor_pos") or 0))
                    issues.append(issue_at(
                        self, formal_message, page, text, start, end,
                        severity="warn",
                        checklist_item="C-12" if kind == "final" else "C-11",
                        check_relation_id=f"{self.code}:{relation.split(':', 1)[1]}",
                    ))
            else:
                pass_reason = (
                    f"组内分项合计{check.get('component_sum_wan')}万元"
                    f"与组级合计{check.get('stated_total_wan')}万元一致"
                )
                if check.get("unbound_components"):
                    pass_reason += (
                        "（另有未逐一对应科目的组内分项，已按组级合计核对留痕："
                        + "、".join(str(value) for value in check["unbound_components"])
                        + "）"
                    )
                if hints:
                    pass_reason += "（" + "；".join(hints) + "）"
                relation_receipt(
                    self, doc, relation, [],
                    status="pass",
                    formula="sum(shared components) == group total (万元)",
                    reason=pass_reason,
                )
        listed_final = sum(1 for record in records if record["kind"] == "final")
        for key, label, kind, checklist in (
            (1, "年初预算", "budget", "C-11"), (2, "支出决算", "final", "C-12"),
        ):
            if total is None and kind == "budget":
                # 缺少总括段已作为正式模板问题报出；表5不含年初预算列，此处不再重复报取数不足。
                continue
            kind_records = [
                record for record in records
                if record["kind"] == kind and not record.get("group_component")
            ]
            not_included = {
                record["pos"]: record
                for record in kind_records
                if not _operand_admitted(record, accounting["conflicts"])
            }
            for record in accounting["gaps"].get(kind, []):
                not_included[record["pos"]] = record
            rejected_positions = set(not_included) | accounting["conflicts"]
            included = [record for record in kind_records
                        if _operand_admitted(record, rejected_positions)]
            not_included_items = [not_included[pos] for pos in sorted(not_included)]
            withheld_items = accounting["withheld"].get(kind, [])
            disclosure_exceptions = [
                item for item in accounting["exceptions"]
                if kind in item["stages"]
                or (item["scope"] == "ambiguous" and not item["stages"])
            ]
            exception_evidence = _exception_evidence(disclosure_exceptions, _locate)
            unit_facts = [record["table_unit_basis"] for record in included
                          if "table_unit_basis" in record]
            for record in included:
                if "table_unit_basis" in record:
                    page, original, start, end = _locate(record["pos"])
                    exception_evidence.append({
                        "page": page,
                        "span": original[start:end],
                        "role": "原表证实单位的正文金额",
                        "unit_for_check": record["unit"],
                    })
            # 2026-11-19：每条算式独立记账——原文数字、原文单位、核算科目、
            # 依据页与原句/原表格。分支只补金额结论，不重造逐项证据。
            calc_evidence = _calculation_evidence(
                included, not_included_items, withheld_items, _locate
            )
            if not kind_records and not not_included_items and not withheld_items:
                continue
            if total is not None:
                stated_raw = total.group(key)
                stated = _dec(stated_raw)
                total_origin_label = f"该部分所列{label}总额"
                if stated is None:
                    unresolved.append(f"{label}总额缺失，无法复算分项加总")
                    unresolved_codes.append("stated_total_missing")
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=f"{label}总额缺失，无法复算分项加总",
                        reason_codes=["stated_total_missing"],
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    continue
            else:
                stated, stated_raw = _table_general_public_expenditure_total(doc)
                total_origin_label = "《一般公共预算财政拨款支出决算表》合计"
                if stated is None or stated_raw is None:
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == FIN_05 table total (万元)",
                        reason=f"{label}总额缺失且表5合计未取到，无法复算分项加总",
                        reason_codes=["stated_total_missing"],
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    continue
            gap_summary = _completeness_summary(not_included_items, label)
            gap_codes = _completeness_codes(not_included_items)
            loc_note = _format_not_included_locations(not_included_items)
            withheld_summary = (
                f"另有{len(withheld_items)}个科目依法不予公开、金额未知"
                if withheld_items else ""
            )
            unknown = bool(not_included_items or withheld_items)
            if not included:
                if not_included_items:
                    detail = (
                        f"{label}无已纳入分项，{len(not_included_items)}处金额未纳入合计"
                        f"（{gap_summary}）{loc_note}"
                        + (f"；{withheld_summary}" if withheld_summary else "")
                        + "，不能发布分项加总"
                    )
                    codes = list(dict.fromkeys([
                        *(gap_codes or ["item_amount_not_included"]),
                        *(["withheld_amount_unknown", "sum_scope_not_comparable"]
                          if withheld_items else []),
                    ]))
                    unresolved.append(detail)
                    unresolved_codes.extend(codes)
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=detail,
                        reason_codes=codes,
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    continue
                if withheld_items:
                    detail = (
                        f"{label}仅有依法不予公开的分项，金额未知；"
                        "公开分项与全口径总额不可比，不做完整加总判断"
                    )
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="not_applicable",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=detail,
                        reason_codes=["withheld_amount_unknown", "sum_scope_not_comparable"],
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    continue
                continue
            subtotal = sum((record["value_wan"] for record in included), Decimal(0))
            # 每个分项按原文末位精度与来源单位系数折算为万元；总额来自正文时恒为万元，
            # 来自表5时用归一后的万元值重算显示精度（表内原文可能是“元”）。
            envelope = _records_display_envelope(
                included,
                extra_raws=[stated_raw if total is not None else format(stated, "f")],
            )
            difference = subtotal - stated
            known_exceeds = difference > envelope
            anchor_page, anchor_text, anchor_s, anchor_e = (
                _locate(total.start()) if total is not None else _locate_header()
            )
            calc_evidence.update({
                "subtotal_wan": _wan_display(subtotal),
                "stated_total_wan": _wan_display(stated),
                "difference_wan": _wan_display(difference),
            })
            trial = _conditional_trial(
                included, not_included_items, subtotal, stated, _locate
            )
            if trial is not None:
                calc_evidence["trial"] = trial
            if unknown:
                if known_exceeds:
                    parts = [
                        f"已公开分项{label}合计{_wan_display(subtotal)}万元"
                        f"（纳入{len(included)}项），已超过{total_origin_label}"
                        f"{_wan_display(stated)}万元，差{_wan_display(abs(difference))}万元；"
                        "即使未纳入或依法不予公开的分项为零，该加总关系也不成立"
                    ]
                    if not_included_items:
                        parts.append(f"另有{gap_summary}{loc_note}，未纳入合计")
                    if trial is not None:
                        parts.append(
                            f"条件试算：若未确定单位的{len(trial['evidence'])}项按万元理解，"
                            f"合计为{trial['subtotal_wan']}万元（差{trial['difference_wan']}万元）；"
                            "该试算仅为假设，不计通过率，不解除检查未完成"
                        )
                    if withheld_items:
                        parts.append(withheld_summary)
                    message = "；".join(parts) + "。"
                    issues.append(issue_at(
                        self, message, anchor_page, anchor_text, anchor_s, anchor_e, severity="warn",
                        checklist_item=checklist,
                        included_items=[_operand_evidence(record) for record in included],
                        not_included_items=[_operand_evidence(record) for record in not_included_items],
                        withheld_items=withheld_items,
                        disclosure_exceptions=exception_evidence,
                        stated_total_wan=_wan_display(stated),
                        included_subtotal_wan=_wan_display(subtotal),
                        difference_wan=_wan_display(difference),
                        completeness="partial",
                        calculation_evidence=calc_evidence,
                    ))
                    codes = list(dict.fromkeys([
                        *(gap_codes or (["item_amount_not_included"]
                                        if not_included_items else [])),
                        "item_sum_mismatch",
                        *(["withheld_amount_unknown"] if withheld_items else []),
                    ]))
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data" if not_included_items else "fail",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=message,
                        reason_codes=codes,
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    if not_included_items:
                        from .diagnostic_fields import blocker, record_gap
                        scope_entry = dict(
                            reason_codes=['scope_unconfirmed', 'item_amount_not_included',
                                          'sum_scope_not_comparable'],
                            blocked_by=[blocker(dimension='fund_scope',
                                                ref=f'V33-251:{kind}:{label}:分项范围',
                                                reason_code='scope_unconfirmed')])
                        unresolved_text = (
                            f"{label}有{len(not_included_items)}处金额未纳入合计：{gap_summary}{loc_note}"
                        )
                        record_gap(doc, unresolved_text, **scope_entry)
                        unresolved.append(unresolved_text)
                        unresolved_codes.extend(gap_codes or ["item_amount_not_included"])
                        if withheld_items:
                            unresolved_codes.extend(
                                ["withheld_amount_unknown", "sum_scope_not_comparable"]
                            )
                    continue
                if not_included_items:
                    parts = [
                        f"已公开分项{label}合计{_wan_display(subtotal)}万元"
                        f"（纳入{len(included)}项）",
                        f"另有{gap_summary}{loc_note}，未纳入合计",
                    ]
                    if withheld_items:
                        parts.append(withheld_summary)
                    if trial is not None:
                        parts.append(
                            f"条件试算：若未确定单位的{len(trial['evidence'])}项按万元理解，"
                            f"合计为{trial['subtotal_wan']}万元（差{trial['difference_wan']}万元）；"
                            "该试算仅为假设，不计通过率，不解除检查未完成"
                        )
                    parts.append(
                        "公开分项与全口径总额范围不一致，不计算完整加总差额，"
                        "不能视为完整分项加总"
                    )
                    message = "；".join(parts) + "。"
                    not_itemized_only = bool(not_included_items) and all(
                        record.get("not_included_reason") == "item_budget_not_itemized"
                        for record in not_included_items
                    )
                    severity = "warn" if not_itemized_only else "info"
                    if not_itemized_only:
                        # 逐项预算未列示是具体披露问题；支出决算分项已列示并按
                        # 组级合计加总，不把组级预算复制到各科目。
                        message = (
                            f"支出决算具体情况中有 {len(not_included_items)} 处项级科目"
                            "仅披露组级年初预算合计、未逐项列示年初预算；"
                            f"已列示的{label}分项与组级合计已按原文核对，"
                            "未将组级预算分摊到各科目，请补充逐项年初预算或说明口径。"
                        )
                    # DH-02：回执创建前登记结构化卡点（分项范围不一致）。
                    from .diagnostic_fields import blocker as _diag_blocker, record_gap as _diag_gap
                    _scope_entry = dict(
                        reason_codes=['scope_unconfirmed', 'item_amount_not_included',
                                      'sum_scope_not_comparable'],
                        blocked_by=[_diag_blocker(dimension='fund_scope',
                                                  ref=f'V33-251:{kind}:{label}:分项范围',
                                                  reason_code='scope_unconfirmed')])
                    _diag_gap(doc, message, **_scope_entry)
                    issues.append(issue_at(
                        self, message, anchor_page, anchor_text, anchor_s, anchor_e, severity=severity,
                        checklist_item=checklist,
                        included_items=[_operand_evidence(record) for record in included],
                        not_included_items=[_operand_evidence(record) for record in not_included_items],
                        withheld_items=withheld_items,
                        disclosure_exceptions=exception_evidence,
                        stated_total_wan=_wan_display(stated),
                        included_subtotal_wan=_wan_display(subtotal),
                        difference_wan=None,
                        completeness="partial",
                        calculation_evidence=calc_evidence,
                    ))
                    codes = list(dict.fromkeys([
                        *(gap_codes or ["item_amount_not_included"]),
                        "sum_scope_not_comparable",
                        *(["withheld_amount_unknown"] if withheld_items else []),
                    ]))
                    relation_receipt(
                        self, doc, f"V33-251:{kind}:item_sum", [],
                        status="insufficient_data",
                        formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                        reason=message,
                        reason_codes=codes,
                        disclosure_sources=exception_evidence,
                        calculation_evidence=calc_evidence,
                    )
                    from .diagnostic_fields import blocker, record_gap
                    scope_entry = dict(
                        reason_codes=['scope_unconfirmed', 'item_amount_not_included',
                                      'sum_scope_not_comparable'],
                        blocked_by=[blocker(dimension='fund_scope',
                                            ref=f'V33-251:{kind}:{label}:分项范围',
                                            reason_code='scope_unconfirmed')])
                    unresolved_text = (
                        f"{label}有{len(not_included_items)}处金额未纳入合计：{gap_summary}{loc_note}"
                    )
                    record_gap(doc, unresolved_text, **scope_entry)
                    unresolved.append(unresolved_text)
                    unresolved_codes.extend(gap_codes or ["item_amount_not_included"])
                    if withheld_items:
                        unresolved_codes.extend(
                            ["withheld_amount_unknown", "sum_scope_not_comparable"]
                        )
                    continue
                detail = (
                    f"{label}有{len(withheld_items)}个科目依法不予公开，金额未知；"
                    "公开分项与全口径总额不可比，不做完整加总判断"
                )
                relation_receipt(
                    self, doc, f"V33-251:{kind}:item_sum", [],
                    status="not_applicable",
                    formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                    reason=detail,
                    reason_codes=["withheld_amount_unknown", "sum_scope_not_comparable"],
                    disclosure_sources=exception_evidence,
                    calculation_evidence=calc_evidence,
                )
                continue
            if not (abs(difference) > envelope):
                calc_evidence["outcome"] = "pass"
                pass_reason = None
                if unit_facts:
                    basis_labels = []
                    for record in included:
                        basis = record.get("table_unit_basis")
                        if basis is None:
                            continue
                        refs = (getattr(basis, "extra_dims", None) or {}).get(
                            "evidence_refs") or []
                        page = next(
                            (ref.get("physical_page")
                             for ref in refs
                             if isinstance(ref, dict) and ref.get("physical_page")),
                            None,
                        )
                        basis_labels.append(
                            f"第{page}页{getattr(basis, 'subject_name', '') or ''}"
                            f"（科目{getattr(basis, 'subject_code', '') or ''}）"
                            f"{record['raw']}{record['unit']}"
                        )
                    pass_reason = (
                        f"已公开分项{label}合计{_wan_display(subtotal)}万元"
                        f"（纳入{len(included)}项）"
                    )
                    if basis_labels:
                        pass_reason += (
                            "；其中" + "、".join(basis_labels[:3])
                            + "由同科目原表决算格证实单位"
                        )
                relation_receipt(
                    self, doc, f"V33-251:{kind}:item_sum", unit_facts,
                    status="pass",
                    formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                    reason=pass_reason,
                    reason_codes=["source_table_unit_confirmed"] if unit_facts else [],
                    disclosure_sources=exception_evidence,
                    calculation_evidence=calc_evidence,
                )
                continue
            unit_items = [record for record in included if record["unit"] != "万元"
                          and not (record.get("source") == "explicit_zero"
                                   and record["unit"] is None and record["value_wan"] == 0)]
            anchors = []
            for record in unit_items:
                page, text, s, e = _locate(record["pos"])
                anchors.append({
                    "page": page, "amount": record["raw"], "unit": record["unit"],
                    "value_wan": _wan_trim(record["value_wan"]),
                    "section": "支出决算具体情况", "span": text[s:e],
                })
            parts = [
                f"支出决算具体情况逐项列示的{label}合计{_wan_display(subtotal)}万元"
                f"（纳入{len(included)}项），与{total_origin_label}{_wan_display(stated)}万元不一致，"
                f"相差{_wan_display(abs(difference))}万元"
            ]
            conditional_assumed = conditional_included = conditional_delta = None
            if unit_items:
                units = "、".join(sorted({record["unit"] for record in unit_items}))
                conditional_assumed = sum(
                    (_dec(record["raw"]) or Decimal(0) for record in unit_items), Decimal(0)
                )
                conditional_included = sum(
                    (record["value_wan"] for record in unit_items), Decimal(0)
                )
                conditional_delta = conditional_assumed - conditional_included
                parts.append(f"其中{len(unit_items)}项原文以{units}列示，已按原文单位折算为万元")
                basis = (
                    f"当前已纳入{_wan_display(conditional_included)}万元，"
                    f"若按万元名义值替换为{_wan_display(conditional_assumed)}万元"
                )
                if conditional_delta > 0:
                    parts.append(
                        f"条件性说明：若这些项按万元名义值计入，合计将增加"
                        f"{_wan_display(conditional_delta)}万元（{basis}）；"
                        "本规则不采用该假设，单位问题另行确认"
                    )
                elif conditional_delta < 0:
                    parts.append(
                        f"条件性说明：若这些项按万元名义值计入，合计将减少"
                        f"{format(abs(conditional_delta), 'f')}万元（{basis}）；"
                        "本规则不采用该假设，单位问题另行确认"
                    )
                else:
                    parts.append(
                        f"条件性说明：若这些项按万元名义值计入，合计不变（{basis}）；"
                        "本规则不采用该假设，单位问题另行确认"
                    )
            if subtotal < stated:
                parts.append("可能漏列科目")
            else:
                parts.append("分项合计超出总额")
            message = "；".join(parts) + "。"
            calc_evidence["outcome"] = "fail"
            receipt_codes = ["item_sum_mismatch"]
            if unit_items:
                receipt_codes.append("source_unit_normalized")
            if unit_facts:
                receipt_codes.append("source_table_unit_confirmed")
            # 2A：差额被同源单位错精确解释时，下游不再产出独立正式问题；
            # 金额子检查仍为 executed，回执记录解释来源与剩余差额。
            explained = (
                conditional_delta is not None
                and _unit_error_explains_amount(difference, conditional_delta, AMOUNT_TOLERANCE_WAN)
            )
            if explained:
                receipt_codes.append("explained_by_unit_error")
                relation_receipt(
                    self, doc, f"V33-251:{kind}:item_sum",
                    [],
                    status="fail",
                    formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                    reason=message,
                    reason_codes=receipt_codes,
                    disclosure_sources=exception_evidence,
                    amount_subcheck_status="executed",
                    explained_by="unit_error",
                    residual_difference_wan=format(
                        max(abs(difference) - abs(conditional_delta), Decimal(0)), "f"),
                    cascade_of=_runtime_defect_key(doc, anchor_page, f"{label}:{_wan_display(conditional_assumed)}"),
                    calculation_evidence=calc_evidence,
                )
                continue
            issues.append(issue_at(
                self, message, anchor_page, anchor_text, anchor_s, anchor_e,
                severity="warn", checklist_item=checklist,
                # R1（2026-11-20 独立复核）：失败分支也必须复用可序列化的
                # 操作数证据。直接拷贝 record 会把运行期 FiscalFact
                # （table_unit_basis）带进分析快照，dumps_canonical 拒绝入库，
                # 差额报告因此无法发布。_operand_evidence 保留事实 ID、原表、
                # 原句和页码，只把 FiscalFact 换成摘要。
                included_items=[_operand_evidence(record) for record in included],
                not_included_items=[_operand_evidence(record) for record in not_included_items],
                unit_normalized_items=anchors,
                disclosure_exceptions=exception_evidence,
                stated_total_wan=_wan_display(stated),
                included_subtotal_wan=_wan_display(subtotal),
                difference_wan=_wan_display(difference),
                completeness="complete",
                calculation_evidence=calc_evidence,
                # 两种口径属于同一原文节的整改事项，但各自算式和失败回执独立保留。
                detail_section_anchor={
                    "page": anchor_page,
                    "span": unique_quote(anchor_text, anchor_s, anchor_e),
                },
                check_relation_id=f"{self.code}:V33-251:{kind}:item_sum",
                **({"conditional_assumed_wan": format(conditional_assumed, "f"),
                    "conditional_included_wan": format(conditional_included, "f"),
                    "conditional_delta_wan": format(conditional_delta, "f")}
                   if conditional_delta is not None else {}),
            ))
            relation_receipt(
                self, doc, f"V33-251:{kind}:item_sum", unit_facts,
                status="fail",
                formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                reason=message,
                reason_codes=receipt_codes,
                disclosure_sources=exception_evidence,
                amount_subcheck_status="executed",
                calculation_evidence=calc_evidence,
            )
        issues.extend(self._coverage(doc, joined, items, _locate, listed_final))
        if unresolved:
            raise RuleDeferred(
                self.code, detail="；".join(unresolved), partial_issues=issues,
                unresolved_reasons=list(dict.fromkeys(unresolved)),
                reason_codes=list(dict.fromkeys(unresolved_codes)),
            )
        return issues

    def _coverage(self, doc: Document, joined: str, items, locate,
                  listed_final: int = 0) -> List[Issue]:
        """决算表项级（7 位）本年支出非零的科目，应在具体情况中逐项出现。"""
        facts, reason = table_facts(doc, "FIN_05_general_public_expenditure")
        if reason:
            return []
        # 项级以编码层级判定：2022 版补零编码里 2080000/2080500 是类/款，不是项。
        from src.services.fiscal_code_hierarchy import leaf_codes

        try:
            leaf_set = leaf_codes({str(f.subject_code) for f in facts if str(f.subject_code or "").isdigit()})
        except ValueError:
            return []
        leaves = {}
        for fact in facts:
            code = str(fact.subject_code or "")
            if (len(code) == 7 and code in leaf_set and fact.indicator == "total_actual"
                    and _canonical_budget_stage(fact.budget_stage) == "决算"
                    and fact.normalized_value not in (None, Decimal(0))):
                leaves[code] = re.sub(r"\s+", "", fact.subject_name or "")
        def _present(name: str) -> bool:
            # 及/与、其他/其它只用于“是否出现”的候选判定；名称差异由名称规则报告。
            variants = {name, name.replace("与", "及"), name.replace("及", "与"),
                        name.replace("其他", "其它"), name.replace("其它", "其他")}
            return any(variant and variant in joined for variant in variants)

        missing = [name for name in leaves.values() if name and not _present(name)]
        listed = max(len(items), listed_final)
        if not missing or listed >= len(leaves):
            return []
        page, text, s, e = locate(0)
        return [issue_at(
            self, f"支出决算具体情况漏列科目：决算表项级科目{len(leaves)}个，说明只列{listed}项，"
            f"未见“{'、'.join(missing[:4])}”{'等' if len(missing) > 4 else ''}。",
            page, text, s, e, severity="warn", checklist_item="A-09", missing_subjects=missing[:10],
        )]


# ---------------------------------------------------------------------------
# V33-252 财政拨款收支总表基金/国资列 ↔ 对应决算表
# ---------------------------------------------------------------------------


def _total_fact(facts: Sequence[Any], indicator: str, names: Sequence[str] = ()) -> Optional[Any]:
    candidates = [f for f in facts if f.indicator == indicator and f.subject_code == "total"
                  and _canonical_budget_stage(f.budget_stage) == "决算"
                  and (not names or any(n in re.sub(r"\s+", "", f.subject_name or "") for n in names))]
    return candidates[0] if len(candidates) == 1 else None


def _top_level_rows_total(facts: Sequence[Any]) -> Optional[Decimal]:
    """决算表顶层科目行（编码层级里没有上级行的行）本年支出之和；取不全返回 None。

    有类行时就是各类之和；表内只列项级行（如只有 2120803）时就是各项之和。
    任一顶层编码没有唯一可用金额就放弃推导，交调用方按取数不足上报。
    """
    from src.services.fiscal_code_hierarchy import hierarchy_paths

    rows = [f for f in facts if f.indicator == "total_actual" and str(f.subject_code or "").isdigit()
            and _canonical_budget_stage(f.budget_stage) == "决算"]
    paths = hierarchy_paths({str(f.subject_code) for f in rows})
    if not paths or len(set(paths.values())) != len(paths):
        return None
    top = {code for code, path in paths.items()
           if not any(other != path and path.startswith(other) for other in paths.values())}
    values: Dict[str, Decimal] = {}
    for fact in rows:
        code = str(fact.subject_code)
        if code not in top:
            continue
        if fact.normalized_value is None or code in values:
            return None
        values[code] = Decimal(str(fact.normalized_value))
    if set(values) != top:
        return None
    return sum(values.values(), Decimal(0))


class R33252_FundColumnsCrossTable(Rule):
    code, severity = "V33-252", "error"
    desc = "财政拨款收支总表政府性基金/国有资本经营列与对应决算表一致（清单 C-04）"

    def apply(self, doc: Document) -> List[Issue]:
        t4, reason = table_facts(doc, "FIN_04_fiscal_grant_total")
        if reason:
            raise RuleDeferred(self.code, detail=reason, unresolved_reasons=[reason])
        issues: List[Issue] = []
        unresolved: List[str] = []
        for indicator, table_code, title in (
            ("government_fund_budget", "FIN_08_gov_fund", "政府性基金预算财政拨款收入支出决算表"),
            ("state_capital_budget", "FIN_09_state_capital", "国有资本经营预算财政拨款收入支出决算表"),
        ):
            column = _total_fact(t4, indicator, ("本年支出合计",))
            if column is None or column.normalized_value is None:
                continue
            column_value = Decimal(str(column.normalized_value))
            detail, detail_reason = table_facts(doc, table_code)
            if detail_reason:
                if column_value != 0:
                    unresolved.append(f"{title}：{detail_reason}")
                continue
            target = _total_fact(detail, "total_actual")
            if target is None:
                # 区级模板把“合计/基本支出/项目支出”子表头印在合计行上，合计行本年支出
                # 没有金额（原件版式，不是解析错误）：按顶层科目行（类行；只列项级行的表
                # 即全部项级行）之和推导本年支出合计。
                target_value = _top_level_rows_total(detail)
            else:
                target_value = Decimal(str(target.normalized_value)) if target.normalized_value is not None else None
            if target_value is None:
                if column_value != 0:
                    unresolved.append(f"{title}本年支出合计未取到")
                continue
            level, diff = classify_amount_diff(column_value, target_value, 1)
            if level != "mismatch":
                continue
            refs = list(column.extra_dims.get("evidence_refs", []) or [])
            page = refs[0].get("physical_page") if refs else None
            text = str(doc.page_texts[page - 1] if page and page <= len(doc.page_texts) else "")
            pos = text.find("本年支出合计")
            if not page or pos < 0:
                unresolved.append(f"财政拨款收支总表“{title[:6]}”列本年支出合计原文未定位")
                continue
            issues.append(issue_at(
                self, f"财政拨款收入支出决算总表“{title[:7]}”列本年支出合计{column_value:.2f}万元，与《{title}》"
                f"本年支出合计{target_value:.2f}万元不一致，相差{diff:.2f}万元。",
                page, text, pos, pos + 6, severity="error", checklist_item="C-04", related_table=table_code,
            ))
        if unresolved:
            raise RuleDeferred(self.code, detail="；".join(unresolved), partial_issues=issues,
                               unresolved_reasons=unresolved)
        return issues


# ---------------------------------------------------------------------------
# CMM-026 有数据的表不得保留空表说明
# ---------------------------------------------------------------------------

_EMPTY_NOTE_RE = re.compile(r"(?:故|因此|所以)?本表(?:无数据|为空表|无内容)|无此项(?:资金|预算)?(?:安排)?[，,]?故本表")
_SUBJECTS = ("政府性基金", "国有资本经营", "三公")


def _has_data(table: Sequence[Sequence[Any]]) -> bool:
    for row in table or []:
        for cell in row or []:
            text = re.sub(r"[,\s]", "", str(cell or ""))
            if re.fullmatch(r"-?\d+\.\d+", text) and Decimal(text) != 0:
                return True
    return False


class CMM026_EmptyNoteOnDataTable(Rule):
    code, severity = "CMM-026", "warn"
    desc = "有数据的表不得保留空表说明（预/决算通用，清单 A-07）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        tables = list(getattr(doc, "page_tables", None) or [])
        for page, text in iter_pages(doc):
            if not page_has_table(doc, page - 1):
                continue
            note = _EMPTY_NOTE_RE.search(text)
            if not note:
                continue
            page_tables = tables[page - 1] if page - 1 < len(tables) else []
            if not any(_has_data(t) for t in page_tables):
                continue
            title = compact("\n".join(text.split("\n")[:4]))
            sentence_start = max(text.rfind("\n", 0, note.start()), text.rfind("注", 0, note.start()))
            sentence = compact(text[max(0, sentence_start):note.end()])
            subject = next((s for s in _SUBJECTS if s in sentence), None)
            if subject and subject not in title:
                continue  # 说明对象与本页数据表不是同一张表（同页另有空表）
            issues.append(issue_at(
                self, "表格已有数据，但表下仍保留“本表无数据/空表”说明，应删除该说明。",
                page, text, max(0, note.start() - 20), note.end(), severity="warn", checklist_item="A-07",
            ))
        return issues


CHECKLIST_FINAL_RULES: List[Rule] = [
    R33251_FunctionalDetailCompleteness(),
    R33252_FundColumnsCrossTable(),
]

def _register() -> None:
    from . import rules_v33

    known = {getattr(rule, "code", None) for rule in rules_v33.ALL_RULES}
    rules_v33.ALL_RULES.extend(rule for rule in CHECKLIST_FINAL_RULES if rule.code not in known)


_register()

__all__ = [
    "CHECKLIST_FINAL_RULES",
    "CMM026_EmptyNoteOnDataTable",
    "R33251_FunctionalDetailCompleteness",
    "R33252_FundColumnsCrossTable",
]
