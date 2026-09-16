from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .budget_rules import find_budget_anchors
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import Document, Issue, Rule, find_table_anchors
from src.services.document_profile_resolver import resolve_report_kind_from_path
from src.utils.narration import merge_soft_wrapped_lines as _merge_soft_wrapped_lines_shared

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
    则把"不知道"伪装成"知道了"。现在统一走画像解析器：只看文件名基名与
    正文首页，识别不到就是 ``"unknown"``。

    本函数的唯一调用方按 ``budget`` / 其它 二分支选择锚点集合，
    因此把兜底从 ``"final"`` 改成 ``"unknown"`` 不改变锚点选择结果。
    """
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


ALL_COMMON_RULES: List[Rule] = [
    CMM001_ThreePublicNarrativeConsistency(),
    CMM002_TextAnomalyRule(),
    CMM003_TocCountConsistency(),
    CMM004_CodeMirrorConsistency(),
    CMM005_ComparativeNarrativeLogic(),
    CMM006_IncomeExpenseTrendConsistency(),
]
