from __future__ import annotations

import re
from typing import Optional


_LIST_PREFIX_RE = re.compile(r"^\s*[一二三四五六七八九十\d]+[、.)]\s*")

_RULE_TITLE_OVERRIDES = {
    "BUD-109": "预算编制说明功能分类类款项名称与T5不一致",
    "V33-227": "说明5功能分类类款项名称与T5不一致",
    "V33-CROSS-SAN-GONG-ECON": "基本支出三公分项超过三公经费表同项决算数",
    "V33-TXT-FUND-DETAIL": "基金/国资决算表与对应说明同一功能分类项金额不一致",
    "V33-NARRATIVE-INDICATOR-REPEAT": "文内同一指标重复披露金额不一致",
}


def strip_list_prefix(message: str) -> str:
    return _LIST_PREFIX_RE.sub("", message or "").strip()


def infer_rule_title(rule_code: str, message: str) -> str:
    normalized_rule = str(rule_code or "").strip().upper()
    override = _RULE_TITLE_OVERRIDES.get(normalized_rule)
    if override:
        return override

    clean = strip_list_prefix(message)
    if not clean:
        return rule_code or "规则命中"
    if "（" in clean:
        return clean.split("（", 1)[0].strip()[:80] or clean[:80]
    if ":" in clean:
        return clean.split(":", 1)[0].strip()[:80] or clean[:80]
    if "：" in clean:
        return clean.split("：", 1)[0].strip()[:80] or clean[:80]
    return clean[:80]


def default_rule_suggestion(rule_code: str, page: Optional[int]) -> str:
    normalized_rule = str(rule_code or "").strip().upper()
    page_hint = f"（第{page}页）" if page else ""

    if normalized_rule == "BUD-109":
        return (
            "请以 T5 一般公共预算支出功能分类预算表为准，核对预算编制说明中同编码的类/款/项名称"
            f"{page_hint}；如说明沿用旧口径名称，请统一改为表格口径并同步全文。"
        )
    if normalized_rule == "V33-227":
        return (
            "请以 T5 一般公共预算财政拨款支出决算表为准，核对说明5中同编码的类/款/项名称"
            f"{page_hint}；如说明沿用旧口径名称，请统一改为表格口径并同步全文。"
        )
    if normalized_rule == "CMM-001":
        return (
            f"请逐项核对“三公”表与“其他相关情况说明”金额口径{page_hint}，"
            "尤其确认公务用车运行费是否一致，再统一正文与表格。"
        )
    if normalized_rule == "V33-TXT-FUND-DETAIL":
        return (
            "请对照《政府性基金预算财政拨款收入支出决算表》/《国有资本经营预算财政"
            f"拨款收入支出决算表》与对应情况说明{page_hint}：以表内同一功能分类项"
            "（类/款/项编码与名称逐级核对）的本年支出决算数为准，复核说明中该业务项"
            "金额及说明自述收支总额，再统一表与说明。"
        )
    if normalized_rule == "V33-NARRATIVE-INDICATOR-REPEAT":
        return (
            "请对照两处披露原文核对同一指标的本年支出决算口径：先确认两处是否"
            "同一业务项、同一期间、同一单位（不得拿预算数/上年数/增减额互比），"
            "再翻底稿确认正确金额并同步修改两处披露"
            "（系统只能确认两处不能同时成立，不能自动判断哪一处正确）。"
        )
    if normalized_rule == "V33-CROSS-SAN-GONG-ECON":
        return (
            f"请对照《财政拨款“三公”经费支出决算表》与《一般公共预算财政拨款基本支出决算表》"
            f"同一业务项{page_hint}：基本支出经济分类金额不应大于三公经费决算数，"
            "两表逐项核对后判断是基本支出经济分类行金额有误，还是三公表分项/合计漏计"
            "（不得只改一侧）。"
        )
    if normalized_rule:
        return f"请按 {normalized_rule} 规则复核原表与说明文字{page_hint}，必要时修订披露口径。"
    return f"请复核原表与说明文字{page_hint}，确认口径与数值一致。"
