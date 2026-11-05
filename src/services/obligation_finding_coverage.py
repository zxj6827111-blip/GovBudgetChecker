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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

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
    for finding in _json_list(_json_dict(result_row).get("rule_findings")):
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
        for operand, identity in zip(operands, identities):
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
