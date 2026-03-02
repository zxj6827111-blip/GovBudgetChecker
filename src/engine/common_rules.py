from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .budget_rules import find_budget_anchors
from .rules_v33 import Document, Issue, Rule, find_table_anchors

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
    path_text = str(getattr(doc, "path", "") or "").lower()
    if "budget" in path_text or "\u9884\u7b97" in path_text:
        return "budget"
    if "final" in path_text or "\u51b3\u7b97" in path_text:
        return "final"

    texts = _page_texts(doc)
    head = "\n".join(texts[:3])
    if "\u9884\u7b97" in head:
        return "budget"
    if "\u51b3\u7b97" in head:
        return "final"
    return "final"


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


class CMM001_ThreePublicNarrativeConsistency(Rule):
    code, severity = "CMM-001", "warn"
    desc = "\u4e09\u516c\u8868\u4e0e\u60c5\u51b5\u8bf4\u660e\u4e00\u81f4\u6027\uff08\u9884/\u51b3\u7b97\u901a\u7528\uff09"

    def apply(self, doc: Document) -> List[Issue]:
        texts = _page_texts(doc)
        if not texts:
            return []

        full_text = "\n".join(texts)
        narrative = _extract_three_public_narrative(full_text)
        if all(value is None for value in narrative.values()):
            return []

        table_page, table_values = _find_three_public_table(doc)
        if table_values is None:
            return []

        issues: List[Issue] = []
        labels = {
            "total": "\u4e09\u516c\u5408\u8ba1",
            "abroad": "\u56e0\u516c\u51fa\u56fd\uff08\u5883\uff09\u8d39",
            "reception": "\u516c\u52a1\u63a5\u5f85\u8d39",
            "car_total": "\u516c\u52a1\u7528\u8f66\u5c0f\u8ba1",
            "car_buy": "\u516c\u52a1\u7528\u8f66\u8d2d\u7f6e\u8d39",
            "car_run": "\u516c\u52a1\u7528\u8f66\u8fd0\u884c\u8d39",
        }

        for key in ("total", "abroad", "reception", "car_total", "car_buy", "car_run"):
            nar = narrative.get(key)
            tab = table_values.get(key)
            if nar is None or tab is None:
                continue
            if _close(nar, tab):
                continue
            label = labels[key]
            severity = "error" if key == "car_run" else "warn"
            message = (
                f"\u4e09\u516c\u8868\u4e0e\u8bf4\u660e\u4e0d\u4e00\u81f4\uff1a{label}\u8bf4\u660e={nar:.2f}\u4e07\u5143\uff0c\u8868\u5185={tab:.2f}\u4e07\u5143"
            )
            if key == "car_run":
                message += "\uff1b\u5efa\u8bae\uff1a\u8bf7\u7edf\u4e00\u201c\u516c\u52a1\u7528\u8f66\u8fd0\u884c\u8d39\u201d\u5728\u8868\u683c\u4e0e\u60c5\u51b5\u8bf4\u660e\u4e2d\u7684\u91d1\u989d\u53e3\u5f84"
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
                    "\u4e09\u516c\u6587\u5b57\u8bf4\u660e\u5185\u90e8\u52fe\u7a3d\u4e0d\u4e00\u81f4\uff1a\u516c\u8f66\u5c0f\u8ba1\u2260\u8d2d\u7f6e\u8d39+\u8fd0\u884c\u8d39",
                    {"page": table_page or 1},
                    "warn",
                    evidence_text=(
                        f"car_total={nar_car_total}, car_buy={nar_car_buy}, car_run={nar_car_run}"
                    ),
                )
            )

        return issues


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
            return []

        common = sorted(set(income).intersection(expense))
        if len(common) < 2:
            return []

        diffs = [
            code for code in common if abs(income[code] - expense[code]) > 1e-6
        ]
        only_income = sorted(set(income) - set(expense))
        only_expense = sorted(set(expense) - set(income))

        issues: List[Issue] = []
        if diffs:
            sample = ", ".join(
                f"{code}:{income[code]}!={expense[code]}" for code in diffs[:3]
            )
            issues.append(
                self._issue(
                    f"\u7c7b\u6b3e\u9879\u91d1\u989d\u4e0d\u4e00\u81f4\uff0c\u5171{len(diffs)}\u9879",
                    {"page": 1},
                    "warn",
                    evidence_text=sample,
                )
            )

        if only_income or only_expense:
            sample_income = ",".join(only_income[:3]) if only_income else "-"
            sample_expense = ",".join(only_expense[:3]) if only_expense else "-"
            issues.append(
                self._issue(
                    "\u6536\u5165/\u652f\u51fa\u603b\u8868\u7c7b\u6b3e\u9879\u7f16\u7801\u96c6\u4e0d\u5b8c\u5168\u4e00\u81f4",
                    {"page": 1},
                    "warn",
                    evidence_text=f"income_only={sample_income}; expense_only={sample_expense}",
                )
            )

        return issues


_ZERO_INCREASE_PATTERN = re.compile(
    r"([一-龥]{0,24}?拨款支出预算)0(?:\.0+)?万元，比20\d{2}年预算增加([0-9][0-9,]*\.?[0-9]*)万元"
)
_ABNORMAL_DELTA_WORDING_PATTERN = re.compile(r"(增加|减少)持平")


class CMM005_ComparativeNarrativeLogic(Rule):
    code, severity = "CMM-005", "warn"
    desc = "同比叙述逻辑异常检查（预/决算通用）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []

        for page_idx, page_text in enumerate(_page_texts(doc), start=1):
            flat_text = _compact_text(page_text)
            if not flat_text:
                continue

            seen_spans: set[Tuple[int, int, str]] = set()

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
                severity = (
                    "error"
                    if _close(float(income_delta["amount"]), float(expense_delta["amount"]))
                    else "warn"
                )
                issues.append(
                    self._issue(
                        "收入/支出同比方向矛盾：同页同时出现“收入减少（或增加）”与“支出增加（或减少）”",
                        {"page": page_idx, "pos": start},
                        severity,
                        evidence_text=_snippet(flat_text, start, end),
                    )
                )

            if summary_match and income_delta and expense_delta:
                summary_direction = summary_match.group("dir")
                if (
                    summary_direction != str(income_delta["direction"])
                    or summary_direction != str(expense_delta["direction"])
                ):
                    issues.append(
                        self._issue(
                            f"口径描述矛盾：文中写“财政拨款收入支出{summary_direction}”，但收入/支出同比方向不一致",
                            {"page": page_idx, "pos": summary_match.start()},
                            "warn",
                            evidence_text=_snippet(
                                flat_text, summary_match.start(), summary_match.end()
                            ),
                        )
                    )

        return issues


ALL_COMMON_RULES: List[Rule] = [
    CMM001_ThreePublicNarrativeConsistency(),
    CMM002_TextAnomalyRule(),
    CMM003_TocCountConsistency(),
    CMM004_CodeMirrorConsistency(),
    CMM005_ComparativeNarrativeLogic(),
    CMM006_IncomeExpenseTrendConsistency(),
]
