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
    """行：cells + 行角色（detail/subtotal/total/header）+ 科目编码。

    双栏表（GPT5.6 R5 P0-B）：左/右栏各持一套「编码+名称+金额」。
    ``code`` 保留左栏编码；右栏编码存 ``code_right``（此前单值
    ``code``/``named_columns`` 丢失右栏数据——样张 P15-16 实测右栏
    310 在第 5-6 列、金额在第 8 列，相关行全部 code=None）。
    """

    cells: List[ParsedCell]
    row_role: str = "detail"            # header / detail / subtotal / total
    code: Optional[str] = None
    code_level: Optional[int] = None    # 3=类 5=款 7=项
    code_right: Optional[str] = None    # 双栏右栏编码（two_sided 表）
    code_level_right: Optional[int] = None
    label: str = ""
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_role": self.row_role,
            "code": self.code,
            "code_level": self.code_level,
            "code_right": self.code_right,
            "code_level_right": self.code_level_right,
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
    # R4 表名锚：从起始页页文本提取的独立表名行（「收入支出决算总表」），
    # 业务表身份——跨页续表合并要求两侧表名严格相同
    anchor_table_name: str = ""

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
            "anchor_table_name": self.anchor_table_name,
            "row_count": len(self.rows),
        }


_CODE_RE = re.compile(r"^\d{3}(\d{2}(\d{2})?)?$")
_NUM_RE = re.compile(r"^-?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _cell_from_raw(raw: Any, page: int) -> ParsedCell:
    text = str(raw).strip() if raw is not None else ""
    if text and _NUM_RE.match(text):
        return ParsedCell(number=Decimal(text.replace(",", "")), page=page)
    return ParsedCell(text=text or None, page=page)


def _row_code(
    cells: List[ParsedCell],
    max_scan: int = 3,
    code_column: Optional[int] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """提取行首科目编码（3/5/7 位整数）。

    科目编码单元格在 PDF 抽取中可能是纯数字串——_cell_from_raw 会把
    "301" 存为 number（GPT5.6 R3 P1-3）。对 number 为整数的单元格，
    检查其无小数位形式是否匹配编码位长。

    GPT5.6 R4 P1-2 收紧：表头确认了「科目编码」列（code_column）时，
    **只在该列识别**——防止金额恰好为 3/5/7 位整数（"301" 万元）被
    误判为科目编码、污染分类域。指定 code_column 时不受 max_scan
    限制（双栏表右栏编码列在第 5-6 列，R5 P0-B）；未指定时只扫
    行首 3 列的 text 形态（数字形态无列守卫不采信）。
    """
    if code_column is not None:
        if 0 <= code_column < len(cells):
            cell = cells[code_column]
            text = (cell.text or "").strip()
            if text and _CODE_RE.match(text):
                return text, len(text)
            if (
                cell.number is not None
                and cell.number == cell.number.to_integral_value()
            ):
                digits = str(int(cell.number))
                if _CODE_RE.match(digits):
                    return digits, len(digits)
        return None, None
    for cell in cells[:max_scan]:
        text = (cell.text or "").strip()
        if text and _CODE_RE.match(text):
            return text, len(text)
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

    # 语义列识别：合计 / 本年支出合计 / 基本支出 / 项目支出 / 决算数 / 预算数。
    # 双栏表（GPT5.6 R5 P0-B）：行宽的一半为界，右半识别出的同名列记为
    # ``*_right``——单值 named_columns 只能存一个位置，右栏的
    # 决算数/合计（样张 P15 右栏 final 在第 8 列）此前被丢弃。
    width_hint = max((len(r) for r in raw_rows if r is not None), default=0)
    seen_right_half = False
    for row in raw_rows[:header_rows + 2]:
        row_width = len(row)
        mid = row_width // 2 if width_hint >= 4 else row_width
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
            if key:
                target_key = f"{key}_right" if i >= mid and width_hint >= 4 else key
                if target_key not in parsed.named_columns:
                    parsed.named_columns[target_key] = i
                    if target_key.endswith("_right"):
                        seen_right_half = True
    # 双栏检测（R5 扩展）：表头同时出现 收入/支出，或右半出现 *_right
    # 语义列（两个"决算数"分列左右），任一即 two_sided
    joined_header = "".join(header_labels)
    if ("收入" in joined_header and "支出" in joined_header) or seen_right_half:
        parsed.column_group = "two_sided"

    # 科目编码列识别（GPT5.6 R4 P1-2）：表头含「科目编码/功能分类/
    # 经济分类」的列是编码列——_row_code 只在该列内识别编码，防止
    # 金额恰好为 3/5/7 位整数（如 301.00 万元取整值 301）被误判科目。
    # 双栏表左右各有一个编码列（R5 P0-B）：右半记 code_right。
    # 注意：同一表头行可能同时含左右两个编码列（样张 P15 R0 的
    # 「经济分类科目编码」×2），必须扫完该行全部列再分配，遇首个
    # 命中就 break 会漏掉右栏。
    for row in raw_rows[:header_rows + 2]:
        row_width = len(row)
        mid = row_width // 2 if width_hint >= 4 else row_width
        for i, cell in enumerate(row):
            text = re.sub(r"\s+", "", str(cell or ""))
            if "编码" in text or text in ("功能分类", "经济分类"):
                if i >= mid and width_hint >= 4:
                    if "code_right" not in parsed.named_columns:
                        parsed.named_columns["code_right"] = i
                elif "code" not in parsed.named_columns:
                    parsed.named_columns["code"] = i

    for r_idx, row in enumerate(raw_rows):
        page = start_page or (pages[0] if pages else 0)
        cells = [_cell_from_raw(c, page) for c in row]
        code, level = _row_code(cells, code_column=parsed.named_columns.get("code"))
        code_right = level_right = None
        if (
            parsed.column_group == "two_sided"
            and "code_right" in parsed.named_columns
        ):
            code_right, level_right = _row_code(
                cells, code_column=parsed.named_columns["code_right"]
            )
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
                code_right=code_right,
                code_level_right=level_right,
                label=label,
            )
        )

    # 科目域：取首个科目行的编码域
    for row in parsed.rows:
        if row.code:
            parsed.classification_type = classify_code_domain(row.code)
            break
    return parsed


