"""同一项级条目内的金额单位依据；不按表值或加总结果反推单位。"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NUMBER = r"[+-]?\d[\d,]*(?:\.\d+)?"
_HEADER = re.compile(rf"[（(]项[）)]\s*(?:支出(?:数)?(?:为|是)?)?\s*"
                     rf"(?P<amount>{_NUMBER})\s*(?P<unit>亿元|万元|元)")
_FINAL = re.compile(rf"(?:支出决算|决算支出|决算数)(?:数)?(?:为|是)?\s*[:：]?\s*"
                    rf"(?P<amount>{_NUMBER})\s*(?P<unit>亿元|万元|元)")


def same_item_final_unit(text: str, start: int, end: int, raw: str, claim_start: int):
    """只认本条目项级标题中的同一决算金额；返回原文区间和单位。

    年初预算、价格、别的条目及上年数字均不能给本年决算提供单位。
    同条目另有显式决算单位冲突时仍保持未知，不为凑平选择单位。
    """
    try:
        value = Decimal(raw.replace(',', ''))
    except (InvalidOperation, AttributeError):
        return None
    segment = text[start:end]
    head_end = re.search(r"主要用于|[。；;]", segment)
    header_end = start + head_end.start() if head_end else end
    headers = list(_HEADER.finditer(text, start, min(header_end, claim_start)))
    if len(headers) != 1:
        return None
    header = headers[0]
    if Decimal(header['amount'].replace(',', '')) != value:
        return None
    # 出现不同年度/阶段时不跨越继承；由上层保留具体取数不足。
    if re.search(r'上年|去年|以前年度|20\d{2}(?:年度|年)|调整后预算|全年预算',
                 text[header.end():claim_start]):
        return None
    explicit = list(_FINAL.finditer(text, start, end))
    if any(m['unit'] != header['unit'] for m in explicit):
        return None
    return {'unit': header['unit'], 'start': header.start(), 'end': header.end(),
            'basis': 'same_item_header_same_final_amount', 'raw_unit_missing': True}
