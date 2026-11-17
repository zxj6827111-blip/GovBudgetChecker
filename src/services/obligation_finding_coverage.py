"""判断一条未完成的检查义务是否已被同源正式问题覆盖。

为什么需要这一层
----------------
引擎的"取数不足"有时不是新问题，而是**同一条问题导致检查做不下去**。
最典型的是 T5：说明里的款名写成旧称“行政事业单位离退休”，表格里是
“行政事业单位养老支出”。V33-227 已经把名称不一致作为正式问题报出来了；
但 V33-222 因为名称对不上绑不到金额，义务仍停在 ``insufficient_data``。
如果两条都进复核队列，用户会在"参考问题"里看到一次、在"检查补核"里又看到
一次，重复且没有新增信息。

本模块只做**去重**，不改变问题本身：义务被覆盖后不再阻塞补核门禁，
问题仍留在问题清单里，由人工按问题流程处理一次。

安全边界
--------
- 只处理 ``insufficient_data`` 的义务；``not_executed``（规则没实现）不隐藏；
- 只认**正式** finding（证据降级 / manual_review / info 不算）；
- finding 必须属于当前可见问题集合（被 legacy 忽略的问题不能用来关义务）；
- 义务 detail 里必须有**可辨识的引号短语**（如旧科目名），且该短语出现在
  finding 的 message/title/location 里；泛化词（“一般公共”“决算数”等）
  不参与匹配，避免把不相关的检查悄悄关掉；
- finding 的 checker 编号必须与义务的 checker 有交集。
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from src.services.evidence_guard import is_formal_finding

#: detail 里的中文引号短语（旧科目名、说明名等）。
_QUOTED_RE = re.compile(r"[“「]([^”」]{2,40})[”」]")

#: 太泛的词不参与“同源”匹配，否则任何包含“一般公共”的 finding 都能关义务。
_GENERIC_TERMS: Set[str] = {
    "一般公共",
    "一般公共预算",
    "财政拨款",
    "年初预算",
    "决算数",
    "预算数",
    "基本支出",
    "项目支出",
    "合计",
    "本年",
    "上年",
    "三公经费",
    "机关运行经费",
    "支出决算",
    "收入决算",
}

#: 至少 4 个字的短语才当作可辨识科目/说明名。
_MIN_TERM_LENGTH = 4

#: U-7：金额/数值类子检查的 checker。义务含这些 checker 时，只有同属金额族的问题
#: 才可能覆盖“金额检查做不下去”的副本；名称/单位类问题只合并展示，不得关闭、
#: 覆盖或隐藏未完成的金额子检查。
_AMOUNT_CHECKERS: Set[str] = {
    "V33-005",
    "V33-106",
    "V33-110",
    "V33-117",
    "V33-202",
    "V33-222",
    "V33-224",
    "V33-234",
    "V33-240",
    "V33-241",
    "V33-243",
    "V33-244",
    "V33-246",
    "V33-249",
    "V33-251",
    "V33-TREND-COMPLETION-RATE",
    "V33-SG-COMPLETION",
    "OBL-TREND-COMPLETION-RATE",
    "CMM-007",
    "CMM-020",
}


def _json_value(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _json_list(raw: Any) -> List[Any]:
    value = _json_value(raw)
    return value if isinstance(value, list) else []


def _current_rule_findings(result_row: Any) -> List[Dict[str, Any]]:
    """同一规则结果的 dual/legacy 存储适配；不改变来源与完成判据。"""
    result = _json_dict(result_row)
    findings = _json_list(result.get('rule_findings'))
    legacy = _json_value(result.get('issues'))
    if isinstance(legacy, list):
        findings += legacy
    elif isinstance(legacy, Mapping):
        if isinstance(legacy.get('all'), list):
            findings += legacy['all']
        else:
            findings += [item for bucket in ('error', 'warn', 'info')
                         for item in _json_list(legacy.get(bucket))]
    unique = {}
    for item in findings:
        if isinstance(item, dict) and item.get('id') and item.get('source') != 'ai':
            unique.setdefault(str(item['id']), item)
    return list(unique.values())


def distinctive_terms(detail: str) -> Set[str]:
    """从义务 detail 里取可辨识的引号短语；泛化词和短词剔除。"""
    terms = {
        match.group(1).strip()
        for match in _QUOTED_RE.finditer(str(detail or ""))
    }
    return {
        term
        for term in terms
        if len(term) >= _MIN_TERM_LENGTH and term not in _GENERIC_TERMS
    }


#: 单位/名称/格式类措辞：即使规则编号属于金额族，也不能当作金额检查已完成。
_NON_AMOUNT_MARKERS = (
    "单位口径", "单位错误", "单位错", "折合", "名称不一致", "名称差异",
    "科目名称", "漏字", "多字", "表名", "小数位", "千分位", "格式",
)
#: 明确指向金额复算/勾稽的措辞；无以上非金额措辞时才可作为金额覆盖。
_AMOUNT_MARKERS = (
    "金额", "差额", "合计", "不等于", "不一致", "相差", "完成率", "占比",
    "预算数", "决算数", "复算", "勾稽", "加总",
)
#: 义务 detail/missing_operands 里表示“金额取数未完成”的措辞。
_AMOUNT_OBLIGATION_MARKERS = (
    "金额", "差额", "合计", "取数", "绑定", "复算", "完成率", "占比",
    "决算数", "预算数", "加总",
)


def _requires_amount_evidence(item: Any) -> bool:
    """义务是否在等一个金额子检查结果。

    优先看 missing_operands 的金额列与 detail 语义；只有两者都没有时才回退
    到 checker 家族（仅作兼容兜底，不单独作为覆盖依据）。
    """
    for operand in getattr(item, "missing_operands", None) or []:
        if not isinstance(operand, dict):
            continue
        text = " ".join(str(operand.get(key) or "") for key in ("column", "role", "row_key"))
        if any(marker in text for marker in _AMOUNT_OBLIGATION_MARKERS):
            return True
    detail = str(getattr(item, "detail", None) or "")
    if any(marker in detail for marker in _AMOUNT_OBLIGATION_MARKERS):
        return True
    return False


def _finding_amount_kind(finding: Dict[str, Any]) -> str:
    """判断 finding 是金额结论、非金额结论还是无法判定。

    先看 check_relation_id/sub_check_id 的实际子检查；没有显式子检查时按
    正文措辞判定。无法判定返回 ``unknown``（金额义务下按不覆盖处理）。
    """
    location = _json_dict(finding.get("location"))
    relation = str(finding.get("check_relation_id") or location.get("check_relation_id")
                   or location.get("sub_check_id") or "").strip()
    if relation:
        tail = re.split(r"[:/]", relation)[-1].lower()
        if any(token in tail for token in ("unit", "name", "format", "structure", "scope")):
            return "non_amount"
        if any(token in tail for token in ("amount", "total", "sum", "diff", "balance", "value")):
            return "amount"
    # 只用问题正文判定：标题/位置字段可能沿用“名称”类通用模板，不能把
    # “金额不一致”正文误判成名称问题。
    text = str(finding.get("message") or finding.get("text_snippet") or "")
    if not text:
        text = _finding_text(finding)
    if any(marker in text for marker in _NON_AMOUNT_MARKERS):
        return "non_amount"
    if any(marker in text for marker in _AMOUNT_MARKERS):
        return "amount"
    return "unknown"


def _finding_text(finding: Dict[str, Any]) -> str:
    """finding 里可能带科目/说明名的字段拼成一段可搜索文本。"""
    location = _json_dict(finding.get("location"))
    display = _json_dict(finding.get("display"))
    parts: List[Any] = [
        finding.get("message"),
        finding.get("title"),
        finding.get("text_snippet"),
        finding.get("suggestion"),
    ]
    for key in (
        "actual_name",
        "expected_name",
        "subject",
        "subject_name",
        "section",
        "table",
        "row",
        "code",
    ):
        parts.append(location.get(key))
    parts.extend(_json_list(display.get("detail_lines")))
    return " ".join(str(part) for part in parts if part)


def _identity_text(value: Any) -> str:
    """身份字段归一：全半角、空白、大小写。不删除语义字符。"""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("code", "row", "name", "subject_code", "subject_name", "key"):
            if value.get(key) not in (None, ""):
                return _identity_text(value.get(key))
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", text).strip().lower()


def _identity_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _first_value(source: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


#: 页码字段：说明侧用 page，表格侧参考 pages 与事实源页。
_PAGE_KEYS = ("page", "physical_page", "page_number")


def _finding_pages(location: Mapping[str, Any]) -> Set[int]:
    pages: Set[int] = set()
    for key in _PAGE_KEYS:
        value = _identity_int(location.get(key))
        if value:
            pages.add(value)
    for key in ("pages", "fact_source_pages"):
        values = location.get(key)
        if isinstance(values, (list, tuple, set)):
            pages.update(value for value in (_identity_int(item) for item in values) if value)
    for item in location.get("fact_source_refs") or []:
        if isinstance(item, Mapping):
            value = _identity_int(item.get("page") or item.get("physical_page"))
            if value:
                pages.add(value)
    for item in location.get("table_refs") or []:
        if isinstance(item, Mapping):
            value = _identity_int(item.get("page") or item.get("physical_page"))
            if value:
                pages.add(value)
    return pages


#: 列身份的同义写法（表头措辞随模板变化，但检查关系不变）。
_COLUMN_ALIASES = {
    "决算数": "final", "决算金额": "final", "支出决算数": "final", "支出决算": "final",
    "本年支出": "final", "本年支出合计": "final", "合计": "total", "总计": "total",
    "预算数": "budget", "年初预算": "budget", "年初预算数": "budget",
    "基本支出": "basic", "项目支出": "project",
}


def _column_identity(value: Any) -> str:
    text = _identity_text(value)
    if not text:
        return ""
    return _COLUMN_ALIASES.get(text, text)


def _operand_identity(operand: Mapping[str, Any]) -> Dict[str, Any]:
    """缺数位置的身份维度。缺省字段留空，不用推断补值。"""
    return {
        "relation": _identity_text(_first_value(
            operand, ("check_relation_id", "sub_check_id", "relation_id"))),
        "source": _identity_text(operand.get("source")),
        "page": _identity_int(_first_value(operand, _PAGE_KEYS)),
        "table": _identity_text(_first_value(
            operand, ("table_instance_id", "table_code", "table"))),
        "table_instance": _identity_text(operand.get("table_instance_id")),
        "row": _identity_text(_first_value(operand, ("row_key", "row", "subject_code", "code"))),
        "column": _column_identity(_first_value(operand, ("column", "field"))),
        "year": _identity_int(_first_value(operand, ("year", "fiscal_year"))),
        "stage": _identity_text(_first_value(operand, ("stage", "budget_stage"))),
        "subject": _identity_text(_first_value(
            operand, ("org_name", "subject", "org", "organization"))),
        "file_hash": _identity_text(_first_value(operand, ("file_hash", "source_file_hash"))),
        "fact_version": _identity_text(_first_value(
            operand, ("fact_version", "fact_context_version_token", "version_token"))),
    }


def _finding_identity(finding: Mapping[str, Any]) -> Dict[str, Any]:
    """正式问题的位置身份。与义务缺数位置逐维可比。"""
    location = _json_dict(finding.get("location"))
    relation = _first_value(
        finding, ("check_relation_id", "sub_check_id", "relation_id"))
    if relation is None:
        relations = location.get("check_relation_ids")
        if isinstance(relations, (list, tuple)) and len(relations) == 1:
            relation = relations[0]
    return {
        "relation": _identity_text(relation) or _identity_text(
            _first_value(location, ("check_relation_id", "sub_check_id", "relation_id"))),
        "source": _identity_text(location.get("source")),
        "page": _identity_int(_first_value(location, _PAGE_KEYS)),
        "pages": _finding_pages(location),
        "table": _identity_text(_first_value(
            location, ("table_instance_id", "table_code", "table"))),
        "table_instance": _identity_text(location.get("table_instance_id")),
        "row": _identity_text(_first_value(
            location, ("row_key", "row", "subject_code", "code", "subject"))),
        "column": _column_identity(_first_value(location, ("column", "field"))),
        "year": _identity_int(_first_value(location, ("year", "fiscal_year"))),
        "stage": _identity_text(_first_value(location, ("stage", "budget_stage"))),
        "subject": _identity_text(_first_value(
            location, ("org_name", "subject_org", "organization", "org"))),
        "file_hash": _identity_text(_first_value(
            location, ("file_hash", "source_file_hash"))),
        "fact_version": _identity_text(_first_value(
            location, ("fact_version", "fact_context_version_token", "version_token"))),
    }


#: 义务显式声明后，问题侧必须有值的身份维度（缺一即 fail-closed）。
_REQUIRED_WHEN_DECLARED = ("year", "stage", "subject", "file_hash", "fact_version")


def _identity_has_anchor(identity: Mapping[str, Any]) -> bool:
    """是否足以定位到具体检查位置。

    单页不算强身份：同一页可能有多张表、多行、多个关系。至少要有关系 ID，
    或“行 + (页/表/表实例)”，或“表实例 + 列”，才允许关闭金额义务。
    """
    if identity.get("relation"):
        return True
    if identity.get("row") and (identity.get("page") or identity.get("table")
                                or identity.get("table_instance")):
        return True
    if identity.get("table_instance") and identity.get("column"):
        return True
    return False


def _identity_agrees(operand: Mapping[str, Any], finding: Mapping[str, Any]) -> bool:
    """缺数位置与问题位置是否指向同一个检查关系/同一处事实。

    规则：
    - 双方都声明关系 ID 时，关系必须相等；相等也不豁免显式年度/主体/版本冲突；
    - 问题声明了关系、义务没有：无法证明同关系，按不覆盖处理；
    - 义务声明了关系、问题没有：问题缺少身份，按不覆盖处理；
    - 义务显式声明的年度/阶段/主体/文件版本，问题侧必须同样声明且相等；
    - 双方都没有关系 ID 时，逐维比较双方都声明的维度；义务声明的维度在
      问题侧缺失即视为身份不足（fail-closed），不允许用文字相似补位；
    - 至少命中一个强身份维度（关系 / 行+页表 / 表实例+列）才算同一位置。
    """
    operand_relation = str(operand.get("relation") or "")
    finding_relation = str(finding.get("relation") or "")
    if operand_relation or finding_relation:
        if not operand_relation or not finding_relation:
            return False
        if operand_relation != finding_relation:
            return False
        # 关系相同不代表同一材料版本/同一主体：显式声明必须一致。
        for key in _REQUIRED_WHEN_DECLARED:
            left, right = operand.get(key), finding.get(key)
            if left in (None, "") and right in (None, ""):
                continue
            if left in (None, "") or right in (None, ""):
                if left not in (None, ""):
                    return False
                continue
            if left != right:
                return False
        return True

    strong = False
    operand_page = operand.get("page")
    if operand_page:
        pages = set(finding.get("pages") or ())
        declared = finding.get("page")
        if declared:
            pages.add(declared)
        if not pages or operand_page not in pages:
            return False
        strong = True

    operand_table = str(operand.get("table") or "")
    finding_table = str(finding.get("table") or "")
    if operand_table:
        if not finding_table or operand_table != finding_table:
            return False
        strong = True

    operand_table_instance = str(operand.get("table_instance") or "")
    finding_table_instance = str(finding.get("table_instance") or "")
    if operand_table_instance:
        if not finding_table_instance or operand_table_instance != finding_table_instance:
            return False
        strong = True

    operand_row = str(operand.get("row") or "")
    finding_row = str(finding.get("row") or "")
    if operand_row:
        if not finding_row or operand_row != finding_row:
            return False
        strong = True

    operand_column = str(operand.get("column") or "")
    finding_column = str(finding.get("column") or "")
    if operand_column:
        if not finding_column or operand_column != finding_column:
            return False
        strong = True

    for key in _REQUIRED_WHEN_DECLARED:
        left, right = operand.get(key), finding.get(key)
        if left in (None, ""):
            continue
        if right in (None, "") or left != right:
            return False

    return strong and _identity_has_anchor(operand)


def _finding_matches_operand(
    finding: Dict[str, Any],
    operand: Mapping[str, Any],
    terms: Set[str],
    *,
    requires_amount: bool,
) -> bool:
    """一条 finding 是否证明某个具体缺数位置已完成。"""
    if requires_amount and _finding_amount_kind(finding) != "amount":
        return False
    text = _finding_text(finding)
    if not any(term in text for term in terms):
        return False
    return _identity_agrees(_operand_identity(operand), _finding_identity(finding))

def obligation_covered_by_finding(
    item: Any,
    result_row: Any,
    visible_issue_ids: Optional[Iterable[str]] = None,
) -> bool:
    """该义务是否只是同一条正式问题的“检查做不下去”副本。

    ``visible_issue_ids`` 为当前问题集合里可见的 issue id；被 legacy 忽略的
    finding 不在其中，不能用来关闭义务。传 ``None`` 表示不校验可见性
    （仅供纯函数测试）。

    金额义务带缺数位置时按身份逐处核对（关系 ID / 页 / 表实例 / 行 / 列 /
    年度 / 主体）：同一处必须由同关系或同位置的正式金额问题证明；任何一处
    没有证明，整条义务保持未完成。历史结果没有缺数位置时沿用冻结短语匹配，
    但同样不能把名称/单位类问题当作金额检查完成。
    """
    if str(getattr(item, "status", "") or "") != "insufficient_data":
        return False
    checkers = {str(code).strip() for code in (getattr(item, "checkers", None) or [])}
    # U-7：义务含金额子检查时，只有金额族问题才可能覆盖；名称/单位类问题
    # 只合并展示，不得关闭、覆盖或隐藏未完成的金额检查。
    amount_checkers = checkers & _AMOUNT_CHECKERS
    requires_amount = bool(amount_checkers) or _requires_amount_evidence(item)
    # DH-03a：新结果读冻结短语列表；只有历史结果没有该字段时才回退解析 detail。
    frozen_terms = getattr(item, "legacy_coverage_terms", None)
    if frozen_terms is None:
        terms = distinctive_terms(str(getattr(item, "detail", None) or ""))
    else:
        terms = {str(term) for term in frozen_terms if str(term)}
    if not checkers or not terms:
        return False

    visible = set(visible_issue_ids) if visible_issue_ids is not None else None
    candidates: List[Dict[str, Any]] = []
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if visible is not None and issue_id and issue_id not in visible:
            continue
        tags = {str(tag).strip() for tag in _json_list(finding.get("tags"))}
        rule_id = str(finding.get("rule_id") or finding.get("check") or "").strip()
        if not ((checkers & tags) | ({rule_id} if rule_id in checkers else set())):
            continue
        candidates.append(finding)

    operands = [
        operand
        for operand in (getattr(item, "missing_operands", None) or [])
        if isinstance(operand, dict)
    ]
    if requires_amount and not operands:
        # R-01：金额义务没有缺数位置就没有可核验的操作数身份。旧结果/缺字段
        # 一律保持未完成；不得回退到“同规则+同名短语”关闭金额检查。
        return False
    if requires_amount and operands:
        # 缺数位置携带身份：逐处证明。旧结果缺少完整身份时保持未完成，
        # 但仍可正常展示/读回，不因字段缺失而报错或降级成“已完成”。
        identities = [_operand_identity(operand) for operand in operands]
        if not any(_identity_has_anchor(identity) for identity in identities):
            return False
        used: Set[int] = set()
        for operand, identity in zip(operands, identities, strict=True):
            if not _identity_has_anchor(identity):
                # 有位置但身份不足以定位：不能凭文字相似补位。
                return False
            match = next(
                (
                    index
                    for index, finding in enumerate(candidates)
                    if index not in used
                    and _finding_matches_operand(
                        finding, operand, terms, requires_amount=True
                    )
                ),
                None,
            )
            if match is None:
                return False
            used.add(match)
        return True

    for finding in candidates:
        if requires_amount and _finding_amount_kind(finding) != "amount":
            # 名称/单位/结构类问题不覆盖金额子检查；子检查性质不明时同样
            # fail-closed，不能仅凭规则编号族把未完成金额检查关掉。
            continue
        text = _finding_text(finding)
        if any(term in text for term in terms):
            return True
    return False


# ---------------------------------------------------------------------------
# 连带项：整张表缺失 / 整张表为空表时，依赖这张表金额的核对一律做不下去。
# 根问题（“缺失表：X”“X 为空表但缺少说明”）已作为正式问题列出；这些核对只是
# 它的后果，不再单独进入“必须处理”，而是挂在根问题下展示（2026-11-08 用户确认）。
# 安全边界：只认“义务的每一条未完成原因都指向已报缺失/空表的那张表”；夹带
# 其他原因（主体冲突、科目名不符等）的义务照常保留。
# ---------------------------------------------------------------------------

#: 九张表的文字别名（义务 detail 与 finding 文本都用它识别表身份）。按“长名在前”
#: 逐段扫描：一段文字被长表名吃掉后，其中的“支出决算表”不会再被认成支出决算表。
_TABLE_TOKENS = (
    ("FIN_08", r"政府性基金预算财政拨款收入支出决算表"),
    ("FIN_09", r"国有资本经营预算财政拨款收入支出决算表"),
    ("FIN_07", r"(?:一般公共预算)?财政拨款三公经费支出决算表"),
    ("FIN_06", r"(?:一般公共预算财政拨款)?基本支出决算表"),
    ("FIN_05", r"一般公共预算财政拨款支出决算表"),
    ("FIN_04", r"财政拨款收入支出决算总表"),
    ("FIN_01", r"收入支出决算总表"),
    ("FIN_02", r"收入决算表"),
    ("FIN_03", r"支出决算表"),
    ("FIN_07", r"三公"),
)
_TABLE_TOKEN_RE = re.compile(
    "|".join(f"(?P<t{index}>{pattern})" for index, (_code, pattern) in enumerate(_TABLE_TOKENS))
    + r"|FIN_0(?P<fin>[1-9])|(?<![A-Za-z0-9])T(?P<short>[1-9])(?![0-9])"
)

#: 旧版结果的 message 前面带“十二、”这样的序号，按包含匹配。
_MISSING_TABLE_RE = re.compile(r"缺失表[:：]\s*(?P<name>[^，。；,;\s]+)")
_EMPTY_TABLE_RE = re.compile(r"(?:【(?P<bracket>[^】]+)】为空表|空表缺少说明[:：](?P<plain>.+?)为空表)")


def _normalize_table_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or ""))
    return re.sub(r"[\s\"'“”‘’]", "", text)


def _table_codes_in(text: str) -> Set[str]:
    codes: Set[str] = set()
    for match in _TABLE_TOKEN_RE.finditer(_normalize_table_name(text)):
        digit = match.group("fin") or match.group("short")
        if digit:
            codes.add(f"FIN_0{digit}")
            continue
        index = next(int(name[1:]) for name, value in match.groupdict().items()
                     if value and name.startswith("t"))
        codes.add(_TABLE_TOKENS[index][0])
    return codes


def _root_table_finding(finding: Mapping[str, Any]) -> Optional[tuple]:
    """finding 是“整张表缺失/为空表”的根问题时返回 (表代码, 类型)，否则 None。"""
    message = str(finding.get("message") or finding.get("title") or "")
    location = _json_dict(finding.get("location"))
    missing = _MISSING_TABLE_RE.search(message)
    if missing:
        name, kind = str(location.get("table") or missing.group("name")), "missing"
    else:
        empty = _EMPTY_TABLE_RE.search(message)
        if not empty:
            return None
        name, kind = str(location.get("table") or empty.group("bracket") or empty.group("plain")), "empty"
    codes = _table_codes_in(name)
    return (codes.pop(), kind) if len(codes) == 1 else None


def _depends_on_tables(item: Any) -> Set[str]:
    codes: Set[str] = set()
    for dep in getattr(item, "depends_on", None) or []:
        text = str(dep or "")
        if text.startswith("table:"):
            codes |= _table_codes_in(text.split(":", 1)[1])
    return codes


_V33227_NAME_MISMATCH_CLAUSE_RE = re.compile(
    r"科目名称差异候选(?:（(?P<level>[^）]+)）)?：正文第(?P<page>[^页]*)页写“(?P<actual>[^”]+)”"
    r"(?:（涉及项“(?P<item_name>[^”]+)”）)?"
    r"(?:；(?:同款)?表格第[^页]*页编码(?P<code>\d+)写“(?P<expected>[^”]+)”[^。]*。"
    r"名称不一致，尚未绑定金额(?:；请核对科目名称后确认，不能仅凭金额相同认定一致。)?)?"
)


def _compact_subject_text(value: Any) -> str:
    # 前导连接号只作候选匹配的噪声（价格认证中心“－机关事业单位…”实测）；
    # 不删除名称内部字符，也不用于补造金额身份。
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    return re.sub(r"^[\-－—–‐]+", "", text)


def _finding_narrative_pages(finding: Mapping[str, Any]) -> Set[int]:
    location = _json_dict(finding.get("location"))
    pages: Set[int] = set()
    for ref in location.get("table_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        role = str(ref.get("role") or "")
        page = ref.get("page")
        if "说明" in role and isinstance(page, int) and page > 0:
            pages.add(page)
    for raw in (location.get("page"), finding.get("page"), finding.get("page_number")):
        if isinstance(raw, int) and raw > 0:
            pages.add(raw)
    if pages:
        return pages
    for raw in location.get("pages") or []:
        if isinstance(raw, int) and raw > 0:
            pages.add(raw)
    return pages


def _finding_is_v33227(finding: Mapping[str, Any]) -> bool:
    rule_id = str(finding.get("rule_id") or finding.get("check") or "").strip()
    tags = {str(tag).strip() for tag in _json_list(finding.get("tags"))}
    return rule_id == "V33-227" or "V33-227" in tags


def _v33227_name_mismatch_roots(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> List[str]:
    """方案 A：OBL-TXT-T5 仅因同款科目名称差异中断、且每处均已被正式 V33-227 报出时挂入其下方。

    安全边界（任一不满足即返回空列表，退回独立补核以免漏报）：
    - reason_codes 必须严格等于 ``{"name_mismatch_confirmed"}``，且无 ``blocked_by``；
    - missing_operands 必须非空，且每处缺数与 detail 中每条差异说明均需对应到可见正式 V33-227。
    """
    if str(getattr(item, "obligation_id", "") or "") != "OBL-TXT-T5":
        return []
    if getattr(item, "blocked_by", None):
        return []
    reason_codes = {
        str(code).strip()
        for code in (getattr(item, "reason_codes", None) or [])
        if str(code).strip()
    }
    if reason_codes != {"name_mismatch_confirmed"}:
        return []
    operands = [
        operand
        for operand in (getattr(item, "missing_operands", None) or [])
        if isinstance(operand, Mapping)
    ]
    if not operands:
        return []

    v227_entries: List[tuple] = []
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        if not _finding_is_v33227(finding):
            continue
        loc = _json_dict(finding.get("location"))
        actual_name = _compact_subject_text(loc.get("actual_name"))
        if not actual_name:
            continue
        code = re.sub(r"\D+", "", str(loc.get("code") or loc.get("row") or ""))
        v227_entries.append((issue_id, actual_name, code, _finding_narrative_pages(finding)))
    if not v227_entries:
        return []

    reasons = str(getattr(item, "detail", None) or "").split("结构：", 1)[0].strip()
    clauses = [match.groupdict() for match in _V33227_NAME_MISMATCH_CLAUSE_RE.finditer(reasons)]
    if clauses:
        leftover = _V33227_NAME_MISMATCH_CLAUSE_RE.sub("", reasons).strip(" ；;。")
        if leftover:
            return []
    elif reasons and any(
        token in reasons
        for token in ("找不到", "缺少", "缺失", "主体", "多个科目", "显式决算金额", "未提取", "未识别", "跨页", "差额", "不一致：")
    ):
        return []

    clause_codes: Dict[tuple, Set[str]] = {}
    clause_codes_by_name: Dict[str, Set[str]] = {}
    item_clause_hits: Dict[tuple, List[str]] = {}
    item_clause_hits_by_name: Dict[str, List[str]] = {}
    matched_ids: List[str] = []
    for clause in clauses:
        c_actual = _compact_subject_text(clause.get("actual"))
        c_code = str(clause.get("code") or "").strip()
        raw_page = str(clause.get("page") or "").strip()
        c_page = int(raw_page) if raw_page.isdigit() and int(raw_page) > 0 else None
        if not c_actual:
            return []
        if not c_code:
            known_codes = clause_codes.get((c_actual, c_page))
            if not known_codes or len(known_codes) != 1:
                return []
            c_code = next(iter(known_codes))
        clause_codes.setdefault((c_actual, c_page), set()).add(c_code)
        clause_codes_by_name.setdefault(c_actual, set()).add(c_code)
        clause_hits = [
            issue_id
            for issue_id, actual_name, code, pages in v227_entries
            if actual_name == c_actual
            and (not code or code == c_code)
            and (c_page is None or not pages or c_page in pages)
        ]
        if not clause_hits:
            return []
        matched_ids.extend(clause_hits)
        c_item = _compact_subject_text(clause.get("item_name"))
        if c_item:
            item_clause_hits.setdefault((c_item, c_page), []).extend(clause_hits)
            item_clause_hits_by_name.setdefault(c_item, []).extend(clause_hits)

    for operand in operands:
        row_key = _compact_subject_text(operand.get("row_key"))
        if not row_key:
            return []
        raw_page = operand.get("page")
        op_page = int(raw_page) if isinstance(raw_page, int) and raw_page > 0 else None
        expected_codes = clause_codes.get((row_key, op_page))
        if expected_codes is None and len(clause_codes_by_name.get(row_key, set())) == 1:
            expected_codes = clause_codes_by_name[row_key]
        hits = [
            issue_id
            for issue_id, actual_name, code, pages in v227_entries
            if actual_name == row_key
            and (op_page is None or not pages or op_page in pages)
            and (not expected_codes or not code or code in expected_codes)
        ]
        if not hits and not expected_codes:
            hits = list(item_clause_hits.get((row_key, op_page), []))
            if not hits and op_page is None:
                hits = list(item_clause_hits_by_name.get(row_key, []))
        if not hits:
            return []
        matched_ids.extend(hits)
    return list(dict.fromkeys(matched_ids))


def _missing_section_cascade_root(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> Optional[str]:
    """三公说明章节或三公表空表无说明已作为正式问题报出时，下游同源缺章义务挂入其下方。"""
    codes = {str(c).strip() for c in (getattr(item, "reason_codes", None) or []) if str(c).strip()}
    if codes and not codes <= {"text_statement_absent"}:
        return None
    if getattr(item, "blocked_by", None):
        return None
    detail = str(getattr(item, "detail", None) or "").split("结构：", 1)[0].strip()
    if not detail:
        return None
    clauses = [clause.strip() for clause in detail.split("；") if clause.strip()]
    if not clauses or not all(
        "三公经费" in clause and ("未找到" in clause or "未定位到" in clause) and ("说明" in clause or "原文" in clause)
        for clause in clauses
    ):
        return None
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        msg = str(finding.get("message") or finding.get("title") or "")
        if ("三公" in msg and ("为空表" in msg or "缺少" in msg) and "说明" in msg):
            return issue_id
    return None


TABLE_READING_CASCADE_CODES = frozenset(
    {
        "table_mapping_incomplete",
        "column_identity_unconfirmed",
        "cell_low_confidence",
        "cell_unbound",
        "row_level_unresolved",
        "cell_blank_semantics_unknown",
        "operand_missing",
        "operand_ambiguous",
        "column_width_drift",
    }
)
_TABLE_READING_STRICT_CODES = TABLE_READING_CASCADE_CODES - frozenset(
    {"operand_missing", "operand_ambiguous"}
)
_READING_ITEM_TYPES = frozenset(
    {
        "table_mapping_incomplete",
        "source_cell_unbound",
        "invalid_number_format",
        "low_confidence_table",
        "table_reading_gap",
    }
)


def _page_table_map(structured: Mapping[str, Any]) -> Dict[int, Set[str]]:
    by_page: Dict[int, Set[str]] = {}
    for entry in (
        structured.get("recognized_tables"),
        ((structured.get("reading_import") or {}).get("reading_quality") or {}).get("tables")
        if isinstance(structured.get("reading_import"), Mapping)
        else None,
    ):
        if not isinstance(entry, Sequence) or isinstance(entry, (str, bytes)):
            continue
        for tbl in entry:
            if not isinstance(tbl, Mapping):
                continue
            codes = _table_codes_in(str(tbl.get("table_code") or tbl.get("code") or ""))
            if not codes:
                continue
            pages: List[int] = []
            for p_key in ("page", "page_number"):
                p_val = tbl.get(p_key)
                if isinstance(p_val, int) and p_val > 0:
                    pages.append(p_val)
            for p_list_key in ("pages", "page_numbers"):
                p_list = tbl.get(p_list_key)
                if isinstance(p_list, Sequence) and not isinstance(p_list, (str, bytes)):
                    pages.extend(p for p in p_list if isinstance(p, int) and p > 0)
            for p in pages:
                by_page.setdefault(p, set()).update(codes)
    return by_page


def _broken_table_roots(
    result_row: Any,
    structured_ingest: Any,
    visible: Optional[Set[str]],
) -> Dict[str, str]:
    """收集已被正式列为待审/错误的问题表根因：表代码(FIN_0x) -> 根问题 issue_id。"""
    roots: Dict[str, str] = {}
    structured = _json_dict(structured_ingest)
    if not structured and isinstance(result_row, Mapping):
        structured = _json_dict(result_row.get("structured_ingest"))
    page_map = _page_table_map(structured) if structured else {}
    job_uuid = str((result_row or {}).get("job_id") or "__quality_gate__") if isinstance(result_row, Mapping) else "__quality_gate__"

    for index, item in enumerate(structured.get("review_items") or []):
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type not in _READING_ITEM_TYPES:
            continue
        issue_id = str(item.get("id") or "").strip()
        raw_page = item.get("page", item.get("page_number"))
        page = int(raw_page) if isinstance(raw_page, int) and raw_page > 0 else None
        raw_table = str(item.get("table_code") or "").strip()
        if not issue_id:
            page_token = str(page) if page is not None else "unlocated"
            issue_id = f"{job_uuid}:structured-review:{item_type}:{raw_table or 'document'}:{page_token}:{index + 1}"
        if visible is not None and issue_id not in visible:
            continue
        codes = _table_codes_in(raw_table)
        if not codes:
            candidate_pages: List[int] = []
            if page is not None:
                candidate_pages.append(page)
            if isinstance(item.get("page_numbers"), Sequence):
                candidate_pages.extend(p for p in item["page_numbers"] if isinstance(p, int) and p > 0)
            mapped: Set[str] = set()
            for p in candidate_pages:
                mapped.update(page_map.get(p, ()))
            if len(mapped) == 1:
                codes = mapped
        for code in sorted(codes):
            roots.setdefault(code, issue_id)

    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        location = _json_dict(finding.get("location"))
        is_column_shift = bool(location.get("column_shift")) or str(
            finding.get("check_relation_id") or ""
        ).endswith(":column_shift")
        if not is_column_shift:
            continue
        codes = _table_codes_in(str(location.get("table_code") or location.get("table") or ""))
        for code in sorted(codes):
            roots.setdefault(code, issue_id)
    return roots


def _table_reading_cascade_root(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
    structured_ingest: Any = None,
) -> Optional[str]:
    """若阻塞义务仅由已上浮的某张坏表（读取/映射/低置信/整列错位）导致，返回其根问题 id。"""
    broken = _broken_table_roots(result_row, structured_ingest, visible)
    if not broken:
        return None
    reasons = {
        str(code).strip()
        for code in (getattr(item, "reason_codes", None) or ())
        if str(code).strip()
    }
    if not reasons or not (reasons <= TABLE_READING_CASCADE_CODES):
        return None

    cited_from_struct: Set[str] = set()
    for blocker in getattr(item, "blocked_by", None) or ():
        if isinstance(blocker, Mapping):
            ref = str(blocker.get("ref") or "").strip()
            if ref and not ref.startswith("规则级:"):
                cited_from_struct.update(_table_codes_in(ref))
    for bucket in (getattr(item, "missing_operands", None) or (), getattr(item, "candidates", None) or ()):
        for entry in bucket:
            if isinstance(entry, Mapping):
                cited_from_struct.update(_table_codes_in(str(entry.get("table_code") or "")))

    if cited_from_struct:
        if not (cited_from_struct <= set(broken)):
            return None
        return broken[sorted(cited_from_struct)[0]]

    detail = str(getattr(item, "detail", None) or "").split("结构：", 1)[0].strip()
    cited_from_detail = _table_codes_in(detail)
    if cited_from_detail:
        if not (cited_from_detail <= set(broken)):
            return None
        return broken[sorted(cited_from_detail)[0]]

    if not (reasons <= _TABLE_READING_STRICT_CODES):
        return None
    declared = _depends_on_tables(item)
    overlap = sorted(declared & set(broken))
    if not overlap:
        return None
    return broken[overlap[0]]


_V33251_KIND_RE = re.compile(r"V33-251:(?P<kind>budget|final)")


def _v33251_defect_cascade_roots(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> List[str]:
    """V33-251 已交付具体披露/加总问题，承接同口径未完成分项关系。"""
    texts = [str(getattr(item, "detail", None) or "")]
    texts.extend(
        str((blocker or {}).get("ref") or "")
        for blocker in (getattr(item, "blocked_by", None) or [])
        if isinstance(blocker, Mapping)
    )
    kinds = {
        match.group("kind")
        for text in texts
        for match in _V33251_KIND_RE.finditer(text)
    }
    if not kinds:
        return []
    checklist = {"budget": "C-11", "final": "C-12"}
    roots: List[str] = []
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        rule_id = str(finding.get("rule_id") or finding.get("check") or "")
        related = {str(value) for value in finding.get("related_rule_ids") or []}
        if rule_id != "V33-251" and "V33-251" not in related:
            continue
        location = _json_dict(finding.get("location"))
        item_code = str(location.get("checklist_item") or "")
        if any(checklist[kind] == item_code for kind in kinds):
            roots.append(issue_id)
    return list(dict.fromkeys(roots))


def _subject_name_cascade_roots(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> List[str]:
    """已正式报出的单位名称差异，承接同页“主体冲突”类未完成关系。

    典型：正文大标题“上海市生态环境局局”由 CMM-017 报出，V33-102 的正文
    金额关系因主体冲突中断。只认已由原件确认的正式名称问题，且页码必须
    与义务阻断位置一致（或全篇仅此一条名称问题），不按相似名称猜。
    """
    detail = str(getattr(item, "detail", None) or "")
    codes = {
        str(code).strip()
        for code in (getattr(item, "reason_codes", None) or [])
        if str(code).strip()
    }
    if "subject_conflict" not in codes and "单位名称" not in detail and "主体" not in detail:
        return []
    pages = {
        int(match.group(1)) for match in re.finditer(r"第(\d+)页", detail)
    }
    candidates: List[tuple] = []
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        message = str(finding.get("message") or finding.get("title") or "")
        if not (str(finding.get("rule_id") or "") == "CMM-017"
                or ("单位名称" in message and ("不一致" in message or "多字" in message))):
            continue
        location = _json_dict(finding.get("location"))
        finding_pages = _finding_pages(location)
        candidates.append((issue_id, finding_pages))
    if not candidates:
        return []
    matched = [
        issue_id for issue_id, finding_pages in candidates
        if not pages or not finding_pages or (pages & finding_pages)
    ]
    if matched:
        return list(dict.fromkeys(matched))
    return [candidates[0][0]] if len(candidates) == 1 else []


_UNIT_DEFECT_TEXT_RE = re.compile(
    r"(?:万万元|亿亿元|元元|万元万|单位重复|重复单位|单位书写有误|单位口径错误)"
)


def _unit_defect_cascade_roots(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> List[str]:
    """已报“重复单位/单位书写有误”的正式问题，承接受其影响的未完成关系。

    只认已由原件证据确认的正式问题；义务自身 detail 必须提到单位异常，
    或 blocked_by 指向同一单位缺陷规则。不能仅凭页码把其他金额问题当根因。
    """
    detail = str(getattr(item, "detail", None) or "")
    blocked_refs = " ".join(
        str((blocker or {}).get("ref") or "")
        for blocker in (getattr(item, "blocked_by", None) or [])
        if isinstance(blocker, Mapping)
    )
    if not (_UNIT_DEFECT_TEXT_RE.search(detail) or "V33-251" in blocked_refs
            or "V33-222" in blocked_refs or "CMM-011" in blocked_refs):
        return []
    pages: Set[int] = set()
    for operand in getattr(item, "missing_operands", None) or []:
        if isinstance(operand, Mapping):
            page = operand.get("page")
            if isinstance(page, int) and not isinstance(page, bool) and page > 0:
                pages.add(page)
    for match in re.finditer(r"第(\d+)页", detail):
        pages.add(int(match.group(1)))
    roots: List[str] = []
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        message = str(finding.get("message") or finding.get("title") or "")
        location = _json_dict(finding.get("location"))
        defect_type = str(location.get("defect_type") or "")
        related = set(str(value) for value in [
            finding.get("rule_id"), finding.get("check"),
            *(finding.get("related_rule_ids") or []),
            *(location.get("related_rule_ids") or []),
        ] if value)
        if not (defect_type == "amount_unit_duplicated"
                or _UNIT_DEFECT_TEXT_RE.search(message)):
            continue
        finding_pages = _finding_pages(location) | {
            finding.get("page") for _ in (0,) if isinstance(finding.get("page"), int)
        }
        if pages and finding_pages and not (pages & finding_pages):
            continue
        if blocked_refs and related and not ({ref for ref in ("V33-251", "V33-222", "CMM-011")
                                             if ref in blocked_refs} & related):
            # blocked_by 明确指向某单位规则时，根问题必须包含同族规则身份。
            if pages and not (pages & finding_pages):
                continue
        roots.append(issue_id)
    return list(dict.fromkeys(roots))


def cascade_root_issue_ids(
    item: Any,
    result_row: Any,
    visible_issue_ids: Optional[Iterable[str]] = None,
    *,
    structured_ingest: Any = None,
) -> List[str]:
    """返回可承接该未完成义务的根问题 issue id 列表（方案：缺表/空表、缺三公说明、表格读取异常或同源名称错误）。"""
    if str(getattr(item, "status", "") or "") != "insufficient_data":
        return []
    visible = set(visible_issue_ids) if visible_issue_ids is not None else None
    table_root = _missing_or_empty_table_cascade_root(item, result_row, visible)
    if table_root is not None:
        return [table_root]
    section_root = _missing_section_cascade_root(item, result_row, visible)
    if section_root is not None:
        return [section_root]
    reading_root = _table_reading_cascade_root(
        item, result_row, visible, structured_ingest=structured_ingest
    )
    if reading_root is not None:
        return [reading_root]
    v33251_roots = _v33251_defect_cascade_roots(item, result_row, visible)
    if v33251_roots:
        return v33251_roots
    name_roots = _subject_name_cascade_roots(item, result_row, visible)
    if name_roots:
        return name_roots
    unit_roots = _unit_defect_cascade_roots(item, result_row, visible)
    if unit_roots:
        return unit_roots
    return _v33227_name_mismatch_roots(item, result_row, visible)


def cascade_root_issue_id(
    item: Any,
    result_row: Any,
    visible_issue_ids: Optional[Iterable[str]] = None,
    *,
    structured_ingest: Any = None,
) -> Optional[str]:
    """该义务若只是“某张表缺失/为空表/读取异常”或已检出同款科目名称错误的连带后果，返回首个根问题 id。"""
    roots = cascade_root_issue_ids(
        item,
        result_row,
        visible_issue_ids=visible_issue_ids,
        structured_ingest=structured_ingest,
    )
    return roots[0] if roots else None


def _missing_or_empty_table_cascade_root(
    item: Any,
    result_row: Any,
    visible: Optional[Set[str]],
) -> Optional[str]:
    roots: Dict[str, str] = {}
    for finding in _current_rule_findings(result_row):
        if not isinstance(finding, dict) or not is_formal_finding(finding):
            continue
        issue_id = str(finding.get("id") or "").strip()
        if not issue_id or (visible is not None and issue_id not in visible):
            continue
        root = _root_table_finding(finding)
        if root is not None:
            roots.setdefault(root[0], issue_id)
    if not roots:
        return None
    reported = set(roots)
    declared = _depends_on_tables(item)
    # “结构：……”是引擎追加在末尾的结构化卡点注记（内部也用“；”分隔），不是原因本身。
    reasons = str(getattr(item, "detail", None) or "").split("结构：", 1)[0]
    clauses = [clause.strip() for clause in reasons.split("；") if clause.strip()]
    if not clauses:
        return None
    cited_all = set().union(*(_table_codes_in(clause) for clause in clauses))
    # 声明了依赖表的，必须依赖已报的表；旧目录没声明依赖的，以原因里点名的表为准。
    dependent = (declared if declared else cited_all) & reported
    if not dependent:
        return None
    for clause in clauses:
        if not (_table_codes_in(clause) & reported):
            return None
    for blocker in getattr(item, "blocked_by", None) or []:
        ref = str((blocker or {}).get("ref") or "") if isinstance(blocker, Mapping) else ""
        if ref.startswith("规则级:"):
            continue
        tables = _table_codes_in(ref)
        if not tables or not tables <= reported:
            return None
    cited = cited_all & reported
    return roots[sorted(cited & dependent or cited)[0]]