_TABLE_NAME_RE = re.compile(r"^.{0,28}?(决算表|决算总表|预算表|支出表|收入表)$")


def _extract_anchor_table_name(page_text: str, scan_lines: int = 8) -> str:
    """从页面文本前几行提取独立表名行（如「收入支出决算总表」）。

    官方样张形态（R4 实测）：每张表的起始页，表名以独立短行出现在
    页文本头部（首行常是部门抬头「…部门决算表」——含「部门」的
    抬头行跳过；「二、收入决算表」这类目录/说明引用也跳过）。
    表名行是唯一稳定的业务身份：PDF 表格 bbox 不含它，但页文本有。
    """
    for line in str(page_text or "").split("\n")[:scan_lines]:
        stripped = line.strip()
        if not stripped or len(stripped) > 30:
            continue
        if "部门" in stripped or "单位" in stripped or stripped.startswith(("一、", "二、", "三、")):
            continue
        if _TABLE_NAME_RE.search(stripped):
            return stripped
    return ""


def build_parsed_tables(
    page_tables: Sequence[Sequence[Any]],
    page_texts: Optional[Sequence[str]] = None,
) -> Dict[str, "ParsedTable"]:
    """从 pdfplumber 风格的逐页表格构建 ParsedTable 集合（跨页续表合并）。

    演进史（四次复核迭代，语义边界逐轮收紧）：
    - R2 前原实现：key 含累计表数永不重复 → 续表从不合并；
    - R2 修复：内容签名识别续表——真实样张上暴露三个缺陷（14 表→4，
      span 吞并 (10,19)、拒绝时静默丢表、同页独立表误合）；
    - R3 收敛：物理相邻 + 拒绝不丢表——但仍把 P7 总表与 P8 收入决算表
      合成 (7,8)、P15-17（基本支出+三公）、P18-19（政府性基金+国有
      资本）跨表种合并（GPT5.6 R4 P0-1：物理相邻挡不住相邻页的
      **不同业务表**；同签名的语义列在决算表族里没有区分度）。
    - R4 收敛（本实现）——**表名锚约束**：
      ① 每张新建表从起始页页文本提取独立表名行（anchor_table_name，
         见 _extract_anchor_table_name）——这是稳定的业务身份；
      ② 合并候选必须满足：物理相邻 **且表名严格相同**（空表名不可
         合并——宁可多独立表不错并）；
      ③ 拒绝合并时无条件保留独立表（R3 语义保持）。
    """
    parsed_tables: Dict[str, ParsedTable] = {}
    last_key: Optional[str] = None
    last_end_page: int = 0
    last_anchor: str = ""
    texts = list(page_texts or [])
    for p_idx, tables in enumerate(page_tables or [], start=1):
        page_text = texts[p_idx - 1] if 0 < p_idx <= len(texts) else ""
        for raw in tables or []:
            title = "".join(
                str(c or "") for c in (raw[0] if raw and raw[0] else [])
            )[:40]
            parsed = materialize_table(raw, title=title, pages=(p_idx,))
            # 表名锚：新建表尝试从起始页页文本取业务表名；
            # 续表候选页若页文本有**新表名行**，则该页起的是新表，
            # 强制不合并（P7 总表续到 P8 时，P8 有「收入决算表」新锚）
            page_anchor = _extract_anchor_table_name(page_text)
            if page_anchor:
                parsed.anchor_table_name = page_anchor
            # 相邻性：上一表与当前表同页或紧邻页（R3 语义）
            adjacent = (
                last_key is not None
                and last_end_page in (p_idx, p_idx - 1)
            )
            merged = False
            if adjacent and last_key in parsed_tables:
                base_table = parsed_tables[last_key]
                # R4 表名锚约束（三层）：
                # ① 当前页出现**新表名行** → 物理翻表，无条件禁止合并
                #   （P7 总表续到 P8 时，P8 有「收入决算表」新锚）；
                # ② 基准表有表名、续页无表名 → 续页通常不重复表名，
                #   视为同表候选，交给签名守卫裁决；
                # ③ 双方都有表名但不同 → 不同业务表，禁止合并。
                # 双方都无表名 → 无业务身份证据，保守不合并。
                new_anchor_on_page = page_anchor and page_anchor != last_anchor
                same_anchor = bool(
                    last_anchor
                    and (
                        parsed.anchor_table_name == last_anchor
                        # 续页无表名行（表名只在首页）→ 同表候选
                        or not parsed.anchor_table_name
                    )
                )
                if same_anchor and not new_anchor_on_page:
                    if _table_signature_compatible(base_table, parsed):
                        if merge_compatible(base_table, parsed):
                            merged = True
                            last_end_page = max(
                                last_end_page, parsed.page_span[1]
                            )
            if not merged:
                # 拒绝合并/不相邻/表名不同：无条件保留为独立表——
                # 结构化解析阶段宁可多出独立表，绝不静默丢表
                key = f"P{p_idx}:{len(parsed_tables)}"
                if key in parsed_tables:
                    key = f"{key}:{id(parsed)}"
                parsed_tables[key] = parsed
                last_key = key
                last_end_page = parsed.page_span[1]
                last_anchor = parsed.anchor_table_name
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
    - 基准表有语义列、续页无表头（表名锚已确认同表、宽度一致且窄于
      基准）→ 这是官方样张的真实续页形态（P9/P11：续页省略左侧
      「功能分类/科目编码」层级表头列，行宽 9/8 vs 基准 11/10，
      语义列为空）——按基准宽度做显式尾部重映射后合并（R5 P0-A：
      此前一律拒绝导致 P9/P11 真实续表断裂、14 行数据不进规则）；
    - 双方语义列零交集（不同表种）→ 拒绝合并并 parse_error 留痕。

    重映射后语义列索引随之平移（named_columns 按宽度差平移），保证
    消费方按命名列取数跨页一致。
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
        if shared:
            pass  # 有共享语义列：可以精确重映射
        elif (
            base_keys
            and not cont_keys
            and len(cont_widths) == 1
            and max(cont_widths) < max(base_widths)
        ):
            # 续页无表头 + 行宽一致且窄于基准：真实续页形态（表名锚
            # 已在 build_parsed_tables 确认同表），尾部重映射合并
            pass
        else:
            base.parse_errors.append(
                "continuation_width_incompatible_no_shared_columns: "
                f"base={sorted(base_widths)} cont={sorted(cont_widths)}"
            )
            return False
        base_width = max(base_widths)
        shift = base_width - max(cont_widths)
        base.parse_errors.append(
            f"continuation_width_remapped: base={sorted(base_widths)} "
            f"cont={sorted(cont_widths)}"
        )
        continuation.rows = _remap_continuation_rows(continuation.rows, base_width)
        # 语义列索引按宽度差平移（续页自身语义列为空时无操作）
        if not cont_keys and shift:
            continuation.named_columns = {}
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

    材料类型路由（GPT5.6 R5 P1-E）：V33 迁移集是**决算**规则——
    budget/unknown 材料不适用，返回空结果（此前对预算任务也执行
    9 条决算规则、统一显示 6 pass + 3 insufficient，把"预算规则减少
    量"误算成 structured delta，且从未验证过规则在目标决算材料上
    的正确性）。
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

    # 材料类型路由：非决算材料不执行决算规则集（迁移批次全部预算
    # 规则落地前，budget/unknown 返回空——诚实反映"该类型无可执行
    # 的迁移规则"，而不是跑出 3 条 insufficient 的假象）
    if (report_kind or "").strip().lower() != "final":
        return [], []

    # 1) 构建内存态结构化表（与数据库无关，规则分析与结构化入库共同消费）。
    # GPT5.6 R2 P0-1a：续表合并改由 build_parsed_tables 按表头签名识别
    # （原内联实现 key 含累计表数，永不重复 → 永不合并）。
    # R4：page_texts 传入以提取表名锚（表名行在页文本、不在表格 bbox）
    parsed_tables = build_parsed_tables(
        getattr(doc, "page_tables", []) or [],
        getattr(doc, "page_texts", []) or [],
    )
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
