from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .amount_math import compute_dynamic_envelope
from .budget_rules import find_budget_anchors
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import (
    Document,
    Issue,
    Rule,
    find_table_anchors,
    _TXT_FUND_UNIT_TO_WAN,
    _nar_repeat_section_of,
    _nar_repeat_sections,
    _resolve_fiscal_year,
    _txt_fund_display,
    _txt_fund_merged_pages,
    _txt_fund_norm,
    _txt_fund_page_for,
)
from src.services.document_profile_resolver import resolve_report_kind_from_path
from src.utils.narration import (
    _NEW_PARAGRAPH_RE,
    merge_soft_wrapped_lines as _merge_soft_wrapped_lines_shared,
)

_AMOUNT = r"([0-9][0-9,]*\.?[0-9]*)"


def _page_texts(doc: Document) -> List[str]:
    texts = getattr(doc, "page_texts", None)
    if not isinstance(texts, list):
        return []
    return [str(item or "") for item in texts]


def _page_tables(doc: Document) -> List[Any]:
    tables = getattr(doc, "page_tables", None)
    if not isinstance(tables, list):
        return []
    return tables


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return None
    try:
        return float(text)
    except Exception:
        return None


def _infer_report_kind(doc: Document) -> str:
    """材料文种判定（收敛到唯一解析器）。

    此前这里有独立的第三份实现：整条 ``doc.path`` 做关键词匹配，并在
    识别不到时兜底 ``"final"``。整串路径匹配有问题——仓库目录名
    ``GovBudgetChecker`` 自带 "Budget"，会让任何材料被判成预算；兜底 final
    则把"不知道"伪装成"知道了"。

    现在的取值顺序（独立验收 2026-09-17 kind_disagreement 反例整改）：

    1. **消费执行入口一次解析的结论** ``doc.report_kind``——同一份材料
       在 pipeline 与通用规则里不允许得出互斥文种；
    2. 没有挂接结论时（如单测直接构造 Document），才按"文件名基名 +
       正文首页"推断；识别不到就是 ``"unknown"``，不兜底。

    本函数的唯一调用方按 ``budget`` / 其它 二分支选择锚点集合。
    """
    attached = str(getattr(doc, "report_kind", "") or "").strip().lower()
    if attached in {"budget", "final"}:
        return attached
    return resolve_report_kind_from_path(
        str(getattr(doc, "path", "") or ""),
        _page_texts(doc),
    )


def _find_first_amount(text: str, patterns: Sequence[str]) -> Optional[float]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.S)
        if not match:
            continue
        value = _to_float(match.group(1))
        if value is not None:
            return value
    return None


def _extract_three_public_narrative(text: str) -> Dict[str, Optional[float]]:
    return {
        "total": _find_first_amount(
            text,
            [
                r"\u4e09\u516c[^\n]{0,40}\u7ecf\u8d39(?:\u652f\u51fa)?"
                r"(?:\u9884\u7b97\u6570|\u51b3\u7b97\u6570|\u9884\u7b97|\u51b3\u7b97)?"
                r"(?:\u4e3a|\u662f)?\s*" + _AMOUNT + r"\u4e07\u5143",
                r"\u4e09\u516c[^\n]{0,20}\u5408\u8ba1[^\n]{0,8}" + _AMOUNT + r"\u4e07\u5143",
            ],
        ),
        "abroad": _find_first_amount(
            text,
            [r"\u56e0\u516c\u51fa\u56fd(?:\uff08\u5883\uff09|\(\u5883\)|\u5883)?\u8d39\s*" + _AMOUNT + r"\u4e07\u5143"],
        ),
        "car_total": _find_first_amount(
            text,
            [r"\u516c\u52a1\u7528\u8f66\u8d2d\u7f6e\u53ca\u8fd0\u884c\u8d39[^0-9]{0,4}" + _AMOUNT + r"\u4e07\u5143"],
        ),
        "car_buy": _find_first_amount(
            text,
            [r"\u516c\u52a1\u7528\u8f66\u8d2d\u7f6e\u8d39\s*" + _AMOUNT + r"\u4e07\u5143"],
        ),
        "car_run": _find_first_amount(
            text,
            [r"\u516c\u52a1\u7528\u8f66\u8fd0\u884c\u8d39\s*" + _AMOUNT + r"\u4e07\u5143"],
        ),
        "reception": _find_first_amount(
            text,
            [r"\u516c\u52a1\u63a5\u5f85\u8d39\s*" + _AMOUNT + r"\u4e07\u5143"],
        ),
    }


def _extract_number_row_from_line(line: str) -> Optional[List[float]]:
    raw_values = re.findall(r"[0-9][0-9,]*\.?[0-9]*", line)
    if len(raw_values) < 6:
        return None
    values: List[float] = []
    for item in raw_values:
        value = _to_float(item)
        if value is None:
            continue
        values.append(value)
    return values if len(values) >= 6 else None


def _extract_three_public_table_values_from_text(page_text: str) -> Optional[List[float]]:
    best: Optional[List[float]] = None
    best_score = -1
    for line in page_text.splitlines():
        nums = _extract_number_row_from_line(line)
        if not nums:
            continue
        score = len(nums) + (1 if "." in line else 0)
        if score > best_score:
            best = nums
            best_score = score
    return best


def _extract_three_public_table_values_from_tables(page_tables: Any) -> Optional[List[float]]:
    if not isinstance(page_tables, list):
        return None
    best: Optional[List[float]] = None
    best_score = -1
    for table in page_tables:
        if not isinstance(table, list):
            continue
        for row in table:
            if not isinstance(row, list):
                continue
            nums: List[float] = []
            for cell in row:
                value = _to_float(cell)
                if value is not None:
                    nums.append(value)
            if len(nums) < 6:
                continue
            score = len(nums)
            if score > best_score:
                best = nums
                best_score = score
    return best


def _map_three_public_values(values: Sequence[float]) -> Dict[str, Optional[float]]:
    keys = (
        "total",
        "abroad",
        "reception",
        "car_total",
        "car_buy",
        "car_run",
        "org_run",
    )
    mapped: Dict[str, Optional[float]] = {}
    for idx, key in enumerate(keys):
        mapped[key] = values[idx] if idx < len(values) else None
    return mapped


def _find_three_public_table(doc: Document) -> Tuple[Optional[int], Optional[Dict[str, Optional[float]]]]:
    texts = _page_texts(doc)
    tables = _page_tables(doc)

    for idx, page_text in enumerate(texts):
        if "\u4e09\u516c" not in page_text:
            continue
        if "\u8868" not in page_text:
            continue
        if "\u9884\u7b97" not in page_text and "\u51b3\u7b97" not in page_text:
            continue
        if "\u76ee\u5f55" in page_text[:120]:
            continue

        from_text = _extract_three_public_table_values_from_text(page_text)
        if from_text is not None:
            return idx + 1, _map_three_public_values(from_text)

        if idx < len(tables):
            from_table = _extract_three_public_table_values_from_tables(tables[idx])
            if from_table is not None:
                return idx + 1, _map_three_public_values(from_table)
    return None, None


