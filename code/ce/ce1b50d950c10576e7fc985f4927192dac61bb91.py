"""有直接文字与数字证据的决算说明质量提示。"""
import re
from decimal import Decimal


def check_quality(rule, doc):
    from .rules_v33 import _txt_fund_merged_pages, _txt_fund_page_for, _nar_repeat_sections
    merged, offsets = _txt_fund_merged_pages(doc)
    issues = []
    for title, start, end in _nar_repeat_sections(merged, doc, offsets):
        if "一般公共预算" not in title or "基本支出" in title:
            continue
        scope = merged[start:end]
        # Only a stated cause that consists entirely of a restated result is
        # flagged. Other short or qualitative causes are not judged here.
        for reason in re.finditer(r"(?:主要原因|原因)\s*(?:是|为|[:：])\s*(?P<body>[^。；;]{1,100})[。；;]", scope):
            body = re.sub(r"\s+", "", reason.group("body"))
            body = re.sub(r"^根据年度实际情况[,，]?", "", body)
            if not re.fullmatch(r"(?:决算数与预算数|预算数与决算数)(?:基本)?持平|(?:经费)?支出(?:有所)?(?:增加|减少)", body):
                continue
            prefix = scope[max(0, reason.start()-350):reason.start()]
            pattern = re.compile(
                r"年初预\s*算(?:\s*数)?\s*(?:为)?\s*(?P<budget>[\d,，]+(?:\.\d+)?)\s*万元"
                r"(?P<middle>[^。]{0,140})支\s*出\s*决\s*算(?:\s*数)?\s*(?:为)?\s*"
                r"(?P<actual>[\d,，]+(?:\.\d+)?)\s*万元", re.S)
            amounts = list(pattern.finditer(prefix))
            if not amounts:
                continue
            amount = amounts[-1]
            if "其中" in amount.group("middle"):
                continue
            budget = Decimal(amount.group("budget").replace(",", "").replace("，", ""))
            actual = Decimal(amount.group("actual").replace(",", "").replace("，", ""))
            if abs(actual-budget) <= Decimal(".5"):
                continue
            quote_start = max(0, reason.start()-350) + amount.start()
            quote = scope[quote_start:reason.end()]
            page = _txt_fund_page_for(offsets, start+quote_start)
            issues.append(rule._issue(
                f"预算差异原因仅重述结果：年初预算{budget}万元、决算{actual}万元；"
                f"“{reason.group('body').strip()}”未说明产生差异的具体事项，建议补充说明。",
                {"page": page, "section": title, "relation_type": "reason_restates_result",
                 "budget_amount_wan": str(budget), "actual_amount_wan": str(actual),
                 "table_refs": [{"page": page, "span": quote, "role": "预算差异及原因原文"}]},
                "medium", evidence_text=quote))

        markers = list(re.finditer(r"(?:^|\n)\s*(?P<num>\d{1,2})[、.．](?!\d)", scope))
        if len(markers) < 4:
            continue
        for previous, current in zip(markers, markers[1:], strict=False):
            first, second = int(previous.group("num")), int(current.group("num"))
            if second <= first + 1:
                continue  # New nested lists and repetitions are not missing entries.
            refs = []
            for marker in (previous, current):
                begin = marker.start("num")
                line_end = scope.find("\n", begin)
                line_end = len(scope) if line_end < 0 else line_end
                refs.append({"page": _txt_fund_page_for(offsets, start+begin),
                             "span": scope[begin:line_end], "role": "说明条目序号"})
            issues.append(rule._issue(
                f"说明条目序号由{first}跳到{second}，建议核对编号连续性；不能据此判定科目内容缺失。",
                {"page": refs[0]["page"], "pages": sorted({ref["page"] for ref in refs}),
                 "section": title, "relation_type": "item_number_gap", "table_refs": refs},
                "low", evidence_text="\n".join(ref["span"] for ref in refs)))
    return issues
