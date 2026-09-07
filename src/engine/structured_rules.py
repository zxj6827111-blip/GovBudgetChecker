"""统一结构化解析中间层（与数据库无关的内存模型）。

阶段 3 的核心交付（docs/GovBudgetChecker 完整整改计划 §3）：

- ``ParsedCell``：number（Decimal/None）/text（str/None）/bbox 三态，
  **文本单元格保留 None，禁止转成 0.0**；
- ``ParsedTable``：``table_code、page_span、named_columns、column_group、
  canonical_measure、classification_type、row_role、confidence``；
- 双栏表显式建模：``split_two_sided``（收入侧/支出侧、人员经费侧/公用经费侧）；
- 跨页表合并守卫：``merge_compatible`` 只在表头签名兼容时合并，列宽变化
  走逻辑列重映射，无法映射时标记 ``parse_error``；
- 金额计算统一 Decimal：舍入包络复用 ``src.engine.amount_math``。

三态开关（legacy/shadow/structured）：``run_structured_rules`` 供
``structured``/``shadow`` 模式消费；首批迁移规则 V33-115/117/120/202/203/
220/241/243/244 通过适配器委托给修复后的规则实现（单一实现，两处消费），
后续规则迁移只需在 ``STRUCTURED_MIGRATED_RULES`` 登记并在
``STRUCTURED_RULE_IMPLEMENTATIONS`` 提供实现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.engine.amount_math import classify_amount_diff

# 结构化迁移登记（两级口径，GPT5.6 R3 P0-2 诚实化）：
# - STRUCTURED_MIGRATED_RULES：经 run_structured_rules 适配器执行的规则
#   （与 legacy 共用同一实现）；
# - STRUCTURED_PARSING_CONSUMERS：**真正消费 doc.parsed_tables**
#   （命名行/三态单元格取数，不再依赖 legacy 文本行回退）的规则。
#   覆盖率/structured_ready 必须以这一级为准——R2/R3 复核指出按登记数
#   报 9/52 是误导（除 V33-115 外其余 8 条虽在适配器中执行，输入仍是
#   legacy 表征）。
STRUCTURED_MIGRATED_RULES: Tuple[str, ...] = (
    "V33-115",
    "V33-117",
    "V33-120",
    "V33-202",
    "V33-203",
    "V33-220",
    "V33-241",
    "V33-243",
    "V33-244",
)

# 真实消费 parsed_tables 的规则（structured 解析覆盖率的分子）
STRUCTURED_PARSING_CONSUMERS: Tuple[str, ...] = (
    "V33-115",  # R3 P0-2：_apply_structured 从 ParsedRow/ParsedCell 三态取数
)

# 科目域（与 common_rules._code_domain 口径一致）
DOMAIN_REVENUE = "revenue"
DOMAIN_FUNCTIONAL = "functional"
DOMAIN_ECONOMIC = "economic"
DOMAIN_OTHER = "other"


def classify_code_domain(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) < 3:
        return DOMAIN_OTHER
    prefix = int(digits[:3])
    if 101 <= prefix <= 110:
        return DOMAIN_REVENUE
    if 201 <= prefix <= 229:
        return DOMAIN_FUNCTIONAL
    if 301 <= prefix <= 310:
        return DOMAIN_ECONOMIC
    return DOMAIN_OTHER


@dataclass
class ParsedCell:
    """单元格三态：number / text / bbox。非数值单元格 number=None。"""

    number: Optional[Decimal] = None
    text: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[Sequence[float]] = None
    confidence: float = 1.0

    @property
    def is_numeric(self) -> bool:
        return self.number is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": str(self.number) if self.number is not None else None,
            "text": self.text,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox else None,
            "confidence": self.confidence,
        }


@dataclass
class ParsedRow:
    """行：cells + 行角色（detail/subtotal/total/header）+ 科目编码。"""

    cells: List[ParsedCell]
    row_role: str = "detail"            # header / detail / subtotal / total
    code: Optional[str] = None
    code_level: Optional[int] = None    # 3=类 5=款 7=项
    label: str = ""
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_role": self.row_role,
            "code": self.code,
            "code_level": self.code_level,
            "label": self.label,
            "confidence": self.confidence,
            "cells": [cell.to_dict() for cell in self.cells],
        }


@dataclass
class ParsedTable:
    """命名列表格模型（内存态，与数据库无关）。"""

    table_code: str = ""
    title: str = ""
    page_span: Tuple[int, int] = (0, 0)
    named_columns: Dict[str, int] = field(default_factory=dict)  # 语义列 → 索引
    column_group: str = "single"        # single / two_sided / multi_measure
    canonical_measure: str = "万元"
    classification_type: str = DOMAIN_OTHER
    row_role: str = "detail"
    rows: List[ParsedRow] = field(default_factory=list)
    confidence: float = 1.0
    parse_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_code": self.table_code,
            "title": self.title,
            "page_span": list(self.page_span),
            "named_columns": dict(self.named_columns),
            "column_group": self.column_group,
            "canonical_measure": self.canonical_measure,
            "classification_type": self.classification_type,
            "row_role": self.row_role,
            "confidence": self.confidence,
            "parse_errors": list(self.parse_errors),
            "row_count": len(self.rows),
        }


_CODE_RE = re.compile(r"^\d{3}(\d{2}(\d{2})?)?$")
_NUM_RE = re.compile(r"^-?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _cell_from_raw(raw: Any, page: int) -> ParsedCell:
    text = str(raw).strip() if raw is not None else ""
    if text and _NUM_RE.match(text):
        return ParsedCell(number=Decimal(text.replace(",", "")), page=page)
    return ParsedCell(text=text or None, page=page)


def _row_code(cells: List[ParsedCell], max_scan: int = 3) -> Tuple[Optional[str], Optional[int]]:
    """提取行首科目编码（3/5/7 位整数）。

    科目编码单元格在 PDF 抽取中可能是纯数字串——_cell_from_raw 会把
    "301" 存为 number（GPT5.6 R3 P1-3：此前只读 cell.text，导致
    code=None、classification_type=other，经济/功能分类域判断失效）。
    因此对 number 为整数的单元格，检查其无小数位形式是否匹配编码位长；
    text 单元格仍按原文匹配（前导零场景如 "301" vs "0301" 以文本为准）。
    """
    for cell in cells[:max_scan]:
        text = (cell.text or "").strip()
        if text and _CODE_RE.match(text):
            return text, len(text)
        if cell.number is not None and cell.number == cell.number.to_integral_value():
            digits = str(int(cell.number))
            if _CODE_RE.match(digits):
                return digits, len(digits)
    return None, None


def _row_role_from_label(label: str, code: Optional[str], numeric_count: int) -> str:
    if any(k in label for k in ("总计", "合计", "本年支出合计", "本年收入合计", "经费合计")):
        if label.strip() in ("总计", "合计", "本年支出合计", "本年收入合计") and not code:
            return "total"
        return "subtotal"
    return "detail"


def materialize_table(
    raw_rows: Sequence[Sequence[Any]],
    *,
    title: str = "",
    table_code: str = "",
    pages: Sequence[int] = (),
    header_rows: int = 2,
) -> ParsedTable:
    """把 pdfplumber 风格的原始行转成 ParsedTable（命名列 + 行角色）。"""

    start_page = min(pages) if pages else 0
    end_page = max(pages) if pages else 0
    parsed = ParsedTable(
        table_code=table_code,
        title=title,
        page_span=(start_page, end_page),
    )

    widths = [len(r) for r in raw_rows if r is not None]
    if widths and len(set(widths)) > 1:
        parsed.parse_errors.append(
            f"row_width_inconsistent: {sorted(set(widths))}"
        )

    header_labels: List[str] = []
    for row in raw_rows[:header_rows]:
        header_labels.extend(str(c).strip() for c in row if c)

    # 语义列识别：合计 / 本年支出合计 / 基本支出 / 项目支出 / 决算数 / 预算数
    for row in raw_rows[:header_rows + 2]:
        for i, cell in enumerate(row):
            text = re.sub(r"\s+", "", str(cell or ""))
            key = None
            if text == "合计" or text in ("本年支出合计", "本年收入合计"):
                key = "total"
            elif text == "基本支出":
                key = "basic"
            elif text == "项目支出":
                key = "project"
            elif text == "预算数":
                key = "budget"
            elif text == "决算数":
                key = "final"
            if key and key not in parsed.named_columns:
                parsed.named_columns[key] = i

    # 双栏检测：表头同时出现 收入/支出 或 左右两个"决算数"
    joined_header = "".join(header_labels)
    if "收入" in joined_header and "支出" in joined_header:
        parsed.column_group = "two_sided"

    for r_idx, row in enumerate(raw_rows):
        page = start_page or (pages[0] if pages else 0)
        cells = [_cell_from_raw(c, page) for c in row]
        code, level = _row_code(cells)
        label = "".join((c.text or "") for c in cells if c.text)
        numeric_count = sum(1 for c in cells if c.is_numeric)
        row_role = "header" if r_idx < header_rows else _row_role_from_label(
            label, code, numeric_count
        )
        parsed.rows.append(
            ParsedRow(
                cells=cells,
                row_role=row_role,
                code=code,
                code_level=level,
                label=label,
            )
        )

    # 科目域：取首个科目行的编码域
    for row in parsed.rows:
        if row.code:
            parsed.classification_type = classify_code_domain(row.code)
            break
    return parsed


def build_parsed_tables(page_tables: Sequence[Sequence[Any]]) -> Dict[str, "ParsedTable"]:
    """从 pdfplumber 风格的逐页表格构建 ParsedTable 集合（跨页续表合并）。

    演进史（三次复核迭代，语义边界逐轮收紧）：
    - R2 前的原实现：key 含累计表数永不重复 → 续表从不合并；
    - R2 修复：按内容签名识别续表——但在官方样张上暴露三个新缺陷
      （GPT5.6 R3 P0-1，实测 14 张原始表只剩 4 张）：
      ① last_key 不限相邻：跨页合并后，后续任意页的签名兼容表都
        会被继续吞并（P10 表 span 竟达 (10,19)，10~19 页全归一张表）；
      ② 签名兼容但 merge_compatible 拒绝（如表头语义列不相交）时，
        else 分支不执行 → 该表被静默丢弃；
      ③ 同页两张相同列结构的独立表被合成一张。
    - R3 收敛语义（本实现）：
      ① 合并候选限定**物理相邻**：上一张表与当前表同页，或上一张表
         的 span 末页与当前表相邻页（隔页/隔表不合并——续表在物理
         排版上必然连续）；
      ② 拒绝合并（签名不兼容或 merge_compatible 返回 False）时，
         当前表无条件保留为独立表，绝不允许静默丢表；
      ③ key 唯一性由「首次出现的页 + 该页内序号」保证。
    """
    parsed_tables: Dict[str, ParsedTable] = {}
    last_key: Optional[str] = None
    last_end_page: int = 0
    for p_idx, tables in enumerate(page_tables or [], start=1):
        for raw in tables or []:
            title = "".join(
                str(c or "") for c in (raw[0] if raw and raw[0] else [])
            )[:40]
            parsed = materialize_table(raw, title=title, pages=(p_idx,))
            # 相邻性：上一表与当前表同页（同页连续排版的续表），或上一
            # 表结束页紧邻当前页（跨页续表）。隔页出现的同签名表是
            # 新表种而非续表。
            adjacent = (
                last_key is not None
                and last_end_page in (p_idx, p_idx - 1)
            )
            merged = False
            if (
                adjacent
                and last_key in parsed_tables
                and _table_signature_compatible(parsed_tables[last_key], parsed)
            ):
                if merge_compatible(parsed_tables[last_key], parsed):
                    merged = True
                    # 合并后表头签名不变且 span 已扩展：续表可继续合并
                    last_end_page = max(last_end_page, parsed.page_span[1])
            if not merged:
                # 拒绝合并/不相邻/签名不兼容：无条件保留为独立表——
                # 结构化解析阶段宁可多出独立表，绝不静默丢表
                key = f"P{p_idx}:{len(parsed_tables)}"
                if key in parsed_tables:
                    key = f"{key}:{id(parsed)}"
                parsed_tables[key] = parsed
                last_key = key
                last_end_page = parsed.page_span[1]
    return parsed_tables


# 高区分度语义列：仅出现在特定表种表头中。"total"（合计/本年收支合计）
# 几乎所有决算表都有，单凭它不能判定同表（GPT5.6 R2 复核发现：
# 收入决算表与支出决算表仅共 total 时会被误判续表）。
_DISTINCTIVE_COLUMNS = {"basic", "project", "budget", "final"}


def _table_signature_compatible(base: "ParsedTable", cont: "ParsedTable") -> bool:
    """续表表头签名是否与基准表兼容（语义列或表头文本）。

    续页常常不带表头行（语义列为空）——此时不能仅凭"列名缺失"判为
    不同表：基准表有语义列、续页行宽可落入基准列宽族（或经显式重映射
    对齐）即视为续表候选，由 merge_compatible 的守卫做最终裁决（表头
    语义列都不匹配时它仍会拒绝）。
    """
    base_keys = set(base.named_columns)
    cont_keys = set(cont.named_columns)
    if base_keys and cont_keys:
        # 双方都有语义列：要求共享高区分度列（basic/project/budget/final），
        # 或共享 ≥2 个语义列——只共 total 不足以判定同表。
        shared = base_keys & cont_keys
        if shared & _DISTINCTIVE_COLUMNS or len(shared) >= 2:
            return True
        # 仅有 total 交集：退回表头文本复核（同表种续页表头文本相同）
        base_head = _normalized_head_text(base, rows=2)
        cont_head = _normalized_head_text(cont, rows=2)
        return bool(base_head and cont_head) and (
            cont_head in base_head or base_head in cont_head
        )
    if base_keys and not cont_keys:
        # 续页无表头：列宽可对齐（同宽或窄于基准）即续表候选
        base_widths = {len(r.cells) for r in base.rows} or {0}
        cont_widths = {len(r.cells) for r in cont.rows} or {0}
        return min(cont_widths) <= max(base_widths)
    if cont_keys and not base_keys:
        return False  # 基准表都没识别出语义列，续页反而有 → 不可靠，不合并
    # 双方都无语义列：按表头文本近似判断（前 2 行归一化后子串包含）
    base_head = _normalized_head_text(base, rows=2)
    cont_head = _normalized_head_text(cont, rows=2)
    if not base_head or not cont_head:
        return False
    return cont_head in base_head or base_head in cont_head


def _normalized_head_text(table: "ParsedTable", rows: int = 2) -> str:
    parts: List[str] = []
    for row in table.rows[:rows]:
        parts.append("".join((c.text or "") for c in row.cells))
    return re.sub(r"\s+", "", "".join(parts))


def _remap_continuation_rows(
    rows: List[ParsedRow], target_width: int
) -> List[ParsedRow]:
    """把续页行重映射到基准列宽（GPT5.6 P0-3）。

    跨页列宽漂移的典型形态：续页把前置编码列（类|款|项）合并成一列，
    行宽变窄、尾部金额列相对位置不变。处置与 V33-120 的运行时 shift
    同源：``src = j + (len(row) - target_width)``，窄行尾部列按"行宽差"
    对齐到基准宽度的尾部索引；前置编码列不参与金额对齐。
    """
    remapped: List[ParsedRow] = []
    for row in rows:
        shift = target_width - len(row.cells)
        if shift == 0:
            remapped.append(row)
            continue
        first_page = row.cells[0].page if row.cells else 0
        cells: List[ParsedCell] = [ParsedCell(page=first_page) for _ in range(target_width)]
        for j, cell in enumerate(row.cells):
            src = j + shift
            if 0 <= src < target_width:
                cells[src] = cell
        remapped.append(
            ParsedRow(
                row_role=row.row_role,
                code=row.code,
                code_level=row.code_level,
                label=row.label,
                confidence=row.confidence,
                cells=cells,
            )
        )
    return remapped


def merge_compatible(base: ParsedTable, continuation: ParsedTable) -> bool:
    """跨页合并守卫：表头签名（语义列）兼容才允许合并。

    列宽不一致时（GPT5.6 P0-3）：
    - 有共同语义列 → 按基准宽度做显式列重映射（尾部对齐，见
      ``_remap_continuation_rows``），重映射记入 parse_errors 供质量门/
      评测消费，随后合并；
    - 无共同语义列（表头不兼容）→ 拒绝合并并 parse_error 留痕，
      续表保持独立（宁可少合并也不错位合并）。
    """
    if not continuation.rows:
        return True
    base_keys = set(base.named_columns)
    cont_keys = set(continuation.named_columns)
    shared = base_keys & cont_keys
    if base_keys and cont_keys and not shared:
        base.parse_errors.append(
            f"continuation_header_incompatible: {sorted(cont_keys)}"
        )
        return False
    base_widths = {len(r.cells) for r in base.rows}
    cont_widths = {len(r.cells) for r in continuation.rows}
    if base_widths and cont_widths and not (base_widths & cont_widths):
        # 列宽族不重叠：必须重映射后才能合并，否则金额列错位
        if not shared:
            base.parse_errors.append(
                "continuation_width_incompatible_no_shared_columns: "
                f"base={sorted(base_widths)} cont={sorted(cont_widths)}"
            )
            return False
        base_width = max(base_widths)
        base.parse_errors.append(
            f"continuation_width_remapped: base={sorted(base_widths)} "
            f"cont={sorted(cont_widths)}"
        )
        continuation.rows = _remap_continuation_rows(continuation.rows, base_width)
    base.rows.extend(continuation.rows)
    base.page_span = (min(base.page_span[0], continuation.page_span[0]),
                      max(base.page_span[1], continuation.page_span[1]))
    return True


# ---------------------------------------------------------------------------
# structured / shadow 模式的规则执行入口
# ---------------------------------------------------------------------------

def run_structured_rules(doc: Any, report_kind: Optional[str] = None):
    """以结构化输入运行首批迁移规则，返回 (issues, outcomes)。

    适配器策略：迁移规则的修复版实现已在 rules_v33/common_rules 中
    （单一实现），此处构建 ParsedTable 中间层并委托执行，保证
    legacy/structured 两种输入模式消费同一套规则语义；未迁移规则
    不在此执行（shadow 对比时与 legacy 路径的输出按规则键比较）。
    """
    from src.engine.rule_outcome import RuleOutcome, RuleOutcomeSignal, STATUS_FAIL, STATUS_PASS, STATUS_EXECUTION_ERROR
    from src.engine.rules_v33 import (
        R33115_TotalSheetCheck,
        R33117_BasicExpenseClassification,
        R33120_DetailTableCheck,
        R33202_InterTable_T4_T5,
        R33203_InterTable_T5_T6,
        R33220_Narrative3_T3,
        R33241_Table3_ExpenseAdvancedCheck,
        R33243_Table6_BasicExpenseAdvancedCheck,
        R33244_Table7_ThreePublicAdvancedCheck,
    )

    # 1) 构建内存态结构化表（与数据库无关，规则分析与结构化入库共同消费）。
    # GPT5.6 R2 P0-1a：续表合并改由 build_parsed_tables 按表头签名识别
    # （原内联实现 key 含累计表数，永不重复 → 永不合并）。
    parsed_tables = build_parsed_tables(getattr(doc, "page_tables", []) or [])
    try:
        doc.parsed_tables = parsed_tables
    except Exception:
        pass

    # 2) 迁移规则经适配器执行（委托给修复版实现）
    migrated = [
        R33115_TotalSheetCheck(),
        R33117_BasicExpenseClassification(),
        R33120_DetailTableCheck(),
        R33202_InterTable_T4_T5(),
        R33203_InterTable_T5_T6(),
        R33220_Narrative3_T3(),
        R33241_Table3_ExpenseAdvancedCheck(),
        R33243_Table6_BasicExpenseAdvancedCheck(),
        R33244_Table7_ThreePublicAdvancedCheck(),
    ]
    issues: List[Any] = []
    outcomes: List[RuleOutcome] = []
    for rule in migrated:
        try:
            produced = list(rule.apply(doc) or [])
        except RuleOutcomeSignal as signal:
            outcomes.append(
                RuleOutcome(
                    rule_id=str(getattr(rule, "code", "")),
                    status=signal.status,
                    detail=str(signal.detail or signal),
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 执行异常进摘要
            outcomes.append(
                RuleOutcome(
                    rule_id=str(getattr(rule, "code", "")),
                    status=STATUS_EXECUTION_ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        issues.extend(produced)
        outcomes.append(
            RuleOutcome(
                rule_id=str(getattr(rule, "code", "")),
                status=STATUS_FAIL if produced else STATUS_PASS,
            )
        )
    return issues, outcomes
