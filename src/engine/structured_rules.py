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
from collections import Counter
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
class ColumnGroup:
    """multi_measure 表的一个列组：业务主体 + 组内语义列位置。

    R7 P0-2：多金额组表（三公表 6 组预算/决算、基金表多段表头）的
    语义必须逐组保留——subject 是该组列上方的业务主体标签（合计、
    因公出国（境）费、小计、公务接待费…），parent_subject 是多级
    表头的父标签（公务用车购置及运行维护费），columns 登记组内
    语义键 → 列索引（如 {budget: 2, final: 3}）。
    """

    subject: str = ""
    parent_subject: str = ""
    columns: Dict[str, int] = field(default_factory=dict)  # 语义键 → 列索引
    span: Tuple[int, int] = (0, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "parent_subject": self.parent_subject,
            "columns": dict(self.columns),
            "span": list(self.span),
        }


@dataclass
class ParsedTable:
    """命名列表格模型（内存态，与数据库无关）。"""

    table_code: str = ""
    title: str = ""
    page_span: Tuple[int, int] = (0, 0)
    named_columns: Dict[str, int] = field(default_factory=dict)  # 语义列 → 首选索引
    # R7 P0-2：semantic_columns 保留每种语义键的**全部**列位置
    # （named_columns 只是兼容旧消费方的首选单值——P17 三公表 6 组
    # 预算/决算列此前只剩 budget=0/final=1，其余 5 组全部丢失）；
    # column_groups 逐组保留业务主体（合计/出国/公车/接待…）。
    semantic_columns: Dict[str, List[int]] = field(default_factory=dict)
    column_groups: List[ColumnGroup] = field(default_factory=list)
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
            "semantic_columns": {
                k: list(v) for k, v in self.semantic_columns.items()
            },
            "column_groups": [g.to_dict() for g in self.column_groups],
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
    #
    # 双栏识别（GPT5.6 R6 P0-1 终版）：R5 的几何中点把普通多金额列表
    # 全部误判 two_sided；R6 第一版的"同键出现两次"仍会误判多段式表头
    # （样张 P18 的「合计×2」分属 年初结转/本年支出 两个金额段，左右
    # 并非重复结构）。真双栏（P7 收支总表、P15 经济分类表）的特征是
    # **列语义序列左右两半相同**——把表头逐列的语义标签串起来，空位
    # 归一后检查 seq[:n//2] == seq[n//2:]。P17 的分组宽表（费用主体
    # 分列）与 P18 的多段表头都不对称，正确判为 single。
    header_hits: Dict[str, List[int]] = {}
    hit_rows: Counter = Counter()
    scan_rows = list(raw_rows[:header_rows + 2])
    # R9 P2：逐行记录各语义键的列位置——multi_measure 判定需要「同行
    # 不同列」证据，跨行的同列重复（表头+首条数据行的「合计」）不算
    per_row_hits: List[Dict[str, List[int]]] = [
        {} for _ in scan_rows
    ]
    for r_idx, row in enumerate(scan_rows):
        # 数据行守卫（/review R9 自查）：扫描窗口含表头后 2 行数据——
        # 其中「合计/基本支出」等精确命中（如续表首页的合计数据行）
        # 不是表头证据。登记会把无表头续表误判成「有表头的新表」：
        # 多表页首表续表守卫（named_columns 非空 → adjacent=False）
        # 与签名守卫（仅共 total 单键 → 表头文本复核失败）双双拒并
        # （实测应 2 张实得 3 张的残留形态）。目标语料的真实表头行
        # 不含金额数字，按行过滤不改变既有表的判定。
        row_has_amount = any(
            _NUM_RE.match(re.sub(r"\s+", "", str(c or ""))) for c in row
        )
        for i, cell in enumerate(row):
            text = re.sub(r"\s+", "", str(cell or ""))
            key = None
            if not row_has_amount:
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
                header_hits.setdefault(key, []).append(i)
                per_row_hits[r_idx].setdefault(key, []).append(i)
                hit_rows[r_idx] += 1

    # 列语义序列：每列聚合表头行标签（归一化），含编码/名称类结构性标签。
    # 领域前缀词（收入/支出/年初/年末等）在归一前先剥除——真双栏左右
    # 两半的领域词不同（P7 收入侧 vs 支出侧）但结构标签对称，
    # 直接比较会把真双栏判 single。
    col_count = max(
        (len(r) for r in raw_rows[:header_rows] if r is not None), default=0
    )
    domain_words = ("收入", "支出", "年初", "年末", "结转", "结余", "功能分类", "经济分类")

    def _structural_label(text: str) -> str:
        for w in domain_words:
            text = text.replace(w, "")
        return text

    col_seq: List[str] = []
    for i in range(col_count):
        labels: List[str] = []
        for r in raw_rows[:header_rows]:
            if i < len(r):
                t = re.sub(r"\s+", "", str(r[i] or ""))
                if t:
                    labels.append(t)
        joined = "|".join(labels) if labels else "_"
        col_seq.append(_structural_label(joined))
    is_two_sided = False
    if col_count >= 4 and col_count % 2 == 0:
        half = col_count // 2
        left_seq = [c for c in col_seq[:half] if c != "_"]
        right_seq = [c for c in col_seq[half:] if c != "_"]
        is_two_sided = bool(left_seq) and left_seq == right_seq
    if is_two_sided:
        parsed.column_group = "two_sided"

    # ---- R7 P0-2：multi_measure 列组模型 ----
    # 非双栏表中同一语义键多次出现 = 多金额组表（样张 P17 三公表
    # 6 组预算/决算、P18 基金表「合计」行标签列 + 本年支出段合计）。
    # 此前非双栏只存每种键的首个位置：P17 只剩 budget=0/final=1、
    # P18 total=0 错指行标签列——其余金额组全部丢失。
    # 语义列全量登记（named_columns 保持首选单值兼容旧消费方）。
    parsed.semantic_columns = {
        key: sorted(set(positions)) for key, positions in header_hits.items()
    }
    # R9 P2：multi_measure 判定必须用**同行内不同列位置**——此前按命中
    # 次数判定，普通单金额表的「合计」在表头与首条数据行同列重复出现
    # 时被误标 multi_measure（语义位置去重后只有 [0]）。分组宽表/多段
    # 表头的真实形态是同一表头行内多个不同列位（P17 预算×6、P18
    # 合计×2、P8 决算数×7 均同行不同列）。
    is_multi_measure = (
        not is_two_sided
        and any(
            len(set(positions)) >= 2
            for row_positions in per_row_hits
            for positions in row_positions.values()
        )
    )
    # 语义叶行：扫描窗口内命中语义键最多的表头行（预算/决算数行、
    # 合计/基本/项目行）——列组主体只取它上方的标签。
    leaf_row: Optional[int] = hit_rows.most_common(1)[0][0] if hit_rows else None
    col_seqs: List[Optional[Tuple[str, ...]]] = []
    if is_multi_measure:
        parsed.column_group = "multi_measure"

        def _stacked_labels(col: int) -> Tuple[str, ...]:
            """列上方（语义叶行之前）的非空表头标签序列，自顶向下去相邻重复。

            非空单元格 <2 的行视为标题/抬头行跳过——P17 的表名行只有
            第 0 列有值，不跳过会把「财政拨款"三公"经费」表名当成
            0 号列组的父主体。
            """
            if leaf_row is None:
                return ()
            labels: List[str] = []
            for r in range(leaf_row):
                if r >= len(scan_rows) or scan_rows[r] is None:
                    continue
                row_cells = scan_rows[r]
                if sum(1 for c in row_cells if str(c or "").strip()) < 2:
                    continue
                if col >= len(row_cells):
                    continue
                text = re.sub(r"\s+", "", str(row_cells[col] or ""))
                if text and (not labels or labels[-1] != text):
                    labels.append(text)
            return tuple(labels)

        for col in range(col_count):
            seq = _stacked_labels(col)
            if not seq and col_seqs:
                # 空列头延续左侧列组（合并单元格跨列，如 P17 决算数列）
                seq = col_seqs[-1]
            if not seq:
                # 首列本身无表头标签（历史材料的多形态表头）：
                # 不建组、也不崩溃——该列命中键不进任何列组
                seq = None
            col_seqs.append(seq)

        # 按「上方标签序列相同」切列组：subject=最深标签、parent_subject=
        # 其上一级标签（多级表头），组内登记语义键 → 列索引。
        groups: List[ColumnGroup] = []
        current_seq: Optional[Tuple[str, ...]] = None
        for col, seq in enumerate(col_seqs):
            if seq is None:
                continue
            if seq != current_seq:
                groups.append(
                    ColumnGroup(
                        subject=seq[-1],
                        parent_subject=seq[-2] if len(seq) > 1 else "",
                        span=(col, col),
                    )
                )
                current_seq = seq
            elif groups:
                groups[-1].span = (groups[-1].span[0], col)
        for key, positions in header_hits.items():
            for pos in positions:
                for group in groups:
                    if group.span[0] <= pos <= group.span[1]:
                        group.columns[key] = pos
        parsed.column_groups = groups

    def _label_block(seq: Optional[Tuple[str, ...]]) -> bool:
        """行标签列组（编码/科目名称等结构性列）——其中的语义键
        （如 P18 第 0 列「合计」是行标签列，不是金额列）不得成为
        named_columns 的首选位置。"""
        joined = "".join(seq or ())
        return "编码" in joined or "科目名称" in joined or joined == "项目"

    for key, positions in header_hits.items():
        if is_two_sided and len(positions) >= 2:
            # 双栏：首次出现记标准键、二次出现记 *_right
            parsed.named_columns[key] = positions[0]
            parsed.named_columns[f"{key}_right"] = positions[1]
        elif is_multi_measure:
            # 多金额组：首选**金额段**位置（跳过行标签列组）——P18 的
            # total 首选第 4 列（本年支出段合计），而非第 0 列行标签
            primary = next(
                (
                    p
                    for p in positions
                    if not _label_block(col_seqs[p] if p < len(col_seqs) else None)
                ),
                positions[0],
            )
            parsed.named_columns[key] = primary
        else:
            parsed.named_columns[key] = positions[0]

    # 科目编码列识别（GPT5.6 R4 P1-2）：表头含「科目编码/功能分类/
    # 经济分类」的列是编码列——_row_code 只在该列内识别编码，防止
    # 金额恰好为 3/5/7 位整数（如 301.00 万元取整值 301）被误判科目。
    # 双栏表左右各有一个编码列（code/code_right 分记）。
    code_positions: List[int] = []
    for row in raw_rows[:header_rows + 2]:
        for i, cell in enumerate(row):
            text = re.sub(r"\s+", "", str(cell or ""))
            if "编码" in text or text in ("功能分类", "经济分类"):
                if i not in code_positions:
                    code_positions.append(i)
    if code_positions:
        parsed.named_columns["code"] = code_positions[0]
    if is_two_sided and len(code_positions) >= 2:
        parsed.named_columns["code_right"] = code_positions[1]

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
        page_tables_on_page = list(tables or [])
        multi_table_page = len(page_tables_on_page) > 1
        for t_idx, raw in enumerate(page_tables_on_page):
            title = "".join(
                str(c or "") for c in (raw[0] if raw and raw[0] else [])
            )[:40]
            parsed = materialize_table(raw, title=title, pages=(p_idx,))
            # 表名锚：新建表尝试从起始页页文本取业务表名。
            # R9 P1-4：单页多表无法用单个页锚表达——「页顶=上一页续表 +
            # 下方=新表」时，页文本的首个表名行属于**新表**，把它安到
            # 页顶第一张 raw table 会把续表错标成新表名、拆断合并（实测
            # 应 2 张实得 3 张）。多表页不赋页锚：首表仅当**无表头**
            # （续表形态）才允许作续表候选，其余一律独立；单表页保持
            # R4 页锚约束（P7→P8 防错并）。bbox 关联是根治方向，当前
            # 抽取层无 bbox，退化为 fail-closed（宁可多独立表不错并）。
            page_anchor = _extract_anchor_table_name(page_text)
            if page_anchor and not multi_table_page:
                parsed.anchor_table_name = page_anchor
            # 相邻性：上一表与当前表同页或紧邻页（R3 语义）。
            # R8 P2：同页第 2+ 张独立 raw table 默认禁止续表合并（无
            # bbox 连续性证据）。
            first_on_page = t_idx == 0
            adjacent = (
                last_key is not None
                and last_end_page in (p_idx, p_idx - 1)
                and first_on_page
            )
            # 多表页首表：仅无表头（无语义列）才可能是上一页续表；
            # 有表头说明它是本页起的新表，不得与上一页的表合并
            if multi_table_page and first_on_page and parsed.named_columns:
                adjacent = False
            merged = False
            if adjacent and last_key in parsed_tables:
                base_table = parsed_tables[last_key]
                # R4 表名锚约束（三层）：
                # ① 当前页出现**新表名行** → 物理翻表，无条件禁止合并
                #   （P7 总表续到 P8 时，P8 有「收入决算表」新锚）；
                #   R9 P1-4：多表页的页锚属于页内新表、不属于续表，
                #   该守卫对多表页不适用（续表会被误拆）；
                # ② 基准表有表名、续页无表名 → 续页通常不重复表名，
                #   视为同表候选，交给签名守卫裁决；
                # ③ 双方都有表名但不同 → 不同业务表，禁止合并。
                # 双方都无表名 → 无业务身份证据，保守不合并。
                new_anchor_on_page = (
                    page_anchor
                    and page_anchor != last_anchor
                    and not multi_table_page
                )
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
    rows: List[ParsedRow],
    target_width: int,
    code_column: Optional[int] = None,
) -> List[ParsedRow]:
    """把续页行重映射到基准列宽（GPT5.6 P0-3 → R6 P0-2 重设计）。

    无表头续页的两种列形态：
    - 前置编码列：续页常把「类|款|项」合并为单列（P9 的 2110105 在
      续页第 0 列，基准 P8 的编码拆列 code=0）。合并编码应**前对齐**
      到基准的 code 列位（左端对齐），此前纯尾部对齐把它推到第 2 列
      （恰好基准的款列位），基准 schema 取不到 → V33-120 无法层级汇总；
    - 尾部金额列：相对位置不变，按行宽差尾部对齐（原语义保持）。

    重建 ParsedRow 时保留 code_right/code_level_right（R6 P0-2：
    此前重建丢右栏字段）。

    review 🟢3（形态守卫）：前对齐只认**编码形态**的首列——合并编码
    （"2110105" 文本或整数 number）才有资格进基准 code 列位；首列是
    任意非空内容（如序号 "1"）时不做前对齐，走尾部对齐（code 列位
    留空 → _row_code 形态守卫兜底返回 None，不会污染编码列）。

    /review 自查补：前对齐仅在续页**窄于**基准（shift>0）时有语义
    （续页把前置编码列合并成单列）；续页更宽（shift<0）时不做前对齐，
    且尾部对齐循环不得覆盖已前对齐的编码单元格（否则 src=j+shift
    可能落回 code 列位，编码被续页第二列静默覆盖）。
    """

    def _looks_like_code(cell: ParsedCell) -> bool:
        text = (cell.text or "").strip()
        if text and _CODE_RE.match(text):
            return True
        if (
            cell.number is not None
            and cell.number == cell.number.to_integral_value()
            and _CODE_RE.match(str(int(cell.number)))
        ):
            return True
        return False

    remapped: List[ParsedRow] = []
    for row in rows:
        shift = target_width - len(row.cells)
        if shift == 0:
            remapped.append(row)
            continue
        first_page = row.cells[0].page if row.cells else 0
        cells: List[ParsedCell] = [ParsedCell(page=first_page) for _ in range(target_width)]
        # 前置编码列前对齐：续页窄于基准（shift>0）且首列为编码形态
        # （3/5/7 位数字）时才前对齐
        prefix_aligned = 0
        if shift > 0 and code_column is not None and code_column < target_width:
            first_cell = row.cells[0] if row.cells else None
            if first_cell is not None and _looks_like_code(first_cell):
                cells[code_column] = first_cell
                prefix_aligned = 1
        # 其余列尾部对齐（金额列相对位置不变）
        for j in range(prefix_aligned, len(row.cells)):
            src = j + shift
            if 0 <= src < target_width:
                if prefix_aligned and src == code_column:
                    continue  # 不覆盖前对齐的编码单元格
                cells[src] = row.cells[j]
        remapped.append(
            ParsedRow(
                row_role=row.row_role,
                code=row.code,
                code_level=row.code_level,
                code_right=row.code_right,
                code_level_right=row.code_level_right,
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
        continuation.rows = _remap_continuation_rows(
            continuation.rows, base_width, code_column=base.named_columns.get("code")
        )
        # 语义列索引按宽度差平移（续页自身语义列为空时无操作）
        if not cont_keys and shift:
            continuation.named_columns = {}
    # 继承首页 schema 重解析续页行（GPT5.6 R6 P0-2）：无表头续页（P9/P11）
    # 自身识别不出编码列，合并后行.code 沿用重映射前的旧值（None）——
    # V33-120 无法做层级汇总。用基准表的 named_columns 对全部续页行
    # 重新计算 code/code_right/code_level，行内编码数据即可被消费。
    code_col = base.named_columns.get("code")
    code_right_col = base.named_columns.get("code_right")
    for row in continuation.rows:
        row.code, row.code_level = _row_code(row.cells, code_column=code_col)
        if code_right_col is not None:
            row.code_right, row.code_level_right = _row_code(
                row.cells, code_column=code_right_col
            )
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
