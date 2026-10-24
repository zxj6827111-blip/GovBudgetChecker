"""检查点清单：预算专用规则（全系统默认检查指标，2026-10-22）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| BUD-116 | C-03/C-04 | 财政拨款收支总表（T4）收支平衡、三列分别等于 T5/T6/T7 合计 |
| BUD-117 | C-07 | 收入预算总表（T2）各来源列加总 = 合计；T1 财政拨款收入 = 三类资金之和 |
| BUD-118 | C-01/C-02 | T2/T3/T5/T6/T7 类→款→项逐级加总、合计行 = 各类之和 |
| BUD-119 | B-10 | 表间同一科目金额一致（T2↔T3 按编码，T1↔T3、T4↔T5 按类） |
| BUD-120 | B-08/C-11 | “财政拨款支出主要内容”细化到项、分项加总 = 财政拨款支出预算 |
| BUD-121 | A-11/C-12 | 政府采购预算：总额/货物/工程/服务/中小企业预留要素与加总 |
| BUD-122 | A-12 | 绩效目标设置要素、项目经费情况说明七要素 |
| BUD-123 | A-10 | 预算编制说明要素（收入来源、财政拨款三类、增减原因、主要内容） |

表格取数复用 ``_get_budget_table_rows``（优先当前统一事实投影，旧材料回退物理
网格），表为“元”时换算为万元比较；判定阈值取 max(显示舍入包络, 0.01 万元)。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .amount_math import classify_amount_diff
from .budget_rules import (
    _get_budget_table_rows,
    _resolve_table_unit,
    find_budget_anchors,
)
from .checklist_identity_rules import body_scope
from .checklist_support import (
    compact,
    display_envelope,
    flat_span,
    issue_at,
    iter_pages,
    page_flat,
    subject_scope,
)
from .rule_outcome import RuleDeferred, RuleNotApplicable
from .rules_v33 import Document, Issue, Rule
from src.utils.narration import normalize_amount_punctuation

_FUNCTIONAL_TABLES = {
    "BUD_T2": "收入预算总表",
    "BUD_T3": "支出预算总表",
    "BUD_T5": "一般公共预算支出功能分类预算表",
    "BUD_T6": "政府性基金预算支出功能分类预算表",
    "BUD_T7": "国有资本经营预算支出功能分类预算表",
}


def _dec(value: Any) -> Optional[Decimal]:
    text = re.sub(r"[,\s，]", "", str(value or ""))
    if not text or text in {"-", "—", "－"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _to_wan(value: Optional[Decimal], unit: str) -> Optional[Decimal]:
    if value is None:
        return None
    if unit == "元":
        return value / Decimal(10000)
    if unit == "亿元":
        return value * Decimal(10000)
    return value


def _envelope(unit: str, count: int) -> Decimal:
    # 元表逐分精确，显示舍入可忽略；万元表两位小数各 ±0.005
    per = Decimal("0.0000005") if unit == "元" else Decimal("0.005")
    return per * (count + 1)


def _fmt(value: Decimal, unit: str) -> str:
    return f"{value:,.2f}{unit}" if unit != "万元" else f"{value:.2f}万元"


class _Grid:
    """预算功能分类类表格的列身份与数据行（编码分列“类/款/项”或合并编码）。"""

    def __init__(self, rows: Sequence[Sequence[Any]]) -> None:
        self.rows = [[str(c if c is not None else "").strip() for c in row] for row in rows or []]
        self.columns: Dict[str, int] = {}
        self.data: List[Dict[str, Any]] = []
        header_rows = []
        for index, row in enumerate(self.rows):
            if any(re.fullmatch(r"\d{3}", c) for c in row[:2]) or (row and row[0].replace(" ", "") in {"合计", "总计"}):
                break
            header_rows.append(row)
        width = max((len(r) for r in self.rows), default=0)
        for col in range(width):
            label = "".join(re.sub(r"\s+", "", r[col]) for r in header_rows if col < len(r))
            if label:
                self.columns.setdefault(label, col)
        for row in self.rows[len(header_rows):]:
            parsed = self._parse(row)
            if parsed:
                self.data.append(parsed)

    def col(self, *keywords: str) -> Optional[int]:
        for label, index in self.columns.items():
            if any(keyword in label for keyword in keywords) and "名称" not in label and "编码" not in label:
                return index
        return None

    @staticmethod
    def _parse(row: Sequence[str]) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        first = re.sub(r"\s+", "", row[0])
        if first in {"合计", "总计"}:
            return {"code": "total", "segments": [], "name": first, "row": row}
        segments: List[str] = []
        index = 0
        if re.fullmatch(r"\d{3}", first):
            segments.append(first)
            index = 1
            while index < len(row) and len(segments) < 3 and re.fullmatch(r"\d{2}", re.sub(r"\s+", "", row[index])):
                segments.append(re.sub(r"\s+", "", row[index]))
                index += 1
            while index < len(row) and row[index] == "":
                index += 1
        elif re.fullmatch(r"\d{5}|\d{7}", first):
            segments = [first[:3], *re.findall(r"\d{2}", first[3:])]
            index = 1
        else:
            return None
        name = row[index] if index < len(row) else ""
        return {"code": "".join(segments), "segments": segments, "name": re.sub(r"\s+", "", name), "row": row}

    def value(self, entry: Dict[str, Any], col: Optional[int]) -> Optional[Decimal]:
        if col is None or col >= len(entry["row"]):
            return None
        return _dec(entry["row"][col])


def _locate(doc: Document, start_page: int, entry: Dict[str, Any]) -> Optional[Tuple[int, str, int, int]]:
    """在表格页（及续页）原文中定位该行：编码分段 + 名称前几个字。"""
    texts = list(doc.page_texts or [])
    for page in range(max(1, start_page), min(len(texts), start_page + 3) + 1):
        text = str(texts[page - 1] or "")
        if entry["code"] == "total":
            hits = list(re.finditer(r"(?m)^\s*(?:合\s*计|总\s*计)\b.*$", text))
            if hits:
                return page, text, hits[-1].start(), hits[-1].end()
            continue
        pattern = r"\s*".join(map(re.escape, entry["segments"])) + r"\s*" + r"\s*".join(map(re.escape, entry["name"][:4]))
        match = re.search(pattern, text)
        if match:
            return page, text, match.start(), match.end()
    return None


def _table(doc: Document, anchors, key: str):
    try:
        rows, page = _get_budget_table_rows(doc, anchors, key)
    except RuleDeferred as exc:
        return None, None, None, str(exc.detail or exc)
    if not rows or not page:
        return None, None, None, f"{_FUNCTIONAL_TABLES.get(key, key)}未定位"
    unit = _resolve_table_unit(doc, page)
    if not unit:
        return None, None, None, f"{_FUNCTIONAL_TABLES.get(key, key)}金额单位未识别"
    return rows, page, unit, None


def _issue_row(rule: Rule, doc: Document, page: int, entry: Dict[str, Any], message: str, **location: Any) -> Issue:
    located = _locate(doc, page, entry)
    if located is None:
        text = str(doc.page_texts[page - 1] or "")
        first = next((ln for ln in text.split("\n") if ln.strip()), "")
        located = (page, text, max(text.find(first), 0), max(text.find(first), 0) + len(first))
    row_page, text, start, end = located
    return issue_at(rule, message, row_page, text, start, end, severity="error", **location)


# ---------------------------------------------------------------------------
# BUD-116 财政拨款收支总表（T4）
# ---------------------------------------------------------------------------


def _bilateral_totals(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[List[str]], Optional[int]]:
    for index in range(len(rows) - 1, -1, -1):
        row = [str(c or "").strip() for c in rows[index]]
        if any(re.sub(r"\s+", "", c) in {"收入总计", "支出总计", "总计"} for c in row):
            return row, index
    return None, None


class BUD116_FiscalTotalTableChecks(Rule):
    code, severity = "BUD-116", "error"
    desc = "财政拨款收支总表收支平衡、三列与功能分类表合计一致（清单 C-03/C-04）"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page, unit, error = _table(doc, anchors, "BUD_T4")
        if error:
            raise RuleDeferred(self.code, detail=error, unresolved_reasons=[error])
        total_row, _ = _bilateral_totals(rows)
        if total_row is None:
            raise RuleDeferred(self.code, detail="财政拨款收支总表未找到“收入总计/支出总计”行",
                               unresolved_reasons=["T4 总计行缺失"])
        cells = [re.sub(r"\s+", "", c) for c in total_row]
        grid = _Grid(rows)
        issues: List[Issue] = []
        unresolved: List[str] = []
        entry = {"code": "total", "segments": [], "name": "总计", "row": total_row}
        try:
            income_idx = next(i for i, c in enumerate(cells) if c in {"收入总计", "总计"})
            expense_idx = next(i for i, c in enumerate(cells) if c == "支出总计")
        except StopIteration:
            raise RuleDeferred(self.code, detail="财政拨款收支总表总计行缺少收入/支出标签",
                               unresolved_reasons=["T4 总计行标签缺失"])
        income = _to_wan(_dec(total_row[income_idx + 1]) if income_idx + 1 < len(total_row) else None, unit)
        expense = _to_wan(_dec(total_row[expense_idx + 1]) if expense_idx + 1 < len(total_row) else None, unit)
        if income is None or expense is None:
            if (income is None) != (expense is None):
                missing = "收入总计" if income is None else "支出总计"
                issues.append(_issue_row(self, doc, page, entry,
                                         f"财政拨款收支总表“{missing}”金额漏填（另一侧为"
                                         f"{_fmt(expense if income is None else income, '万元')}）。",
                                         table="BUD_T4", checklist_item="C-03"))
            else:
                unresolved.append("财政拨款收支总表收入/支出总计均未取到数值")
        else:
            level, diff = classify_amount_diff(income, expense, 1, envelope=_envelope(unit, 1))
            if level == "mismatch":
                issues.append(_issue_row(self, doc, page, entry,
                                         f"财政拨款收支总表收支不平衡：收入总计{income:.2f}万元，支出总计{expense:.2f}万元，"
                                         f"相差{diff:.2f}万元。", table="BUD_T4", checklist_item="C-03"))
        # 支出总计行的“一般公共预算 / 政府性基金预算 / 国有资本经营预算”三列 ↔ T5/T6/T7 合计
        for column_key, keywords, target in (
            ("一般公共预算", ("一般公共预算",), "BUD_T5"),
            ("政府性基金预算", ("政府性基金",), "BUD_T6"),
            ("国有资本经营预算", ("国有资本经营",), "BUD_T7"),
        ):
            col = grid.col(*keywords)
            if col is None or col <= expense_idx:
                continue
            t4_value = _to_wan(_dec(total_row[col]) if col < len(total_row) else None, unit) or Decimal(0)
            t_rows, t_page, t_unit, t_error = _table(doc, anchors, target)
            if t_error:
                if t4_value != 0:
                    unresolved.append(f"{_FUNCTIONAL_TABLES[target]}：{t_error}")
                continue
            t_grid = _Grid(t_rows)
            t_total = next((e for e in t_grid.data if e["code"] == "total"), None)
            t_col = t_grid.col("合计")
            t_value = _to_wan(t_grid.value(t_total, t_col), t_unit) if t_total else None
            if t_total is None:
                if t4_value != 0:
                    unresolved.append(f"{_FUNCTIONAL_TABLES[target]}未找到合计行")
                continue
            t_value = t_value or Decimal(0)
            level, diff = classify_amount_diff(t4_value, t_value, 1, envelope=_envelope(unit, 1))
            if level == "mismatch":
                issues.append(_issue_row(self, doc, page, entry,
                                         f"财政拨款收支总表“{column_key}”列支出总计{t4_value:.2f}万元，与"
                                         f"《{_FUNCTIONAL_TABLES[target]}》合计{t_value:.2f}万元不一致，相差{diff:.2f}万元。",
                                         table="BUD_T4", checklist_item="C-04", related_table=target))
        if unresolved:
            raise RuleDeferred(self.code, detail="；".join(unresolved), partial_issues=issues,
                               unresolved_reasons=unresolved)
        return issues


# ---------------------------------------------------------------------------
# BUD-117 收入预算总表来源加总
# ---------------------------------------------------------------------------

_INCOME_SOURCE_KEYWORDS = ("财政拨款", "事业收入", "事业单位经营收入", "经营收入", "其他收入", "上年结转",
                           "上级补助", "附属单位", "用事业基金弥补", "非财政拨款结余")


class BUD117_IncomeSourceSum(Rule):
    code, severity = "BUD-117", "error"
    desc = "收入预算总表各来源列加总等于合计、财政拨款收入等于三类资金之和（清单 C-07）"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page, unit, error = _table(doc, anchors, "BUD_T2")
        if error:
            raise RuleDeferred(self.code, detail=error, unresolved_reasons=[error])
        grid = _Grid(rows)
        total_col = grid.col("合计")
        sources = sorted({index for label, index in grid.columns.items()
                          if index != total_col and any(k in label for k in _INCOME_SOURCE_KEYWORDS)
                          and "名称" not in label and "编码" not in label})
        if total_col is None or not sources:
            raise RuleDeferred(self.code, detail="收入预算总表未识别到合计列或收入来源列",
                               unresolved_reasons=["T2 列身份未识别"])
        issues: List[Issue] = []
        for entry in grid.data:
            total = _to_wan(grid.value(entry, total_col), unit)
            parts = [_to_wan(grid.value(entry, col), unit) for col in sources]
            if total is None and not any(p for p in parts if p):
                continue
            subtotal = sum((p for p in parts if p is not None), Decimal(0))
            level, diff = classify_amount_diff(total or Decimal(0), subtotal, len(sources),
                                               envelope=_envelope(unit, len(sources)))
            if level != "mismatch":
                continue
            label = "合计行" if entry["code"] == "total" else f"{' '.join(entry['segments'])} {entry['name']}"
            issues.append(_issue_row(
                self, doc, page, entry,
                f"收入预算总表“{label}”行合计{(total or 0):.2f}万元，与各收入来源之和{subtotal:.2f}万元不一致，"
                f"相差{diff:.2f}万元。", table="BUD_T2", checklist_item="C-07", code=entry["code"],
            ))
        issues.extend(self._t1_fiscal_split(doc, anchors))
        return issues

    def _t1_fiscal_split(self, doc: Document, anchors) -> List[Issue]:
        """T1“财政拨款收入”= 一般公共预算资金 + 政府性基金 + 国有资本经营预算（子项全空则不判）。"""
        rows, page, unit, error = _table(doc, anchors, "BUD_T1")
        if error:
            return []
        values: Dict[str, Optional[Decimal]] = {}
        for row in rows:
            cells = [str(c or "").strip() for c in row]
            if not cells:
                continue
            label = re.sub(r"\s+", "", re.sub(r"^\s*(?:\d+\s*[.、．]|[一二三四五六七八九十]+、)", "", cells[0]))
            amount = _to_wan(_dec(cells[1]) if len(cells) > 1 else None, unit)
            if label.startswith("财政拨款收入"):
                values["total"] = amount
            elif label.startswith("一般公共预算"):
                values["general"] = amount
            elif label.startswith("政府性基金"):
                values["fund"] = amount
            elif label.startswith("国有资本经营"):
                values["state"] = amount
        parts = [values.get(k) for k in ("general", "fund", "state")]
        if values.get("total") is None or not any(p for p in parts if p):
            return []
        subtotal = sum((p for p in parts if p is not None), Decimal(0))
        level, diff = classify_amount_diff(values["total"], subtotal, 3, envelope=_envelope(unit, 3))
        if level != "mismatch":
            return []
        text = str(doc.page_texts[page - 1] or "")
        pos = text.find("财政拨款收入")
        return [issue_at(
            self, f"财务收支预算总表“财政拨款收入”{values['total']:.2f}万元，与一般公共预算资金、政府性基金、"
            f"国有资本经营预算三项之和{subtotal:.2f}万元不一致，相差{diff:.2f}万元。",
            page, text, max(pos, 0), max(pos, 0) + 6, severity="error", table="BUD_T1", checklist_item="C-07",
        )]


# ---------------------------------------------------------------------------
# BUD-118 类→款→项层级加总与合计行
# ---------------------------------------------------------------------------


class BUD118_FunctionalHierarchySum(Rule):
    code, severity = "BUD-118", "error"
    desc = "预算功能分类表类→款→项逐级加总、合计行等于各类之和（清单 C-01/C-02）"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved: List[str] = []
        checked = 0
        for key in ("BUD_T2", "BUD_T3", "BUD_T5", "BUD_T6", "BUD_T7"):
            rows, page, unit, error = _table(doc, anchors, key)
            if error:
                if key in ("BUD_T2", "BUD_T3", "BUD_T5"):
                    unresolved.append(f"{_FUNCTIONAL_TABLES[key]}：{error}")
                continue
            grid = _Grid(rows)
            entries = [e for e in grid.data if e["code"] != "total"]
            if not entries:
                continue
            checked += 1
            # 百分比/增减率列不满足层级加总，只取金额列
            value_cols = [index for label, index in grid.columns.items()
                          if "名称" not in label and "编码" not in label and label not in {"项目", "类", "款", "项"}
                          and not re.search(r"%|％|率|比例", label)
                          and any(_dec(e["row"][index]) is not None for e in grid.data if index < len(e["row"]))]
            codes = {e["code"]: e for e in entries}
            total_entry = next((e for e in grid.data if e["code"] == "total"), None)
            for col in sorted(set(value_cols)):
                header = next(label for label, index in grid.columns.items() if index == col)
                relations: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
                for code, entry in codes.items():
                    if len(code) in (3, 5):
                        children = [c for k, c in codes.items() if len(k) == len(code) + 2 and k.startswith(code)]
                        if children:
                            relations.append((entry, children))
                if total_entry is not None:
                    relations.append((total_entry, [e for k, e in codes.items() if len(k) == 3]))
                for parent, children in relations:
                    parent_value = _to_wan(grid.value(parent, col), unit)
                    child_values = [_to_wan(grid.value(c, col), unit) for c in children]
                    if parent_value is None and not any(v for v in child_values if v):
                        continue
                    subtotal = sum((v for v in child_values if v is not None), Decimal(0))
                    level, diff = classify_amount_diff(parent_value or Decimal(0), subtotal, len(children),
                                                       envelope=_envelope(unit, len(children)))
                    if level != "mismatch":
                        continue
                    if parent["code"] == "total":
                        message = (f"《{_FUNCTIONAL_TABLES[key]}》合计行“{header}”{(parent_value or 0):.2f}万元，"
                                   f"与各类科目之和{subtotal:.2f}万元不一致，相差{diff:.2f}万元。")
                        item = "C-01"
                    else:
                        message = (f"《{_FUNCTIONAL_TABLES[key]}》“{' '.join(parent['segments'])} {parent['name']}”"
                                   f"“{header}”{(parent_value or 0):.2f}万元，与其下级科目之和{subtotal:.2f}万元不一致，"
                                   f"相差{diff:.2f}万元。")
                        item = "C-02"
                    issues.append(_issue_row(self, doc, page, parent, message, table=key, checklist_item=item,
                                             code=parent["code"], column=header))
        if not checked:
            raise RuleDeferred(self.code, detail="；".join(unresolved) or "预算功能分类表均未定位",
                               unresolved_reasons=unresolved or ["预算功能分类表均未定位"])
        if unresolved:
            raise RuleDeferred(self.code, detail="；".join(unresolved), partial_issues=issues,
                               unresolved_reasons=unresolved)
        return issues


# ---------------------------------------------------------------------------
# BUD-119 表间同一科目金额
# ---------------------------------------------------------------------------


def _class_rows(rows: Sequence[Sequence[Any]], side: str) -> Dict[str, Tuple[Optional[Decimal], List[str]]]:
    """双侧总表（T1/T4）支出侧“一、一般公共服务支出”类行：类名 → (第一列金额, 行)。"""
    out: Dict[str, Tuple[Optional[Decimal], List[str]]] = {}
    for row in rows:
        cells = [str(c or "").strip() for c in row]
        for index, cell in enumerate(cells):
            label = re.sub(r"\s+", "", cell)
            match = re.fullmatch(r"[一二三四五六七八九十]+、(.+支出)", label)
            if match and index + 1 < len(cells):
                out.setdefault(match.group(1), (_dec(cells[index + 1]), cells))
    return out


class BUD119_CrossTableSubjectAmount(Rule):
    code, severity = "BUD-119", "error"
    desc = "预算表间同一科目金额一致（清单 B-10）"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved: List[str] = []
        t2 = _table(doc, anchors, "BUD_T2")
        t3 = _table(doc, anchors, "BUD_T3")
        if t2[3] or t3[3]:
            unresolved.extend(e for e in (t2[3], t3[3]) if e)
        else:
            g2, g3 = _Grid(t2[0]), _Grid(t3[0])
            c2, c3 = g2.col("合计"), g3.col("合计")
            v3 = {e["code"]: e for e in g3.data if e["code"] != "total"}
            for entry in g2.data:
                other = v3.get(entry["code"])
                if entry["code"] == "total" or other is None:
                    continue
                a = _to_wan(g2.value(entry, c2), t2[2])
                b = _to_wan(g3.value(other, c3), t3[2])
                if a is None and b is None:
                    continue
                level, diff = classify_amount_diff(a or Decimal(0), b or Decimal(0), 1,
                                                   envelope=_envelope(t2[2], 1))
                if level == "mismatch":
                    issues.append(_issue_row(
                        self, doc, t2[1], entry,
                        f"同一科目“{' '.join(entry['segments'])} {entry['name']}”在收入预算总表合计{(a or 0):.2f}万元，"
                        f"支出预算总表合计{(b or 0):.2f}万元，两表不一致（相差{diff:.2f}万元）。",
                        table="BUD_T2", related_table="BUD_T3", checklist_item="B-10", code=entry["code"],
                    ))
            # T1 支出侧类行 ↔ T3 类级合计
            t1 = _table(doc, anchors, "BUD_T1")
            if not t1[3]:
                classes = _class_rows(t1[0], "expense")
                by_name = {e["name"]: e for e in g3.data if len(e["code"]) == 3}
                for name, (value, row) in classes.items():
                    target = by_name.get(name)
                    if target is None:
                        continue
                    a = _to_wan(value, t1[2])
                    b = _to_wan(g3.value(target, c3), t3[2])
                    if a is None or b is None:
                        continue
                    level, diff = classify_amount_diff(a, b, 1, envelope=_envelope(t1[2], 1))
                    if level == "mismatch":
                        text = str(doc.page_texts[t1[1] - 1] or "")
                        pos = text.find(name)
                        issues.append(issue_at(
                            self, f"同一科目“{name}”在财务收支预算总表为{a:.2f}万元，支出预算总表为{b:.2f}万元，"
                            f"两表不一致（相差{diff:.2f}万元）。", t1[1], text, max(pos, 0), max(pos, 0) + len(name) + 12,
                            severity="error", table="BUD_T1", related_table="BUD_T3", checklist_item="B-10",
                        ))
        if unresolved:
            raise RuleDeferred(self.code, detail="；".join(unresolved), partial_issues=issues,
                               unresolved_reasons=unresolved)
        return issues


# ---------------------------------------------------------------------------
# 预算编制说明范围
# ---------------------------------------------------------------------------


def explanation_pages(doc: Document) -> List[Tuple[int, str]]:
    """表格前的说明页（预算编制说明、其他相关情况说明、项目经费情况说明都在表前或表后）。"""
    return [(page, text) for page, text in iter_pages(doc)]


def _section_flat(doc: Document, start_re: str, stop_re: str) -> List[Tuple[int, str, str, List[int], int, int]]:
    """按页返回 (页, 原文, 平铺文本, 偏移表, 段起, 段止)：从命中 start_re 起到 stop_re 止。"""
    out = []
    active = False
    for page, text in iter_pages(doc):
        flat, positions = page_flat(doc, page, text)
        flat = normalize_amount_punctuation(flat)
        begin = 0
        if not active:
            match = re.search(start_re, flat)
            if not match:
                continue
            active = True
            begin = match.start()
        stop = re.search(stop_re, flat[begin + 1:])
        end = begin + 1 + stop.start() if stop else len(flat)
        out.append((page, text, flat, positions, begin, end))
        if stop:
            break
    return out


# ---------------------------------------------------------------------------
# BUD-120 财政拨款支出主要内容
# ---------------------------------------------------------------------------

_MAIN_CONTENT_START = r"财政拨款支出主要内容"
_MAIN_CONTENT_STOP = r"(?:[五六七八九]、|第[二三四]部分|[0-9]{4}年(?:部门|单位)?财务收支预算总表|其他相关情况说明)"
_FISCAL_TOTAL_RE = re.compile(r"财政拨款支出预算(?:为|共)?(\d[\d,]*(?:\.\d+)?)万元")
_ENTRY_LINE_RE = re.compile(r"(?:^|(?<=[。；;：:]))(\d{1,2})[、.．](?=\D)")


class BUD120_MainContentItemization(Rule):
    code, severity = "BUD-120", "warn"
    desc = "财政拨款支出主要内容细化到项、分项加总等于财政拨款支出预算（清单 B-08/C-11）"

    def apply(self, doc: Document) -> List[Issue]:
        blocks = _section_flat(doc, _MAIN_CONTENT_START, _MAIN_CONTENT_STOP)
        if not blocks:
            raise RuleNotApplicable(self.code, detail="未找到“财政拨款支出主要内容”段落（缺失由 BUD-123 报告）")
        issues: List[Issue] = []
        amounts: List[Decimal] = []
        raws: List[str] = []
        for page, text, flat, positions, begin, end in blocks:
            segment = flat[begin:end]
            starts = [m.start() for m in _ENTRY_LINE_RE.finditer(segment)]
            for index, start in enumerate(starts):
                stop = starts[index + 1] if index + 1 < len(starts) else len(segment)
                item = segment[start:stop]
                amount = re.search(r"[”\"]?[:：]?(\d[\d,]*(?:\.\d+)?)\s*万\s*元", item)
                if not amount:
                    continue
                value = Decimal(amount.group(1).replace(",", ""))
                amounts.append(value)
                raws.append(amount.group(1))
                head = item[:amount.start()]
                if re.search(r"[（(]\s*类\s*[)）]|[（(]\s*\d{3}\s*类\s*[)）]", head) and not re.search(r"[（(]\s*\d{0,2}\s*项\s*[)）]", head):
                    s, e = flat_span(positions, begin + start, begin + start + amount.end())
                    issues.append(issue_at(
                        self, f"“财政拨款支出主要内容”未细化到项：第{item[:2].strip('、.．')}条只列至类/款级科目。",
                        page, text, s, e, severity="warn", checklist_item="B-08",
                    ))
        total, total_raw = None, ""
        for page, text in iter_pages(doc):
            match = _FISCAL_TOTAL_RE.search(normalize_amount_punctuation(compact(text)))
            if match:
                total, total_raw = Decimal(match.group(1).replace(",", "")), match.group(1)
                break
        if total is not None and len(amounts) >= 2:
            subtotal = sum(amounts, Decimal(0))
            tolerance = display_envelope([total_raw, *raws])
            if abs(subtotal - total) > tolerance:
                page, text, flat, positions, begin, end = blocks[0]
                s, e = flat_span(positions, begin, min(end, begin + 14))
                issues.append(issue_at(
                    self, f"“财政拨款支出主要内容”{len(amounts)}项金额合计{subtotal}万元，与财政拨款支出预算{total}万元"
                    f"{'不一致（列示不全）' if subtotal < total else '不一致'}，相差{abs(subtotal - total)}万元。",
                    page, text, s, e, severity="warn", checklist_item="C-11",
                ))
        return issues


# ---------------------------------------------------------------------------
# BUD-121 政府采购预算
# ---------------------------------------------------------------------------

_PROC_START = r"政府采购预算情况|政府采购安排情况|政府采购预算"
_PROC_STOP = r"(?:[四五六七八九]、|绩效目标|国有资产)"


class BUD121_ProcurementBudget(Rule):
    code, severity = "BUD-121", "warn"
    desc = "政府采购预算要素齐全、分项加总等于总额（清单 A-11/C-12）"

    def apply(self, doc: Document) -> List[Issue]:
        blocks = _section_flat(doc, _PROC_START, _PROC_STOP)
        if not blocks:
            raise RuleNotApplicable(self.code, detail="未找到政府采购预算段落（缺失由 CMM-023 报告）")
        page, text, flat, positions, begin, end = blocks[0]
        segment = "".join(b[2][b[4]:b[5]] for b in blocks)
        s, e = flat_span(positions, begin, min(end, begin + 16))
        scope = body_scope(doc) or subject_scope(doc)
        issues: List[Issue] = []
        if re.search(r"未安排政府采购|无政府采购预算|没有政府采购", segment):
            return []
        total = re.search(r"政府采购预算(?:总额)?(?:为|共)?(\d[\d,]*(?:\.\d+)?)万元", segment)
        if not total:
            issues.append(issue_at(self, "政府采购预算情况未披露采购预算总额，也未写明“未安排政府采购预算”。",
                                   page, text, s, e, severity="warn", checklist_item="A-11"))
            return issues
        parts = {}
        for label in ("货物", "工程", "服务"):
            match = re.search(rf"(?:政府采购)?{label}(?:类)?(?:采购)?预算(?:为)?(\d[\d,]*(?:\.\d+)?)万元", segment)
            if match:
                parts[label] = Decimal(match.group(1).replace(",", ""))
        missing = [label for label in ("货物", "工程", "服务") if label not in parts]
        if missing:
            issues.append(issue_at(self, f"政府采购预算未分别列示{'、'.join(missing)}预算金额。",
                                   page, text, s, e, severity="warn", checklist_item="A-11"))
        total_value = Decimal(total.group(1).replace(",", ""))
        if len(parts) == 3:
            subtotal = sum(parts.values(), Decimal(0))
            if abs(subtotal - total_value) > Decimal("0.03"):
                issues.append(issue_at(
                    self, f"政府采购预算货物、工程、服务三项合计{subtotal}万元，与采购预算总额{total_value}万元不一致。",
                    page, text, s, e, severity="warn", checklist_item="C-12",
                ))
        reserve = re.search(r"面向中小企业预留(?:政府采购)?(?:项目)?(?:预算)?(?:金额)?(?:为)?(\d[\d,]*(?:\.\d+)?)万元", segment)
        small = re.search(r"(?:预留给小型和微型企业|小微企业预留)[^。；]{0,20}?(\d[\d,]*(?:\.\d+)?)万元", segment)
        if scope == "department" and total_value > 0:
            if not reserve:
                issues.append(issue_at(self, "部门预算政府采购情况未披露“面向中小企业预留政府采购项目预算金额”。",
                                       page, text, s, e, severity="warn", checklist_item="A-11"))
            elif not small:
                issues.append(issue_at(self, "部门预算政府采购情况未披露“预留给小型和微型企业的政府采购项目预算”。",
                                       page, text, s, e, severity="warn", checklist_item="A-11"))
        if reserve and Decimal(reserve.group(1).replace(",", "")) > total_value + Decimal("0.01"):
            issues.append(issue_at(self, f"面向中小企业预留金额{reserve.group(1)}万元超过政府采购预算总额{total_value}万元。",
                                   page, text, s, e, severity="warn", checklist_item="C-12"))
        if reserve and small and Decimal(small.group(1).replace(",", "")) > Decimal(reserve.group(1).replace(",", "")) + Decimal("0.01"):
            issues.append(issue_at(self, f"预留给小型和微型企业的金额{small.group(1)}万元超过面向中小企业预留金额{reserve.group(1)}万元"
                                   "（小微企业预留属于中小企业预留的一部分）。",
                                   page, text, s, e, severity="warn", checklist_item="C-12"))
        return issues


# ---------------------------------------------------------------------------
# BUD-122 绩效目标设置与项目经费情况说明要素
# ---------------------------------------------------------------------------

_PROJECT_ELEMENTS = ("项目概述", "立项依据", "实施主体", "实施方案", "实施周期", "年度预算安排", "绩效目标")


class BUD122_PerformanceElements(Rule):
    code, severity = "BUD-122", "warn"
    desc = "绩效目标设置情况要素与项目经费情况说明七要素齐全（清单 A-12）"

    def apply(self, doc: Document) -> List[Issue]:
        scope = body_scope(doc) or subject_scope(doc)
        issues: List[Issue] = []
        blocks = _section_flat(doc, r"绩效目标设置情况|预算绩效管理情况|绩效目标设置", r"(?:[五六七八九十]、|国有资产|项目经费情况说明)")
        if blocks:
            page, text, flat, positions, begin, end = blocks[0]
            segment = "".join(b[2][b[4]:b[5]] for b in blocks)
            s, e = flat_span(positions, begin, min(end, begin + 12))
            missing = []
            if not re.search(r"项目\d+个|\d+个项目", segment):
                missing.append("编报绩效目标的项目数")
            if not re.search(r"\d[\d,]*(?:\.\d+)?万元", segment):
                missing.append("涉及项目预算资金")
            if scope == "department" and not re.search(r"\d+(?:家|个)(?:预算)?单位|单位\d+(?:家|个)", segment):
                missing.append("开展编报的预算单位数")
            if missing:
                issues.append(issue_at(self, f"绩效目标设置情况未写明{'、'.join(missing)}。",
                                       page, text, s, e, severity="warn", checklist_item="A-12"))
        if scope == "department":
            proj = _section_flat(doc, r"项目经费情况说明", r"$^")
            if proj:
                segment = "".join(b[2][b[4]:b[5]] for b in proj)
                missing = [name for name in _PROJECT_ELEMENTS if name not in segment]
                if missing:
                    page, text, flat, positions, begin, end = proj[0]
                    s, e = flat_span(positions, begin, min(end, begin + 10))
                    issues.append(issue_at(self, f"项目经费情况说明缺少要素：{'、'.join(missing)}（应含项目概述、立项依据、"
                                           "实施主体、实施方案、实施周期、年度预算安排、绩效目标七项）。",
                                           page, text, s, e, severity="warn", checklist_item="A-12"))
        if not blocks and scope != "department":
            raise RuleNotApplicable(self.code, detail="未找到绩效目标设置段落（缺失由 CMM-023 报告）")
        return issues


# ---------------------------------------------------------------------------
# BUD-123 预算编制说明要素
# ---------------------------------------------------------------------------


class BUD123_ExplanationElements(Rule):
    code, severity = "BUD-123", "warn"
    desc = "预算编制说明要素齐全（收入来源、财政拨款三类、增减原因、主要内容，清单 A-10）"

    def apply(self, doc: Document) -> List[Issue]:
        blocks = _section_flat(doc, r"预算编制说明|预算情况说明|收支总体情况",
                               r"(?:[0-9]{4}年(?:部门|单位)?财务收支预算总表|其他相关情况说明|“三公”经费预算情况说明|第三部分)")
        if not blocks:
            raise RuleNotApplicable(self.code, detail="未找到预算编制说明（缺失由 BUD-001 报告）")
        page, text, flat, positions, begin, end = blocks[0]
        segment = "".join(b[2][b[4]:b[5]] for b in blocks)
        s, e = flat_span(positions, begin, min(end, begin + 10))
        missing = []
        if not re.search(r"收入预算|收入包括|收入合计", segment):
            missing.append("收入预算总额及来源分项")
        elif not re.search(r"财政拨款收入|一般公共预算(?:拨款)?收入", segment):
            missing.append("财政拨款收入等收入来源分项")
        if not re.search(r"一般公共预算", segment):
            missing.append("财政拨款支出中一般公共预算等分项")
        if re.search(r"比20\d{2}年(?:预算)?(?:执行数)?(?:增加|减少)", segment) and not re.search(r"原因", segment):
            missing.append("财政拨款收入支出增减的主要原因")
        if not re.search(r"主要内容|具体安排情况|支出主要用于", segment):
            missing.append("“财政拨款支出主要内容”")
        if not missing:
            return []
        return [issue_at(self, f"预算编制说明要素不全：未见{'、'.join(missing)}。",
                         page, text, s, e, severity="warn", checklist_item="A-10")]


CHECKLIST_BUDGET_RULES: List[Rule] = [
    BUD116_FiscalTotalTableChecks(),
    BUD117_IncomeSourceSum(),
    BUD118_FunctionalHierarchySum(),
    BUD119_CrossTableSubjectAmount(),
    BUD120_MainContentItemization(),
    BUD121_ProcurementBudget(),
    BUD122_PerformanceElements(),
    BUD123_ExplanationElements(),
]


def _register() -> None:
    from . import budget_rules

    known = {getattr(rule, "code", None) for rule in budget_rules.ALL_BUDGET_RULES}
    budget_rules.ALL_BUDGET_RULES.extend(rule for rule in CHECKLIST_BUDGET_RULES if rule.code not in known)


_register()