def _close(a: Optional[float], b: Optional[float], tol: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _snippet(text: str, start: int, end: int, radius: int = 24) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


def _sentence_around(text: str, start: int, end: int) -> str:
    if not text:
        return ""
    separators = "。！？；\n"
    left = -1
    for sep in separators:
        idx = text.rfind(sep, 0, max(start, 0))
        if idx > left:
            left = idx

    right_candidates = [text.find(sep, min(end, len(text))) for sep in separators]
    right_candidates = [idx for idx in right_candidates if idx != -1]
    right = min(right_candidates) if right_candidates else len(text)

    begin = left + 1
    finish = right + 1 if right < len(text) else right
    sentence = text[begin:finish].strip()
    if not sentence:
        return _snippet(text, start, end, radius=36).strip()
    return sentence


class CMM001_ThreePublicNarrativeConsistency(Rule):
    code, severity = "CMM-001", "warn"
    desc = "\u4e09\u516c\u8868\u4e0e\u60c5\u51b5\u8bf4\u660e\u4e00\u81f4\u6027\uff08\u9884/\u51b3\u7b97\u901a\u7528\uff09"

    def apply(self, doc: Document) -> List[Issue]:
        texts = _page_texts(doc)
        if not texts:
            raise RuleDeferred(
                self.code,
                detail="未提取到正文页面",
                unresolved_reasons=["未提取到正文页面"],
            )

        full_text = "\n".join(texts)
        narrative = _extract_three_public_narrative(full_text)
        if all(value is None for value in narrative.values()):
            raise RuleDeferred(
                self.code,
                detail="未提取到三公经费情况说明数值",
                unresolved_reasons=["未提取到三公经费情况说明数值"],
            )

        table_page, table_values = _find_three_public_table(doc)
        if table_values is None:
            raise RuleDeferred(
                self.code,
                detail="未定位到三公经费表格数值",
                unresolved_reasons=["未定位到三公经费表格数值"],
            )

        issues: List[Issue] = []
        unresolved_reasons: List[str] = []
        labels = {
            "total": "三公合计",
            "abroad": "因公出国（境）费",
            "reception": "公务接待费",
            "car_total": "公务用车小计",
            "car_buy": "公务用车购置费",
            "car_run": "公务用车运行费",
        }

        for key in ("total", "abroad", "reception", "car_total", "car_buy", "car_run"):
            nar = narrative.get(key)
            tab = table_values.get(key)
            if nar is None or tab is None:
                unresolved_reasons.append(f"{labels[key]}分项说明或表格数值缺失")
                continue
            if _close(nar, tab):
                continue
            label = labels[key]
            severity = "error" if key == "car_run" else "warn"
            message = (
                f"三公表与说明不一致：{label}说明={nar:.2f}万元，表内={tab:.2f}万元"
            )
            if key == "car_run":
                message += "；建议：请统一“公务用车运行费”在表格与情况说明中的金额口径"
            issues.append(
                self._issue(
                    message,
                    {"page": table_page or 1},
                    severity,
                    evidence_text=f"narrative={nar}, table={tab}, field={label}",
                )
            )

        nar_car_total = narrative.get("car_total")
        nar_car_buy = narrative.get("car_buy")
        nar_car_run = narrative.get("car_run")
        if (
            nar_car_total is not None
            and nar_car_buy is not None
            and nar_car_run is not None
            and not _close(nar_car_total, nar_car_buy + nar_car_run)
        ):
            issues.append(
                self._issue(
                    "三公文字说明内部勾稽不一致：公车小计≠购置费+运行费",
                    {"page": table_page or 1},
                    "warn",
                    evidence_text=(
                        f"car_total={nar_car_total}, car_buy={nar_car_buy}, car_run={nar_car_run}"
                    ),
                )
            )

        if not issues and all(
            narrative.get(key) is None or table_values.get(key) is None
            for key in ("total", "abroad", "reception", "car_total", "car_buy", "car_run")
        ):
            raise RuleDeferred(
                self.code,
                detail=f"部分三公数据缺失: {', '.join(unresolved_reasons[:5])}",
                unresolved_reasons=unresolved_reasons,
            )

        return issues


_SOFT_LINE_ENDINGS = "。；：！？!?:;"


def _merge_soft_wrapped_lines(page_text: str) -> List[str]:
    """委托共享实现（src/utils/narration.py），保留旧名兼容既有调用。"""
    return _merge_soft_wrapped_lines_shared(page_text)


class CMM002_TextAnomalyRule(Rule):
    code, severity = "CMM-002", "warn"
    desc = "\u91cd\u590d\u8bcd/\u6807\u70b9\u5f02\u5e38\u68c0\u67e5\uff08\u9884/\u51b3\u7b97\u901a\u7528\uff09"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        duplicate_patterns: Sequence[Tuple[str, str]] = (
            (r"\u9884\u7b97\u9884\u7b97", "\u7591\u4f3c\u91cd\u590d\u8bcd\uff1a\u201c\u9884\u7b97\u9884\u7b97\u201d"),
            (r"\u51b3\u7b97\u51b3\u7b97", "\u7591\u4f3c\u91cd\u590d\u8bcd\uff1a\u201c\u51b3\u7b97\u51b3\u7b97\u201d"),
            (r"\u8d22\u653f\u62e8\u6b3e\u8d22\u653f\u62e8\u6b3e", "\u7591\u4f3c\u91cd\u590d\u8bcd\uff1a\u201c\u8d22\u653f\u62e8\u6b3e\u8d22\u653f\u62e8\u6b3e\u201d"),
        )
        punctuation_pattern = re.compile(r"[\u4e00-\u9fff],\s*[0-9]")
        abnormal_punctuation_patterns: Sequence[Tuple[re.Pattern[str], str]] = (
            (re.compile(r"\u3002{2,}"), "\u7591\u4f3c\u8fde\u7eed\u53e5\u53f7"),
            (re.compile(r"[\uff1b;]\s*\u3002"), "\u7591\u4f3c\u6b8b\u7f3a\u53e5\u6216\u591a\u4f59\u6807\u70b9"),
        )

        for page_idx, page_text in enumerate(_page_texts(doc), start=1):
            seen: set[Tuple[int, int]] = set()
            for pattern, message in duplicate_patterns:
                for match in re.finditer(pattern, page_text):
                    span = (match.start(), match.end())
                    if span in seen:
                        continue
                    seen.add(span)
                    snippet = page_text[max(0, match.start() - 16): match.end() + 24].replace("\n", " ")
                    issues.append(
                        self._issue(
                            message,
                            {"page": page_idx, "pos": match.start()},
                            "warn",
                            evidence_text=snippet,
                        )
                    )

            for match in punctuation_pattern.finditer(page_text):
                span = (match.start(), match.end())
                if span in seen:
                    continue
                seen.add(span)
                snippet = page_text[max(0, match.start() - 16): match.end() + 24].replace("\n", " ")
                issues.append(
                    self._issue(
                        "\u4e2d\u6587\u8bed\u5883\u4e0b\u7591\u4f3c\u4f7f\u7528\u4e86\u82f1\u6587\u9017\u53f7\u8fde\u63a5\u91d1\u989d",
                        {"page": page_idx, "pos": match.start()},
                        "info",
                        evidence_text=snippet,
                    )
                )

            for pattern, message in abnormal_punctuation_patterns:
                for match in pattern.finditer(page_text):
                    span = (match.start(), match.end())
                    if span in seen:
                        continue
                    seen.add(span)
                    snippet = page_text[max(0, match.start() - 16): match.end() + 24].replace("\n", " ")
                    issues.append(
                        self._issue(
                            message,
                            {"page": page_idx, "pos": match.start()},
                            "warn",
                            evidence_text=snippet,
                        )
                    )

        # 引号配对按"全篇逻辑段落"检查（先合并软换行，且段落允许跨页）：
        # 同段右引号多于左引号才算未闭合。
        # - 逐行检查会把同段跨行的配对引号误判成异常（样张 4 条误报根因，HANDOFF §3.3B）；
        # - 按页检查会把"段首在上页页尾、段尾在下页页首"的跨页配对误判成异常
        #   （石泉路样张 3 条误报根因：p29 末"…一般行政管理事"与 p30 首"务（项）"…"）。
        page_texts_all = _page_texts(doc)
        page_spans: List[Tuple[int, int, int]] = []
        parts: List[str] = []
        offset = 0
        for page_idx, page_text in enumerate(page_texts_all, start=1):
            parts.append(page_text)
            page_spans.append((offset, offset + len(page_text), page_idx))
            offset += len(page_text) + 1  # +1 为页间连接符
        full_text = "\n".join(parts)

        def _page_at(pos: int) -> int:
            for start, end, pageno in page_spans:
                if start <= pos < end:
                    return pageno
            return len(page_texts_all)

        for paragraph in _merge_soft_wrapped_lines(full_text):
            has_double_imbalance = paragraph.count("\u201d") > paragraph.count("\u201c")
            has_single_imbalance = paragraph.count("\u2019") > paragraph.count("\u2018")
            if not (has_double_imbalance or has_single_imbalance):
                continue
            pos = full_text.find(paragraph[:20])
            issues.append(
                self._issue(
                    "\u7591\u4f3c\u591a\u4f59\u53f3\u5f15\u53f7",
                    {"page": _page_at(max(pos, 0))},
                    "warn",
                    evidence_text=paragraph[:200],
                )
            )

        return issues


def _count_toc_table_items(page_texts: Sequence[str]) -> int:
    table_line_pattern = re.compile(
        r"^(?:\s*\d+[\.、．]|\s*20\d{2}).*(?:\u603b\u8868|\u9884\u7b97\u8868|\u51b3\u7b97\u8868|\u7ecf\u8d39.*\u8868)"
    )
    items: set[str] = set()

    for page_text in page_texts[:4]:
        if "\u76ee\u5f55" not in page_text:
            continue
        for line in page_text.splitlines():
            text = line.strip()
            if not text or "\u8868" not in text:
                continue
            if not table_line_pattern.search(text):
                continue
            items.add(re.sub(r"\s+", "", text))
    return len(items)


class CMM003_TocCountConsistency(Rule):
    code, severity = "CMM-003", "warn"
    desc = "\u76ee\u5f55\u8868\u683c\u6570\u91cf\u4e0e\u6b63\u6587\u5b9a\u4f4d\u8868\u683c\u6570\u91cf\u4e00\u81f4\u6027"

    def apply(self, doc: Document) -> List[Issue]:
        texts = _page_texts(doc)
        toc_count = _count_toc_table_items(texts)
        if toc_count < 8:
            return []

        report_kind = _infer_report_kind(doc)
        if report_kind == "budget":
            anchors = find_budget_anchors(doc)
        else:
            anchors = find_table_anchors(doc)

        detected_count = sum(1 for pages in anchors.values() if pages)
        if detected_count == toc_count:
            return []

        return [
            self._issue(
                f"\u76ee\u5f55\u4e0e\u6b63\u6587\u8868\u6570\u7591\u4f3c\u4e0d\u4e00\u81f4\uff1a\u76ee\u5f55={toc_count}\uff0c\u5b9e\u9645\u5b9a\u4f4d={detected_count}",
                {"page": 1},
                "warn",
                evidence_text=f"toc_count={toc_count}, detected={detected_count}",
            )
        ]


_ROW_CODE_RE = re.compile(
    r"^\s*(\d{3})(?:\s+(\d{2}))?(?:\s+(\d{2}))?\s+.+?\s+([0-9][0-9,]*\.?[0-9]*)\b"
)


# 科目域判定（docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.3A）：
# 收入分类（101-110）、一般公共预算功能分类（201-229）、经济分类（301-310）
# 属于互不可比的科目体系；经济分类编码只应出现在经济分类性质的表中。
def _code_domain(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) < 3:
        return "other"
    prefix = int(digits[:3])
    if 101 <= prefix <= 110:
        return "revenue"
    if 201 <= prefix <= 229:
        return "functional"
    if 301 <= prefix <= 310:
        return "economic"
    return "other"


def _dominant_code_domain(codes) -> Optional[str]:
    """取一组编码的主科目域；域混杂或无法判定时返回 None。"""
    domains = {_code_domain(code) for code in codes}
    domains.discard("other")
    if len(domains) != 1:
        return None
    return domains.pop()


def _extract_code_amount_pairs(page_texts: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
    income_titles = (
        "\u6536\u5165\u9884\u7b97\u603b\u8868",
        "\u6536\u5165\u51b3\u7b97\u8868",
    )
    expense_titles = (
        "\u652f\u51fa\u9884\u7b97\u603b\u8868",
        "\u652f\u51fa\u51b3\u7b97\u8868",
    )
    stop_titles = (
        "\u8d22\u653f\u62e8\u6b3e\u6536\u652f",
        "\u4e00\u822c\u516c\u5171\u9884\u7b97",
        "\u4e00\u822c\u516c\u5171\u51b3\u7b97",
    )

    mode: Optional[str] = None
    income: Dict[str, float] = {}
    expense: Dict[str, float] = {}

    for page_text in page_texts:
        if any(title in page_text for title in income_titles):
            mode = "income"
        elif any(title in page_text for title in expense_titles):
            mode = "expense"
        elif mode and any(title in page_text for title in stop_titles):
            mode = None

        if not mode:
            continue

        for line in page_text.splitlines():
            match = _ROW_CODE_RE.match(line.strip())
            if not match:
                continue
            c1, c2, c3, amount_text = match.groups()
            amount = _to_float(amount_text)
            if amount is None:
                continue
            code = c1 + (c2 or "") + (c3 or "")
            if mode == "income":
                income.setdefault(code, amount)
            else:
                expense.setdefault(code, amount)

    return income, expense


class CMM004_CodeMirrorConsistency(Rule):
    code, severity = "CMM-004", "warn"
    desc = "\u6536\u5165/\u652f\u51fa\u603b\u8868\u7c7b\u6b3e\u9879\u7f16\u7801\u4e0e\u91d1\u989d\u955c\u50cf\u4e00\u81f4\u6027"

    def apply(self, doc: Document) -> List[Issue]:
        income, expense = _extract_code_amount_pairs(_page_texts(doc))
        if len(income) < 2 or len(expense) < 2:
            raise RuleDeferred(
                self.code,
                detail="收入或支出明细条目不足2条，无法执行镜像比对",
                unresolved_reasons=["收入或支出明细条目不足"],
            )

        # 科目域隔离（P0 止血）：只有两侧主科目域一致时才可镜像比较。
        # 收入分类、功能分类、经济分类互不可比——经济分类编码（301-310）
        # 只出现在经济分类性质的《基本支出决算表》是正常且必然的，
        # 不能报"仅出现在支出表"（样张 33 条误报的根因）。
        income_domain = _dominant_code_domain(income)
        expense_domain = _dominant_code_domain(expense)
        if not income_domain or not expense_domain or income_domain != expense_domain:
            raise RuleNotApplicable(
                self.code,
                f"两侧科目域不同（income={income_domain}, expense={expense_domain}）"
                "或域混杂，收入/功能分类/经济分类之间不可比",
            )

        common = sorted(set(income).intersection(expense))
        if len(common) < 2:
            raise RuleDeferred(
                self.code,
                detail="收支公共科目条目不足2条，无法执行镜像比对",
                unresolved_reasons=["收支公共科目条目不足"],
            )

        diffs = [
            code for code in common if abs(income[code] - expense[code]) > 1e-6
        ]
        only_income = sorted(set(income) - set(expense))
        only_expense = sorted(set(expense) - set(income))

        issues: List[Issue] = []
        anchors = find_table_anchors(doc)
        candidate_pages = []
        for table_name in ("\u6536\u5165\u51b3\u7b97\u8868", "\u652f\u51fa\u51b3\u7b97\u8868", "\u6536\u5165\u652f\u51fa\u51b3\u7b97\u603b\u8868"):
            candidate_pages.extend(anchors.get(table_name, []))
        page_number = min(candidate_pages) if candidate_pages else 1
        base_location = {"page": page_number, "table": "\u6536\u5165/\u652f\u51fa\u603b\u8868"}

        for code in diffs:
            income_value = income[code]
            expense_value = expense[code]
            diff_value = abs(income_value - expense_value)
            issues.append(
                self._issue(
                    f"\u7c7b\u6b3e\u9879\u7f16\u7801 {code} \u91d1\u989d\u4e0d\u4e00\u81f4",
                    {**base_location, "row": code},
                    "warn",
                    evidence_text=(
                        f"code={code}; income={income_value}; expense={expense_value}; diff={diff_value}"
                    ),
                )
            )

        for code in only_income:
            issues.append(
                self._issue(
                    f"\u7c7b\u6b3e\u9879\u7f16\u7801 {code} \u4ec5\u51fa\u73b0\u5728\u6536\u5165\u8868",
                    {**base_location, "row": code},
                    "warn",
                    evidence_text=f"income_only={code}",
                )
            )

        for code in only_expense:
            issues.append(
                self._issue(
                    f"\u7c7b\u6b3e\u9879\u7f16\u7801 {code} \u4ec5\u51fa\u73b0\u5728\u652f\u51fa\u8868",
                    {**base_location, "row": code},
                    "warn",
                    evidence_text=f"expense_only={code}",
                )
            )

        return issues


_ZERO_INCREASE_PATTERN = re.compile(
    r"([一-龥]{0,24}?拨款支出预算)0(?:\.0+)?万元，比20\d{2}年预算增加([0-9][0-9,]*\.?[0-9]*)万元"
)
_ABNORMAL_DELTA_WORDING_PATTERN = re.compile(r"(增加|减少)持平")
_BUDGET_FINAL_DIRECTION_RE = re.compile(
    r"(?:^|[。；;\n])(?P<segment>[^。；;\n]{0,80}?"
    r"年初预算(?:数)?(?:为|是)?\s*(?P<budget>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[^。；;\n]{0,120}?"
    r"(?:支出)?决算(?:数)?(?:为|是)?\s*(?P<final>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[^。；;\n]{0,120}?"
    r"(?P<word>持平|一致|等于|增加|增长|高于|超出|超过|减少|下降|低于|少于)[^。；;\n]{0,40})",
    re.S,
)
_UP_WORDS = {"增加", "增长", "高于", "超出", "超过"}
_DOWN_WORDS = {"减少", "下降", "低于", "少于"}
_FLAT_WORDS = {"持平", "一致", "等于"}


def _direction_from_word(word: str) -> Optional[str]:
    if word in _UP_WORDS:
        return "up"
    if word in _DOWN_WORDS:
        return "down"
    if word in _FLAT_WORDS:
        return "flat"
    return None


def _expected_budget_final_direction(budget: float, final: float) -> str:
    if abs(final - budget) <= 0.01:
        return "flat"
    return "up" if final > budget else "down"


def _direction_label(direction: str) -> str:
    return {"flat": "持平", "up": "增加", "down": "减少"}.get(direction, direction)


def _budget_final_direction_issues(rule: Rule, page_idx: int, page_text: str) -> List[Issue]:
    issues: List[Issue] = []
    for match in _BUDGET_FINAL_DIRECTION_RE.finditer(page_text or ""):
        budget = _to_float(match.group("budget"))
        final = _to_float(match.group("final"))
        actual_direction = _direction_from_word(match.group("word"))
        if budget is None or final is None or actual_direction is None:
            continue
        expected_direction = _expected_budget_final_direction(budget, final)
        if actual_direction == expected_direction:
            continue
        segment = match.group("segment").strip()
        issues.append(
            rule._issue(
                (
                    "预算数与决算数文字方向不一致："
                    f"年初预算数={budget:.2f}万元，决算数={final:.2f}万元，"
                    f"按金额应表述为“{_direction_label(expected_direction)}”，"
                    f"当前写为“{match.group('word')}”。"
                ),
                {"page": page_idx, "pos": match.start(), "section": "支出决算具体情况"},
                "error",
                evidence_text=segment,
            )
        )
    return issues


_TEMPLATE_LEFTOVER_PATTERNS: Sequence[Tuple[re.Pattern[str], str]] = (
    (
        re.compile(r"(?:增加|减少)[（(](?:减少|增加)[)）]"),
        "\u7591\u4f3c\u6a21\u677f\u6b8b\u7559\uff1a\u589e\u51cf\u65b9\u5411\u672a\u786e\u5b9a",
    ),
    (
        re.compile(r"\u4e3b\u8981\u539f\u56e0\u662f[：:]?[\u3002\uff1b;!?？！]"),
        "\u7591\u4f3c\u6b8b\u7f3a\u53e5\uff1a\u201c\u4e3b\u8981\u539f\u56e0\u662f\u201d\u540e\u7f3a\u5c11\u539f\u56e0\u8bf4\u660e",
    ),
)


class CMM005_ComparativeNarrativeLogic(Rule):
    code, severity = "CMM-005", "warn"
    desc = "同比叙述逻辑异常检查（预/决算通用）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []

        for page_idx, page_text in enumerate(_page_texts(doc), start=1):
            flat_text = _compact_text(page_text)
            if not flat_text:
                continue

            seen_spans: set[Tuple[int, int, str]] = set()

            for pattern, message in _TEMPLATE_LEFTOVER_PATTERNS:
                for match in pattern.finditer(page_text):
                    span_key = (match.start(), match.end(), message)
                    if span_key in seen_spans:
                        continue
                    seen_spans.add(span_key)
                    issues.append(
                        self._issue(
                            message,
                            {"page": page_idx, "pos": match.start()},
                            "warn",
                            evidence_text=_sentence_around(page_text, match.start(), match.end()),
                        )
                    )

            for match in _ABNORMAL_DELTA_WORDING_PATTERN.finditer(flat_text):
                span_key = (match.start(), match.end(), "wording")
                if span_key in seen_spans:
                    continue
                seen_spans.add(span_key)
                phrase = match.group(0)
                issues.append(
                    self._issue(
                        f"疑似模板残留：出现“{phrase}”",
                        {"page": page_idx, "pos": match.start()},
                        "warn",
                        evidence_text=_snippet(flat_text, match.start(), match.end()),
                    )
                )

            for match in _ZERO_INCREASE_PATTERN.finditer(flat_text):
                amount = _to_float(match.group(2))
                if amount is None or amount <= 0:
                    unresolved_reasons.append(f"第{page_idx}页零增长语句数值解析异常")
                    continue
                span_key = (match.start(), match.end(), "zero_increase")
                if span_key in seen_spans:
                    continue
                seen_spans.add(span_key)
                item_name = match.group(1)
                issues.append(
                    self._issue(
                        f"同比口径矛盾：{item_name}为0万元，却写“比上年增加{amount:.2f}万元”",
                        {"page": page_idx, "pos": match.start()},
                        "error",
                        evidence_text=_snippet(flat_text, match.start(), match.end()),
                    )
                )

            issues.extend(_budget_final_direction_issues(self, page_idx, page_text))

        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail=f"部分同比表述数值未能解析: {', '.join(unresolved_reasons[:5])}",
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


_BUDGET_DELTA_PATTERN_TEMPLATE = re.compile(
    r"(?P<subject>收入|支出)预算[0-9][0-9,]*\.?[0-9]*万元[^。；]{0,120}?比20\d{2}年预算(?P<dir>增加|减少)(?P<amount>[0-9][0-9,]*\.?[0-9]*)万元"
)
_INCOME_EXPENSE_SUMMARY_PATTERN = re.compile(r"财政拨款收入支出(?P<dir>增加|减少)")


def _extract_budget_delta(flat_text: str, subject: str) -> Optional[Dict[str, Any]]:
    for match in _BUDGET_DELTA_PATTERN_TEMPLATE.finditer(flat_text):
        if match.group("subject") != subject:
            continue
        amount = _to_float(match.group("amount"))
        if amount is None:
            continue
        return {
            "direction": match.group("dir"),
            "amount": amount,
            "start": match.start(),
            "end": match.end(),
        }
    return None


class CMM006_IncomeExpenseTrendConsistency(Rule):
    code, severity = "CMM-006", "warn"
    desc = "收入/支出同比方向一致性检查（预/决算通用）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []

        for page_idx, page_text in enumerate(_page_texts(doc), start=1):
            flat_text = _compact_text(page_text)
            if not flat_text:
                continue

            income_delta = _extract_budget_delta(flat_text, "收入")
            expense_delta = _extract_budget_delta(flat_text, "支出")
            summary_match = _INCOME_EXPENSE_SUMMARY_PATTERN.search(flat_text)

            if income_delta and expense_delta and income_delta["direction"] != expense_delta["direction"]:
                start = min(int(income_delta["start"]), int(expense_delta["start"]))
                end = max(int(income_delta["end"]), int(expense_delta["end"]))
                mismatch_sentence = _sentence_around(flat_text, start, end)
                severity = (
                    "error"
                    if _close(float(income_delta["amount"]), float(expense_delta["amount"]))
                    else "warn"
                )
                message = (
                    "收入/支出同比方向矛盾：同页同时出现“收入增加/减少”与“支出减少/增加”的相反方向描述。"
                )
                if mismatch_sentence:
                    message += f" 命中原文：{mismatch_sentence}"
                issues.append(
                    self._issue(
                        message,
                        {"page": page_idx, "pos": start},
                        severity,
                        evidence_text=mismatch_sentence or _snippet(flat_text, start, end),
                    )
                )

            if summary_match and income_delta and expense_delta:
                summary_direction = summary_match.group("dir")
                if (
                    summary_direction != str(income_delta["direction"])
                    or summary_direction != str(expense_delta["direction"])
                ):
                    summary_sentence = _sentence_around(
                        flat_text, summary_match.start(), summary_match.end()
                    )
                    message = (
                        f"口径描述矛盾：文中写“财政拨款收入支出{summary_direction}”，但收入/支出同比方向不一致。"
                    )
                    if summary_sentence:
                        message += f" 这段文字出现了错误：{summary_sentence}"
                    issues.append(
                        self._issue(
                            message,
                            {"page": page_idx, "pos": summary_match.start()},
                            "warn",
                            evidence_text=summary_sentence
                            or _snippet(flat_text, summary_match.start(), summary_match.end()),
                        )
                    )

        return issues


# ============================================================================
# WP4-D：同比基期/本期/增减额与增长百分比的确定性复算（CMM-007，预/决算通用）
#
# 真值（2026-09-17 独立验收真实反例，当时均未报告）：
# - 宜川 P36：公务接待费本期 0.3 万元、比上年增加 0.3 万元，却写「增长100%」
#   ——复算基期 0.3−0.3=0，同比百分比不存在有限定义；
# - 文旅 P28：公务接待费本期 0.40 万元、增加 0.40 万元，「增长100%」——同理。
# 证据链与设计口径：docs/WP4D_ZERO_BASE_20260925.md。
#
# 边界（不重复造轮子）：CMM-005 管"当前为0却写增加"等模板/方向异常；
# V33-234 / BUD-111 管"显式基期+本期+百分比"复算（基期为 0 时跳过）——
# 零基数与"本期+增减额+百分比"复算此前无人覆盖。百分比容差复用仓库统一
# 策略值（abs 0.5pp / 相对 1%，BUD-111/V33-234 同款），金额侧 Decimal +
# 显示半步长包络（compute_dynamic_envelope），不自造容差。
# ============================================================================

_CMM007_OBLIGATION_ID = "OBL-TREND-ZERO-BASE"

#: 同比变化语义单元：指标 (口径后缀)? (比20XX年度)? 方向词(幅/了)? 金额 单位，
#: 同句读单元内再接 (增长|下降|提高|降低)X%。谓词与口径词字符间容忍 PDF
#: 软换行（宜川原文「支出决算增\n加 0.3 万元，增长 100%」）。句读（。；）
#: 不可跨越——三数必须同属一个语义单元，禁止跨句拼接。
_CMM007_UNIT_RE = re.compile(
    r"(?P<ind>[一-龥（）“”]{2,30}?)\s*"
    r"(?:支\s*出\s*决\s*算\s*数|支\s*出\s*决\s*算|决\s*算\s*数|决\s*算|支\s*出|预\s*算)?"
    r"(?:\s*比\s*(?P<base_year>20\d{2})\s*年(?:度)?)?"
    r"\s*(?P<verb>增\s*加|减\s*少|增\s*长|下\s*降)(?:幅|了)?\s*"
    r"(?P<delta>\d[\d,，.]*)\s*(?P<dunit>万\s*元|亿\s*元|元)"
    r"[^。；]{0,12}?"
    r"(?P<pverb>增\s*长|下\s*降|提\s*高|降\s*低)(?:幅|了)?\s*(?P<pct>\d[\d,，.]*)\s*%"
)

#: 同节本期金额披露：指标 (口径后缀)? (为|是)? 金额 单位。
_CMM007_CURRENT_RE = re.compile(
    r"(?P<ind>[一-龥（）“”]{2,30}?)\s*"
    r"(?:支\s*出\s*决\s*算\s*数|支\s*出\s*决\s*算|决\s*算\s*数|决\s*算|支\s*出|预\s*算)?"
    r"(?:\s*(?:为|是))?\s*"
    r"(?P<amt>\d[\d,，.]*)\s*(?P<aunit>万\s*元|亿\s*元|元)"
)

#: 显式基期披露：上年/上年度/20XX年度 的指标金额。
_CMM007_EXPLICIT_PRIOR_RE = re.compile(
    r"(?:上\s*年(?:度|同期)?|(?P<pyear>20\d{2})\s*年(?:度)?)\s*(?:的)?\s*"
    r"(?P<ind>[一-龥（）“”]{2,30}?)\s*"
    r"(?:支\s*出\s*决\s*算\s*数|支\s*出\s*决\s*算|决\s*算\s*数|决\s*算|支\s*出|预\s*算)?"
    r"(?:\s*(?:为|是))?\s*"
    r"(?P<amt>\d[\d,，.]*)\s*(?P<aunit>万\s*元|亿\s*元|元)"
)

#: 指标名归一：去时间/比较前缀（本年/比上年…）与口径后缀（支出决算/决算/
#: 支出/预算）——身份是业务项，口径（预算/决算）是绑定槽位自己的属性。
_CMM007_IND_PREFIX_RE = re.compile(r"^(?:比\s*)?(?:上\s*年(?:度|同期)?|本\s*年(?:度)?)+")
_CMM007_IND_SUFFIX_RE = re.compile(
    r"(?:支\s*出\s*决\s*算\s*数|支\s*出\s*决\s*算|决\s*算\s*数|决\s*算|支\s*出|预\s*算)$"
)

#: 上年/基期标记（本期披露候选的拒绝条件：上年口径的金额不是本期）
_CMM007_PRIOR_MARK_RE = re.compile(r"上\s*年(?:度|同期)?|以前年度|(20\d{2})\s*年(?:度)?")

_CMM007_UP_VERBS = ("增加", "增长", "提高")
_CMM007_DOWN_VERBS = ("减少", "下降", "降低")


def _cmm007_clean_amount(text: str) -> str:
    return re.sub(r"[,，\s]", "", text or "")


def _cmm007_scale(cleaned: str) -> int:
    return len(cleaned.split(".", 1)[1]) if "." in cleaned else 0


def _cmm007_canon(name: str) -> str:
    """指标身份归一：去前缀/后缀后必须仍有业务项名，否则为空（拒绝绑定）。"""
    text = _CMM007_IND_PREFIX_RE.sub("", _txt_fund_norm(name))
    while True:
        stripped = _CMM007_IND_SUFFIX_RE.sub("", text)
        if stripped == text:
            return stripped
        text = stripped


def _cmm007_verb_direction(verb: str) -> str:
    normalized = re.sub(r"\s+", "", verb)
    return "up" if normalized in _CMM007_UP_VERBS else "down"


def _cmm007_clause_marked_prior(merged: str, start: int, year: int) -> bool:
    """句读前缀（16 字符窗）出现上年/基期年度标记 → 该披露是基期口径。"""
    prefix = merged[max(0, start - 16):start]
    cut = max(prefix.rfind(ch) for ch in "。；，、：")
    if cut >= 0:
        prefix = prefix[cut + 1:]
    match = _CMM007_PRIOR_MARK_RE.search(prefix)
    if match is None:
        return False
    found_year = match.group(1)
    return found_year is None or int(found_year) == year - 1


class CMM007_ZeroBaseTrendRecompute(Rule):
    """同比基期/本期/增减额与增长百分比的确定性复算（零基数）。

    只绑定完整语义单元：同节内"指标 + 本期金额"配"（比20XX年度）增加/减少
    X 万元，增长/下降 Y%"。增加 → 基期 = 本期 − X；减少 → 基期 = 本期 + X。
    基期在显示精度包络内为 0 却声明有限百分比 → 正式 finding（同比百分比
    无定义）；基期非 0 → 复算方向与百分比；显式基期与推导基期冲突 → 四元
    矛盾 finding。完成率/占比/持平/模板残留不归本规则（CMM-005 与其它义务）。
    """

    code, severity = "CMM-007", "error"
    desc = "同比基期/本期/增减额与增长百分比的确定性复算（零基数）"

    def apply(self, doc: Document) -> List[Issue]:
        merged, offsets = _txt_fund_merged_pages(doc)
        if not merged.strip():
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，同比复算无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )
        sections = _nar_repeat_sections(merged, doc, offsets)
        heading_pages = tuple(
            {_txt_fund_page_for(offsets, start) for _, start, _ in sections}
        )
        year = _resolve_fiscal_year(doc, heading_pages)
        if year is None:
            raise RuleDeferred(
                self.code,
                "未能确认材料财政年度，同比期间不可确认",
                unresolved_reasons=["未能确认材料财政年度"],
            )

        issues: List[Issue] = []
        unresolved: List[str] = []
        currents, priors = self._collect_amounts(merged, offsets, sections, year, unresolved)

        for match in _CMM007_UNIT_RE.finditer(merged):
            canon = _cmm007_canon(match.group("ind"))
            section_title, section_start = _nar_repeat_section_of(sections, match.start())
            base_year = match.group("base_year")
            if base_year is not None and int(base_year) != year - 1:
                continue  # 与材料年度不衔接的比较窗口：已解决的"不可比"
            if not canon:
                # 比较句自身无指标身份（如「比上年预算增加…」承接式）时，
                # 继承同句读单元内唯一一个已接受本期披露的指标——只允许
                # 同句承接，禁止跨句拼接；非空 canon 无本期披露则 fail-closed
                canon = self._inherit_same_clause(
                    merged, match.start(), currents, section_start
                )
            if not canon:
                continue
            key = (canon, section_start)
            candidates = currents.get(key)
            if not candidates:
                continue  # 无可验证的本期金额 → fail-closed 不比
            if not self._candidates_agree(candidates):
                unresolved.append(
                    f"指标「{canon}」在本节存在多个不一致的本期金额披露，"
                    "无法确定比较基期（parse_ambiguity）"
                )
                continue
            current = candidates[0]
            delta_text = _cmm007_clean_amount(match.group("delta"))
            delta_unit = _txt_fund_norm(match.group("dunit"))
            delta_factor = _TXT_FUND_UNIT_TO_WAN.get(delta_unit)
            current_factor = _TXT_FUND_UNIT_TO_WAN.get(current["unit"])
            if delta_factor is None or current_factor is None:
                unresolved.append(
                    f"指标「{canon}」同比句金额单位不在归一口径内，不形成正式比较"
                )
                continue
            direction = _cmm007_verb_direction(match.group("verb"))
            delta_wan = Decimal(delta_text) * delta_factor
            current_wan = Decimal(current["amount"]) * current_factor
            sign = 1 if direction == "down" else -1
            derived_prior = current_wan + sign * delta_wan

            prior_candidates = priors.get(key)
            explicit_prior = None
            if prior_candidates:
                if not self._candidates_agree(prior_candidates):
                    unresolved.append(
                        f"指标「{canon}」在本节存在多个不一致的上年基期披露，"
                        "基期交叉验证不可完成（parse_ambiguity）"
                    )
                else:
                    explicit_prior = prior_candidates[0]
                    explicit_wan = Decimal(explicit_prior["amount"]) * _TXT_FUND_UNIT_TO_WAN.get(
                        explicit_prior["unit"], Decimal("1")
                    )
                    envelope = compute_dynamic_envelope(
                        [
                            (current["scale"], current["unit"]),
                            (_cmm007_scale(delta_text), delta_unit),
                            (explicit_prior["scale"], explicit_prior["unit"]),
                        ]
                    )
                    if abs(explicit_wan - derived_prior) > envelope:
                        issues.append(
                            self._issue(
                                f"同比基期交叉矛盾（{canon}）：上年披露"
                                f" {explicit_prior['amount']} {explicit_prior['unit']}，"
                                f"但按本期 {current['amount']} {current['unit']}、"
                                f"{match.group('verb')}{delta_text} {delta_unit} 推导基期为"
                                f" {_txt_fund_display(derived_prior)} 万元，四个数字"
                                "不能同时成立，需人工复核底稿。",
                                self._location(
                                    sections, offsets, match, canon, current,
                                    delta_text, delta_unit, direction, derived_prior,
                                    year,
                                    explicit_prior=explicit_prior["amount"],
                                ),
                                severity="error",
                                evidence_text=self._evidence(
                                    match, current, explicit_prior=explicit_prior
                                ),
                            )
                        )
                        continue

            amount_envelope = compute_dynamic_envelope(
                [(current["scale"], current["unit"]), (_cmm007_scale(delta_text), delta_unit)]
            )
            pct_text = _cmm007_clean_amount(match.group("pct"))
            pct_verb = _cmm007_verb_direction(match.group("pverb"))

            if abs(derived_prior) <= amount_envelope:
                issues.append(
                    self._issue(
                        f"同比零基数百分比无定义（{canon}）：本期"
                        f" {current['amount']} {current['unit']}，{match.group('verb')}"
                        f" {delta_text} {delta_unit}，复算基期为 0，却声明"
                        f"「{re.sub(r'[（），,]','',match.group('pverb'))}{match.group('pct')}%」"
                        "——分母为 0，同比百分比不存在有限定义。本期金额、增减额"
                        "与增长百分比三者不能同时成立，需人工复核底稿。",
                        self._location(
                            sections, offsets, match, canon, current, delta_text,
                            delta_unit, direction, derived_prior, year, zero_base=True,
                        ),
                        severity="error",
                        evidence_text=self._evidence(match, current),
                    )
                )
                continue

            if direction != pct_verb:
                issues.append(
                    self._issue(
                        f"同比方向矛盾（{canon}）：本期 {current['amount']}"
                        f" {current['unit']}、{match.group('verb')} {delta_text}"
                        f" {delta_unit}，却写「{re.sub(r'[（]，,]','',match.group('pverb'))}"
                        f"{match.group('pct')}%」——金额增减方向与百分比方向相反，"
                        "两处披露不能同时成立，需人工复核底稿。",
                        self._location(
                            sections, offsets, match, canon, current, delta_text,
                            delta_unit, direction, derived_prior, year,
                            direction_conflict=True,
                            recomputed=self._recompute(current_wan, derived_prior),
                        ),
                        severity="error",
                        evidence_text=self._evidence(match, current),
                    )
                )
                continue

            recomputed = self._recompute(current_wan, derived_prior)
            declared = Decimal(pct_text)
            tolerance = max(
                Decimal("0.5"),
                abs(recomputed) * Decimal("0.01"),
                abs(declared) * Decimal("0.01"),
            )
            diff = abs(declared - recomputed)
            if diff <= tolerance:
                continue
            severity = "error" if diff > Decimal("1.0") else "warn"
            issues.append(
                self._issue(
                    f"增减额与同比百分比复算不一致（{canon}）：本期"
                    f" {current['amount']} {current['unit']}、{match.group('verb')}"
                    f" {delta_text} {delta_unit}，复算基期"
                    f" {_txt_fund_display(derived_prior)} 万元，应为"
                    f"{re.sub(r'[（]，,]','',match.group('pverb'))}{_txt_fund_display(recomputed)}%"
                    f"，声明 {match.group('pct')}%，相差 {_txt_fund_display(diff)} 个百分点。"
                    "本期金额、增减额与增长百分比三者不能同时成立，需人工复核底稿。",
                    self._location(
                        sections, offsets, match, canon, current, delta_text, delta_unit,
                        direction, derived_prior, year,
                        recomputed=_txt_fund_display(recomputed),
                    ),
                    severity=severity,
                    evidence_text=self._evidence(match, current),
                )
            )

        if unresolved:
            reasons = list(dict.fromkeys(unresolved))
            raise RuleDeferred(
                self.code,
                "；".join(reasons),
                partial_issues=issues,
                unresolved_reasons=reasons,
            )
        return issues

    # ---- 内部：同节金额披露收集 ----

    def _collect_amounts(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: int,
        unresolved: List[str],
    ) -> Tuple[Dict[Tuple[str, int], List[Dict[str, Any]]], Dict[Tuple[str, int], List[Dict[str, Any]]]]:
        currents: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        priors: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for match in _CMM007_CURRENT_RE.finditer(merged):
            entry = self._amount_entry(merged, offsets, sections, match)
            if entry is None:
                continue
            if re.match(r"^(?:上\s*年|以前)", entry["raw_ind"]) or _cmm007_clause_marked_prior(
                merged, match.start(), year
            ):
                continue  # 上年口径披露不是本期金额
            currents.setdefault(entry["key"], []).append(entry)
        for match in _CMM007_EXPLICIT_PRIOR_RE.finditer(merged):
            entry = self._amount_entry(merged, offsets, sections, match)
            if entry is None:
                continue
            pyear = match.group("pyear")
            if pyear is not None and int(pyear) != year - 1:
                continue  # 与材料年度不衔接的基期年：不作为本材料基期
            priors.setdefault(entry["key"], []).append(entry)
        return currents, priors

    def _amount_entry(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        match: Any,
    ) -> Optional[Dict[str, Any]]:
        raw_ind = match.group("ind")
        canon = _cmm007_canon(raw_ind)
        if not canon:
            return None  # 归一后为空 = 纯口径/时间词，无业务项身份
        amount = _cmm007_clean_amount(match.group("amt"))
        unit = _txt_fund_norm(match.group("aunit"))
        if _TXT_FUND_UNIT_TO_WAN.get(unit) is None:
            return None
        section_title, section_start = _nar_repeat_section_of(sections, match.start())
        return {
            "key": (canon, section_start),
            "raw_ind": raw_ind,
            "canon": canon,
            "amount": amount,
            "unit": unit,
            "scale": _cmm007_scale(amount),
            "page": _txt_fund_page_for(offsets, match.start()),
            "section": section_title,
            "span": re.sub(r"\s+", "", merged[match.start():match.end()]),
            "start": match.start(),
            "end": match.end(),
        }

    def _candidates_agree(self, candidates: List[Dict[str, Any]]) -> bool:
        values = {
            Decimal(item["amount"]) * _TXT_FUND_UNIT_TO_WAN.get(item["unit"], Decimal("1"))
            for item in candidates
        }
        return len(values) == 1

    def _inherit_same_clause(
        self,
        merged: str,
        unit_start: int,
        currents: Dict[Tuple[str, int], List[Dict[str, Any]]],
        section_start: int,
    ) -> str:
        """比较句自身无指标身份时，继承同句读单元（无。；隔断）内唯一的
        本期披露的指标名——承接式同句绑定，不跨句拼接。"""
        clause_start = merged.rfind("。", 0, unit_start)
        semi = merged.rfind("；", 0, unit_start)
        clause_start = max(clause_start, semi)
        inherited: List[str] = []
        for key, items in currents.items():
            if key[1] != section_start:
                continue
            for item in items:
                if clause_start < item["end"] <= unit_start:
                    inherited.append(key[0])
                    break
        unique = set(inherited)
        return inherited[0] if len(unique) == 1 else ""

    # ---- 内部：finding 组装 ----

    def _recompute(self, current_wan: Decimal, derived_prior: Decimal) -> Decimal:
        return abs(current_wan - derived_prior) / abs(derived_prior) * Decimal("100")

    def _location(
        self,
        sections: List[Tuple[str, int, int]],
        offsets: List[int],
        match: Any,
        canon: str,
        current: Dict[str, Any],
        delta_text: str,
        delta_unit: str,
        direction: str,
        derived_prior: Decimal,
        year: int,
        zero_base: bool = False,
        direction_conflict: bool = False,
        recomputed: Optional[str] = None,
        explicit_prior: Optional[str] = None,
    ) -> Dict[str, Any]:
        unit_page = _txt_fund_page_for(offsets, match.start())
        section_title, _ = _nar_repeat_section_of(sections, match.start())
        location: Dict[str, Any] = {
            "page": unit_page,
            "pages": sorted({unit_page, current["page"]}),
            "section": section_title,
            "indicator": canon,
            "current_amount": current["amount"],
            "direction": re.sub(r"\s+", "", match.group("verb")),
            "delta_amount": delta_text,
            "delta_unit": delta_unit,
            "derived_prior": _txt_fund_display(derived_prior),
            "declared_percent": _cmm007_clean_amount(match.group("pct")),
            "unit": "万元",
            "fiscal_year": year,
            "obligation_id": _CMM007_OBLIGATION_ID,
            "zero_base": zero_base,
            "direction_conflict": direction_conflict,
            "table_refs": [
                {
                    "role": "增减句",
                    "page": unit_page,
                    "section": section_title,
                    "span": re.sub(r"\s+", "", match.group(0)),
                },
                {
                    "role": "本期披露",
                    "page": current["page"],
                    "section": current["section"],
                    "span": current["span"],
                },
            ],
        }
        if recomputed is not None:
            location["recomputed_percent"] = recomputed
        if explicit_prior is not None:
            location["explicit_prior"] = explicit_prior
        return location

    def _evidence(
        self,
        match: Any,
        current: Dict[str, Any],
        explicit_prior: Optional[Dict[str, Any]] = None,
    ) -> str:
        lines = [
            f"增减句：{match.group(0)}",
            f"本期披露：{current['span']}（第{current['page']}页）",
            f"口径：本期金额与增减额均为本年支出/预算口径，同比基期按"
            f"{match.group('verb')}反推",
        ]
        if explicit_prior is not None:
            lines.append(
                f"上年基期披露：{explicit_prior['span']}（第{explicit_prior['page']}页）"
            )
        return "\n".join(lines)


# ============================================================================
# WP4-F：百分比写法完整性（V33-DISCLOSURE-PERCENT-UNIT，预/决算通用）
#
# 真值（REAL，2026-09-16 样张复核人工判定 Y08，当时系统未报告）：
# - 宜川路街道 2025 年度决算（41 页，SHA ``f809eef2…``）P28
#   「（二）一般公共预算财政拨款支出决算结构情况」：城乡社区支出(类)
#   15411.43 万元，占 76.23——同枚举句其余 8 项结构占比全部带 %，
#   唯独此项缺百分号；复算 15411.43/20218.21×100 = 76.2255 → 76.23，
#   与文中分母（20218.21 万元）精确吻合，百分比语义三重成立。
#   人工判定 severity「低」，system_match=miss。证据链：
#   outputs/sample_validation_20260916/{adjudication.json,comparison.md}；
#   文档 docs/WP4F_PERCENT_UNIT_20260926.md。
#
# 谓词范围（不无限扩展）：占 / 占比 / 比重 / 比例——「占」是真实 truth
# 谓词，后三类是占比披露的强谓词形态。「率」类（完成率/执行率/增长率）
# 不纳入：归 CMM-007 与 V33-234/V33-TREND-COMPLETION-RATE 领域，避免
# 大面积重叠（AGENTS.md R004 任务边界）。
# ============================================================================

_WP4F_OBLIGATION_ID = "OBL-DISCLOSURE-PERCENT-UNIT"

#: 谓词与数值之间的空隙：行内空白，或**恰一次**换行（PDF 软换行把「占」留在
#: 行尾、数值换到次行行首，任务 §二十二）。空行（两个换行）不跨越——与共享
#: merge 纪律的空行断段、跨页 fail-closed 语义对齐。
_WP4F_GAP = r"(?:[ \t]*\n)?[ \t]*"

#: 占比谓词 + 数值（page 级单通道）。复合谓词（占比/比重/比例）先于单字
#: 「占」；「占」以负向前瞻排除「占地/占用」复合词（面积/金额语义）。谓词
#: 与数值间容忍「为/是/达到/达」（自身也可跨一次软换行）与空隙。在页面原文
#: 上扫描：每个匹配即一个物理 occurrence，identity = (page, start, end)。
_WP4F_PRED_RE = re.compile(
    r"(?P<pred>占比|比重|比例|占(?![地用]))"
    + _WP4F_GAP
    + r"(?:(?:为|是|达到|达)"
    + _WP4F_GAP
    + r")?"
    + r"(?P<num>\d+(?:\.\d+)?)"
)

#: 数值后百分比单位在场（合规）：ASCII %、全角 ％、中文「百分之」、
#: 「个百分点」。百分点是变动量单位而非百分比，同样视为表达完整
#: （AGENTS.md 决算占比口径：单位在/disclosure 即不缺）。
_WP4F_UNIT_PRESENT_RE = re.compile(r"\s*(?:%|％|‰|百\s*分\s*之|个\s*百\s*分\s*点)")

#: 数值后紧跟另一数字或千分位逗号（表格提取层列错位的伪绑定形态：
#: 财政局 2024 P21 实测「占⏎60.99 2.07% 445.69；⏎15.16%」——「占」后
#: 的 60.99 是金额而非占比）。fail-closed 跳过，不产出确定性结论。
_WP4F_DIGIT_RUN_RE = re.compile(r"\s*[,，]\s*\d|\s*\d")

#: 数值后明确非百分比单位（金额/面积/数量/时长/计数）首字：存在单位
#: 即不是百分比披露缺口 → 0 finding。首字覆盖（万元/亿元/平方米/亩/
#: 公顷/人次/个数/天数/成数/折数等派生词都由首字命中）。
_WP4F_NON_PCT_UNIT_RE = re.compile(
    r"\s*(?:万|亿|元|平|亩|公|人|个|项|天|日|次|辆|户|件|台|名|家|处|张|所|班|床"
    r"|支|年|月|时|分|秒|吨|升|米|里|度|间|座|栋|套|批|期|届|位|篇|部|册|幅|枚"
    r"|号|倍|折|成|岁)"
)

#: 中文序号章节标题（**行首锚**，section 定位用）：每个 candidate 只允许
#: 向前取自己 source_start 之前最近的标题（任务 §十二/§十三），不得借用
#: 后文或整页最后一个章节；定位不到不伪造（None）。alternation 长词在前，
#: 避免「…情况说明」被截断成「…情况」。
_WP4F_SECTION_HEAD_RE = re.compile(
    r"^(?:[一二三四五六七八九十]{1,3}\s*、|[(（][一二三四五六七八九十\d]{1,3}[)）])"
    r"[^。；，,]{2,45}?(?:情况说明|情况|说明|分析)",
    re.M,
)

#: 行尾谓词（跨行防线的通道归属判定用）：数字行上一非空行以占比谓词结尾，
#: 才是旧 line-break 补扫描通道的形态。
_WP4F_LINE_END_PRED_RE = re.compile(
    r"(?:占比|比重|比例|占)(?:[ \t]*(?:为|是|达到|达)[ \t]*)?$"
)

#: 纯数值行（只含数字/小数点/千分位/百分号/空白/句读）：表格提取层的
#: 表头行与数据行线性化形态（财政局 2024 P21 实测）——仅在「跨行 + 数字行
#: 是条目形态（merge 必断段，主扫描不可能命中）+ 上一非空行尾含谓词」的
#: line-break 通道形态下 fail-closed，不挡同行主扫描形态。
_WP4F_PLAIN_NUMBER_LINE_RE = re.compile(r"^[\d.,，%\s；;]+$")

#: 证据片段前后保留的上下文字符数。
_WP4F_SPAN_CONTEXT = 18


def _wp4f_norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


class _Wp4fPageLine(NamedTuple):
    """页面物理行：raw 起止偏移 + strip 后内容及其在原文中的起始偏移。"""

    start: int
    end: int
    stripped: str
    stripped_start: int


def _wp4f_page_lines(page_text: str) -> List[_Wp4fPageLine]:
    """按 keepends 切物理行并保留原文偏移（occurrence 定位与 section 回溯共用）。"""
    lines: List[_Wp4fPageLine] = []
    cursor = 0
    for raw in page_text.splitlines(True):
        start = cursor
        cursor += len(raw)
        stripped = raw.strip()
        lines.append(
            _Wp4fPageLine(
                start=start,
                end=cursor,
                stripped=stripped,
                stripped_start=start + (len(raw) - len(raw.lstrip())),
            )
        )
    return lines


class R33DisclosurePercentUnit(Rule):
    """百分比写法完整性：占比谓词 + 数值后缺百分比单位。

    在页面原文上单通道扫描（R1 的 merge 段主扫描 + line-break 补扫描双通道
    在 R2 合并：page 级 pattern 的空隙容忍"行内空白或恰一次软换行"，其匹配
    集与旧双通道的物理 occurrence 集合一一对应），每个匹配即一个物理
    occurrence，identity = (page, source_start, source_end)——同页同谓词
    同数值的不同 occurrence 各自成 finding，不会互相吞并（R2 P1 整改）。
    后缀判定：%/％/‰/百分之/个百分点 在场 → 合规；紧跟另一数字或千分位 →
    列错位伪绑定 fail-closed；明确非百分比单位 → 金额/面积/数量披露，0
    finding；数值所在行尾结束（rest 为空）时向后看下一个非空行——条目形态
    （merge 必断段）或无次行 → finding，行首数字 → 延续 skip，其余按次行
    开头判定——精确复刻旧 merge 段内 rest 的段边界语义。跨行形态且数字行
    是条目形态时叠加纯数值行防线（表格线性化 fail-closed，仅此通道，不挡
    同行形态）。数值 < 1 的小数比例形态无法证明作者意图，文案只说「披露
    口径不明确」，不得断言换算结果。76.234% 等精度问题归 V33-232，本规则
    不重叠。
    """

    code, severity = "V33-DISCLOSURE-PERCENT-UNIT", "info"
    desc = "百分比写法完整（不得漏百分号）"

    def apply(self, doc: Document) -> List[Issue]:
        page_texts = [str(item or "") for item in (doc.page_texts or [])]
        if not any(text.strip() for text in page_texts):
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，百分比写法完整性无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )
        # 表达完整性规则：fiscal_year 能确认就记录，确认不了不阻塞
        # （AGENTS.md 占比披露口径与期间计算无关，WP4-E 的年度门禁不适用）。
        fiscal_year = _resolve_fiscal_year(doc, ())

        issues: List[Issue] = []
        for page_idx, page_text in enumerate(page_texts, start=1):
            if not page_text.strip():
                continue
            lines = _wp4f_page_lines(page_text)
            headings = [
                (head.start(), _wp4f_norm(head.group()))
                for head in _WP4F_SECTION_HEAD_RE.finditer(page_text)
            ]
            for match in _WP4F_PRED_RE.finditer(page_text):
                issue = self._evaluate(
                    doc, page_text, lines, headings, match, page_idx, fiscal_year
                )
                if issue is not None:
                    issues.append(issue)
        return issues

    @staticmethod
    def _section_for(
        headings: List[Tuple[int, str]], position: int
    ) -> Optional[str]:
        """occurrence 之前最近的行首章节标题（严格 start < position，只向前；
        找不到为 None，不借用后文或整页最后一个标题——任务 §十二/§十三）。"""
        result: Optional[str] = None
        for start, text in headings:
            if start < position:
                result = text
            else:
                break
        return result

    @staticmethod
    def _line_at(lines: List[_Wp4fPageLine], position: int) -> int:
        """position 所在物理行下标（lines 按 start 升序，空页不调用）。"""
        low, high = 0, len(lines) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if lines[mid].start <= position:
                low = mid
            else:
                high = mid - 1
        return low

    @staticmethod
    def _next_nonempty_line(
        lines: List[_Wp4fPageLine], index: int
    ) -> Optional[_Wp4fPageLine]:
        for line in lines[index + 1:]:
            if line.stripped:
                return line
        return None

    def _evaluate(
        self,
        doc: Document,
        page_text: str,
        lines: List[_Wp4fPageLine],
        headings: List[Tuple[int, str]],
        match: Any,
        page: int,
        fiscal_year: Optional[int],
    ) -> Optional[Issue]:
        num_start = match.start("num")
        num_end = match.end("num")
        gap_text = match.group(0)[: num_start - match.start()]
        crossed = "\n" in gap_text

        line_idx = self._line_at(lines, num_start)
        line = lines[line_idx]
        stripped = line.stripped
        rest_inline = stripped[num_end - line.stripped_start:]

        # 跨行 + 数字行是条目形态（merge 必断段，主扫描通道不可能命中）+
        # 上一非空行尾含谓词 = 旧 line-break 通道形态：叠加纯数值行防线。
        if (
            crossed
            and _NEW_PARAGRAPH_RE.match(stripped)
            and _WP4F_PLAIN_NUMBER_LINE_RE.match(stripped)
        ):
            prev_line = next(
                (
                    item
                    for item in reversed(lines[:line_idx])
                    if item.stripped
                ),
                None,
            )
            if prev_line is not None and _WP4F_LINE_END_PRED_RE.search(
                prev_line.stripped
            ):
                return None  # 表格线性化形态，fail-closed

        if rest_inline.strip():
            verdict = self._classify(rest_inline)
        else:
            # 数值在行尾结束：后缀语义延伸到下一个非空行的开头——
            # 条目形态即 merge 段边界（旧段内 rest 到此为止）→ finding；
            # 行首数字是数字串延续（列错位）→ skip；其余按次行开头判定。
            next_line = self._next_nonempty_line(lines, line_idx)
            if next_line is None or _NEW_PARAGRAPH_RE.match(next_line.stripped):
                verdict = "finding"
            elif next_line.stripped[:1].isdigit() or _WP4F_DIGIT_RUN_RE.match(
                next_line.stripped
            ):
                verdict = "skip"
            else:
                verdict = self._classify(next_line.stripped)
        if verdict != "finding":
            return None

        source_start = match.start()
        source_end = match.end()
        return self._build_issue(
            doc,
            span=page_text[
                max(0, source_start - _WP4F_SPAN_CONTEXT):
                source_end + _WP4F_SPAN_CONTEXT
            ],
            predicate=_wp4f_norm(match.group("pred")),
            numeric_text=_wp4f_norm(match.group("num")),
            page=page,
            section=self._section_for(headings, source_start),
            fiscal_year=fiscal_year,
            source_start=source_start,
            source_end=source_end,
            line_break=crossed,
        )

    @staticmethod
    def _classify(rest: str) -> str:
        """数值后缀判定：compliant（百分比单位在场）/ skip（列错位伪绑定
        或明确非百分比单位）/ finding（缺百分比单位）。"""
        if _WP4F_UNIT_PRESENT_RE.match(rest):
            return "compliant"  # %/％/‰/百分之/个百分点（精度问题归 V33-232）
        if rest[:1].isdigit() or _WP4F_DIGIT_RUN_RE.match(rest):
            return "skip"  # 数字串延续：提取层列错位伪绑定，fail-closed
        if _WP4F_NON_PCT_UNIT_RE.match(rest):
            return "skip"  # 明确非百分比单位：金额/面积/数量/时长披露
        return "finding"

    def _build_issue(
        self,
        doc: Document,
        span: str,
        predicate: str,
        numeric_text: str,
        page: int,
        section: Optional[str],
        fiscal_year: Optional[int],
        source_start: int,
        source_end: int,
        line_break: bool,
    ) -> Issue:
        value = Decimal(numeric_text)
        location: Dict[str, Any] = {
            "page": page,
            "pos": source_start,
            # 物理 occurrence 身份（page_text 坐标系）：dedup / 证据定位 /
            # section 解析共用同一身份，不再维护独立的位置推断（任务 §十八）。
            "source_start": source_start,
            "source_end": source_end,
            "obligation_id": _WP4F_OBLIGATION_ID,
            "predicate": predicate,
            "numeric_text": numeric_text,
            "normalized_value": numeric_text,
            "unit_present": False,
            "expected_unit": "percent",
            "section": section,
            "fiscal_year": fiscal_year,
            "report_kind": getattr(doc, "report_kind", None),
            "line_break": line_break,
        }
        if Decimal("0") <= value < Decimal("1"):
            # 小数比例（0.7623）无法证明作者意图：既可能漏 %，也可能是
            # 刻意的小数比例值——文案只说口径不明确，不得给出换算结果。
            message = (
                f"百分比披露口径不明确：原文「{span}」中数值 {numeric_text} 为"
                " 0 与 1 之间的小数比例形态，既可能是缺少百分号的百分数，"
                "也可能是刻意使用的小数比例值，当前披露口径不明确，请核实"
                "原稿后统一为规范的百分比写法。"
            )
        else:
            message = (
                f"百分比单位缺失：原文「{span}」中「{predicate}{numeric_text}」"
                f"使用了明确的占比语义，但数值 {numeric_text} 后未披露"
                "“%/％/百分之”等百分比单位，当前口径不完整，请核实原稿并"
                "补充正确的百分比单位。"
            )
        return self._issue(
            message,
            location,
            evidence_text=f"发现位置：P{page}\n命中片段：{span}",
        )


ALL_COMMON_RULES: List[Rule] = [
    CMM001_ThreePublicNarrativeConsistency(),
    CMM002_TextAnomalyRule(),
    CMM003_TocCountConsistency(),
    CMM004_CodeMirrorConsistency(),
    CMM005_ComparativeNarrativeLogic(),
    CMM006_IncomeExpenseTrendConsistency(),
    CMM007_ZeroBaseTrendRecompute(),
    R33DisclosurePercentUnit(),
]
