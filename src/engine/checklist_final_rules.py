"""检查点清单：决算专用规则与预决算通用的空表说明反向检查（2026-10-22 全系统默认）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| V33-251 | 决算 A-09/C-11/C-12 | 一般公共预算财政拨款支出决算具体情况：逐项细化到项、年初预算与支出决算分项加总等于总额、不漏列科目 |
| V33-252 | 决算 C-04 | 财政拨款收支总表“政府性基金/国有资本经营”列本年支出 = 对应决算表本年支出合计 |
| CMM-026 | 预算 A-07、决算 A-07 | 有数据的表不得保留“本表无数据/空表”说明 |
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .amount_math import classify_amount_diff
from .rule_policy import AMOUNT_TOLERANCE_WAN
from .checklist_support import compact, display_envelope, flat_span, issue_at, iter_pages, page_flat, page_has_table, unique_quote
from .fact_context import _canonical_budget_stage
from .native_table_arithmetic import table_facts
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import Document, Issue, Rule, _unit_error_explains_amount


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

_AMOUNT = r"(\d[\d,]*(?:\.\d+)?)"


def _dec(text: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


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
    r"(?P<name>[\u4e00-\u9fff“”\"'（）()]{2,40}?)"
    r"(?P<stage>年初支出预算|年初预算|支出决算|决算数|决算支出|决算)?"
    r"(?:数)?(?:为|是)?\s*[:：]?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)"
)
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


def _shared_group_disclosures(
    joined: str,
    clauses: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
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
        "budget_not_itemized": set(),
        "components": set(),
        "totals": set(),
        "untrusted_totals": set(),
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
        for match in _GROUP_ITEM_AMOUNT_RE.finditer(enum_text):
            value = _dec(match.group("amount"))
            factor = _AMOUNT_UNIT_FACTOR.get(match.group("unit"))
            if value is None or factor is None:
                continue
            name = re.sub(r"^[、，,；;：:\s]+", "", match.group("name") or "")
            name = re.sub(r"\s+", "", name).strip("“”\"'（）()")
            if not name:
                continue
            stage_word = match.group("stage") or ""
            per_item.append({
                "name": name,
                "raw": match.group("amount"),
                "unit": match.group("unit"),
                "value_wan": value * factor,
                "pos": enum_start + match.start(),
                "role": ("budget" if stage_word in _DECL_BUDGET_KINDS else "final")
                if stage_word else None,
                "stage_word": stage_word,
            })
        if len(per_item) < 2:
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
        # 组级合计：枚举句之后仍属同一编号条目的预算/决算声明；按阶段分开取。
        total_positions = {
            int(record["pos"]) for record in records
            if enum_end <= int(record["pos"]) < item_end
            and record.get("kind") in {"budget", "final"}
        }
        prefix_stage = None
        prefix_match = _GROUP_ENUM_PREFIX_STAGE_RE.match(enum_text)
        if prefix_match:
            prefix_stage = (
                "budget" if prefix_match.group(1) in _DECL_BUDGET_KINDS else "final"
            )
        explicit_roles = {item["role"] for item in per_item if item["role"]}
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
            if stated_total is None:
                arithmetic_status = "insufficient_data"
                arithmetic_reason = (
                    "共享分项缺少同阶段组级合计，无法核对组内分项算术"
                )
            else:
                envelope = display_envelope(
                    [format(group_sum, "f"), format(stated_total, "f")]
                )
                difference = group_sum - stated_total
                if abs(difference) <= envelope:
                    arithmetic_status = "pass"
                else:
                    arithmetic_status = "fail"
                    arithmetic_reason = (
                        f"组内分项合计{format(group_sum, 'f')}与组级合计"
                        f"{format(stated_total, 'f')}不一致，差{format(abs(difference), 'f')}"
                    )
                    if total_pos is not None:
                        result["untrusted_totals"].add(total_pos)

            # 每个有科目名的子句分别判定；重叠切分产生的无名字段不在这里记缺口。
            for binding in bindings:
                if not binding["name"]:
                    continue
                if binding["own"] or binding["matched"]:
                    if stage == "final":
                        result["final_disclosed"].add(binding["clause_start"])
                elif stage == "final":
                    # 共享枚举里没有能对应到本科目的决算分项：不能按“数量够了”
                    # 认定已披露，保留具体缺口。
                    for clause in subject_clauses:
                        if int(clause["start"]) == binding["clause_start"]:
                            clause["group_component_unbound"] = True
                            break

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
                "ambiguous_bindings": list(dict.fromkeys(ambiguous_names)),
                "name_candidates": name_candidates,
                "mixed_roles": mixed_roles,
                "total_pos": total_pos,
            }
            result["group_checks"].append(check)
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
    shared = _shared_group_disclosures(joined, clauses, records)
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
                usable = [record for record in kind_records
                          if record["value_wan"] is not None and low <= record["pos"] < high]
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
            if not kind_records and kind == "final" and start in shared["final_disclosed"]:
                # 同一编号条目内多个项级科目共享“其中：”分项决算枚举，
                # 原文本已逐项列出金额；不能再报“未披露支出决算”。
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
        if joined:
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
    unusable = (
        len(items) - missing - not_itemized - conflict - ambiguous - dup_unit
        - unbound - group_conflict
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


def _operand_evidence(record: Dict[str, Any]) -> Dict[str, Any]:
    """证据保留原始数值精度，同时保证分析快照可序列化。"""
    evidence = dict(record)
    if isinstance(evidence.get("value_wan"), Decimal):
        evidence["value_wan"] = format(evidence["value_wan"], "f")
    return evidence


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
        for index, (_, _, flat, _, begin, end) in enumerate(blocks):
            spans.append((len(joined), index, begin))
            joined += flat[begin:end]

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
                **({"bad_unit": bad_unit} if bad_unit else {}),
                "value_wan": value * factor if value is not None and factor is not None else None,
                "not_included_reason": (
                    "duplicated_unit" if bad_unit
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
        accounting = _clause_declaration_accounting(
            joined, clauses, records, tail_start
        )
        # 共享“其中”分项：组内科目绑定与组级算术分别留回执；
        # 组级合计只有在组内分项可核对时才作为章节加总数使用。
        for check in accounting.get("group_checks") or []:
            kind = str(check.get("kind") or "")
            relation = f"V33-251:{kind}:group:{check.get('item_start')}"
            blocking = []
            hints = []
            if check.get("arithmetic_status") == "fail":
                blocking.append(str(check.get("reason") or "组内分项与组级合计不一致"))
            elif check.get("arithmetic_status") == "insufficient_data":
                blocking.append(
                    str(check.get("reason") or "组内分项缺少同阶段组级合计，无法核对算术")
                )
            if check.get("unbound_subjects"):
                blocking.append(
                    "共享分项未能对应到项级科目："
                    + "、".join(str(value) for value in check["unbound_subjects"])
                )
                # 标题科目存在未绑定时，无法归属的分项是错配证据，一并列入。
                if check.get("unbound_components"):
                    blocking.append(
                        "共享分项中存在无法归属的条目："
                        + "、".join(str(value) for value in check["unbound_components"])
                    )
            if check.get("ambiguous_bindings"):
                blocking.append(
                    "共享分项与科目对应存在歧义（重复名称或一名多配）："
                    + "、".join(str(value) for value in check["ambiguous_bindings"])
                )
            if check.get("unresolved_role_components"):
                blocking.append(
                    "共享分项未写明金额阶段，无法纳入组内核对："
                    + "、".join(str(value) for value in check["unresolved_role_components"])
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
                relation_receipt(
                    self, doc, relation, [],
                    status="fail" if arithmetic_fail else "insufficient_data",
                    formula="sum(shared components) == group total (万元)",
                    reason=message,
                    reason_codes=(
                        ["item_sum_mismatch"]
                        if arithmetic_fail
                        else ["item_disclosure_missing"]
                    ),
                )
                page, text, start, end = _locate(int(check.get("anchor_pos") or 0))
                issues.append(issue_at(
                    self, message, page, text, start, end,
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
                    )
                    continue
            gap_summary = _completeness_summary(not_included_items, label)
            gap_codes = _completeness_codes(not_included_items)
            withheld_summary = (
                f"另有{len(withheld_items)}个科目依法不予公开、金额未知"
                if withheld_items else ""
            )
            unknown = bool(not_included_items or withheld_items)
            if not included:
                if not_included_items:
                    detail = (
                        f"{label}无已纳入分项，{len(not_included_items)}处金额未纳入合计"
                        f"（{gap_summary}）"
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
                    )
                    continue
                continue
            subtotal = sum((record["value_wan"] for record in included), Decimal(0))
            envelope = display_envelope(
                [stated_raw, *[format(record["value_wan"], "f") for record in included]]
            )
            difference = subtotal - stated
            known_exceeds = difference > envelope
            anchor_page, anchor_text, anchor_s, anchor_e = (
                _locate(total.start()) if total is not None else _locate_header()
            )
            if unknown:
                if known_exceeds:
                    parts = [
                        f"已公开分项{label}合计{format(subtotal, 'f')}万元"
                        f"（纳入{len(included)}项），已超过{total_origin_label}"
                        f"{format(stated, 'f')}万元，差{format(abs(difference), 'f')}万元；"
                        "即使未纳入或依法不予公开的分项为零，该加总关系也不成立"
                    ]
                    if not_included_items:
                        parts.append(f"另有{gap_summary}，未纳入合计")
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
                        stated_total_wan=format(stated, "f"),
                        included_subtotal_wan=format(subtotal, "f"),
                        difference_wan=format(difference, "f"),
                        completeness="partial",
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
                            f"{label}有{len(not_included_items)}处金额未纳入合计：{gap_summary}"
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
                        f"已公开分项{label}合计{format(subtotal, 'f')}万元"
                        f"（纳入{len(included)}项）",
                        f"另有{gap_summary}，未纳入合计",
                    ]
                    if withheld_items:
                        parts.append(withheld_summary)
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
                        stated_total_wan=format(stated, "f"),
                        included_subtotal_wan=format(subtotal, "f"),
                        difference_wan=None,
                        completeness="partial",
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
                    )
                    from .diagnostic_fields import blocker, record_gap
                    scope_entry = dict(
                        reason_codes=['scope_unconfirmed', 'item_amount_not_included',
                                      'sum_scope_not_comparable'],
                        blocked_by=[blocker(dimension='fund_scope',
                                            ref=f'V33-251:{kind}:{label}:分项范围',
                                            reason_code='scope_unconfirmed')])
                    unresolved_text = (
                        f"{label}有{len(not_included_items)}处金额未纳入合计：{gap_summary}"
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
                )
                continue
            if not (abs(difference) > envelope):
                relation_receipt(
                    self, doc, f"V33-251:{kind}:item_sum", [],
                    status="pass",
                    formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                    reason=None,
                    reason_codes=[],
                    disclosure_sources=exception_evidence,
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
                    "value_wan": format(record["value_wan"], "f"),
                    "section": "支出决算具体情况", "span": text[s:e],
                })
            parts = [
                f"支出决算具体情况逐项列示的{label}合计{format(subtotal, 'f')}万元"
                f"（纳入{len(included)}项），与{total_origin_label}{format(stated, 'f')}万元不一致，"
                f"相差{format(abs(difference), 'f')}万元"
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
                    f"当前已纳入{format(conditional_included, 'f')}万元，"
                    f"若按万元名义值替换为{format(conditional_assumed, 'f')}万元"
                )
                if conditional_delta > 0:
                    parts.append(
                        f"条件性说明：若这些项按万元名义值计入，合计将增加"
                        f"{format(conditional_delta, 'f')}万元（{basis}）；"
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
            receipt_codes = ["item_sum_mismatch"]
            if unit_items:
                receipt_codes.append("source_unit_normalized")
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
                    cascade_of=_runtime_defect_key(doc, anchor_page, f"{label}:{format(conditional_assumed, 'f')}"),
                )
                continue
            issues.append(issue_at(
                self, message, anchor_page, anchor_text, anchor_s, anchor_e,
                severity="warn", checklist_item=checklist,
                included_items=[
                    {**{k: v for k, v in record.items() if k != "value_wan"},
                     "value_wan": format(record["value_wan"], "f")}
                    for record in included
                ],
                not_included_items=not_included_items,
                unit_normalized_items=anchors,
                disclosure_exceptions=exception_evidence,
                stated_total_wan=format(stated, "f"),
                included_subtotal_wan=format(subtotal, "f"),
                difference_wan=format(difference, "f"),
                completeness="complete",
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
                self, doc, f"V33-251:{kind}:item_sum", [],
                status="fail",
                formula="sum(item declared amounts normalized by original unit) == stated section total (万元)",
                reason=message,
                reason_codes=receipt_codes,
                disclosure_sources=exception_evidence,
                amount_subcheck_status="executed",
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
