from __future__ import annotations

import re
from typing import Optional


_LIST_PREFIX_RE = re.compile(r"^\s*[一二三四五六七八九十\d]+[、.)]\s*")

_RULE_TITLE_OVERRIDES = {
    "BUD-109": "预算编制说明功能分类类款项名称与T5不一致",
    "V33-227": "说明5功能分类类款项名称与T5不一致",
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
    if normalized_rule:
        return f"请按 {normalized_rule} 规则复核原表与说明文字{page_hint}，必要时修订披露口径。"
    return f"请复核原表与说明文字{page_hint}，确认口径与数值一致。"
