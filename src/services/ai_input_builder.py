"""AI 输入表征增强：为语义审计构建「结构化事实 + 表格关系 + 页码」上下文。

阶段 4 条目（docs/GovBudgetChecker 完整整改计划 §4）：
AI 输入改为「原文窗口 + 结构化事实 + 表格关系 + 页码/bbox」，
**模型只负责语义候选，不负责确定性金额复算**。

构建原则：
- 表格关系来自与规则分析同一套内存解析（``structured_rules.materialize_table``），
  避免同一 PDF 两套解析结论；
- 说明事实复用 ``narration`` 的软换行恢复/章节切分/金额抽取（年份 token 排除）；
- 每条关系都带页码；bbox 无法从纯文本行恢复时如实标注"页码级定位"，
  不伪造 bbox；
- 上下文为增量注入（prompt 指令与版本不变），超长时按预算截断，
  截断事实如实标注。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from src.engine.structured_rules import materialize_table
from src.utils.narration import (
    extract_amounts,
    merge_page_texts,
    split_clauses,
    split_numbered_sections,
)

#: 注入块头部：向模型声明这段内容的地位与边界
_CONTEXT_HEADER = (
    "【结构化事实与表格关系（确定性解析产出，仅供交叉参考）】\n"
    "以下信息由确定性解析器从同一文档的结构化结果中提取，带页码定位。\n"
    "金额勾稽以确定性规则为准；你只负责输出语义候选（表述/逻辑/披露完整性），"
    "不负责金额复算，也不得仅凭下列数字新增勾稽类问题。\n"
)

#: 上下文预算（字符）：过大挤占原文窗口
_DEFAULT_MAX_CHARS = 6000

_MAX_TABLE_ROWS = 12
_MAX_FACTS = 40


def _table_summary(
    raw_rows: Sequence[Sequence[Any]],
    page_number: int,
    title_hint: str = "",
) -> List[str]:
    """单个表格 → 紧凑关系摘要（标题/页/列语义/合计行/科目样本）。"""
    parsed = materialize_table(raw_rows, title=title_hint, pages=(page_number,))
    lines: List[str] = []
    title = title_hint or parsed.title or f"P{page_number} 表"
    cols = "/".join(f"{k}@c{v}" for k, v in sorted(parsed.named_columns.items(), key=lambda kv: kv[1]))
    lines.append(
        f"[表] P{page_number} {title[:40]} | "
        f"列组={parsed.column_group} | 科目域={parsed.classification_type}"
        + (f" | 语义列: {cols}" if cols else "")
    )

    total_rows = [r for r in parsed.rows if r.row_role in ("total", "subtotal")]
    for row in total_rows[:3]:
        numbers = [
            f"{c.number:.2f}" if c.number is not None and c.page is not None else ""
            for c in row.cells
        ]
        nums = [n for n in numbers if n]
        if nums:
            lines.append(f"    合计行(P{page_number}): {row.label[:24]} = {' / '.join(nums[:8])}")

    detail_rows = [
        r for r in parsed.rows
        if r.row_role == "detail" and r.code and r.code_level == 7
    ] or [r for r in parsed.rows if r.row_role == "detail" and r.code]
    shown = 0
    for row in detail_rows:
        if shown >= _MAX_TABLE_ROWS:
            break
        amounts = [
            f"{c.number:.2f}" for c in row.cells[-3:] if c.number is not None
        ]
        if not amounts:
            continue
        lines.append(
            f"    明细(P{page_number}): {row.code} {row.label[:20]} = {' / '.join(amounts)}"
        )
        shown += 1
    if parsed.parse_errors:
        lines.append(f"    解析风险: {'; '.join(parsed.parse_errors[:2])}")
    return lines


def _fact_summary(page_texts: Sequence[str]) -> List[str]:
    """说明章节 → {subject, metric, amount, unit, 页码} 事实摘要。"""
    lines: List[str] = []
    merged = merge_page_texts(list(page_texts))
    facts: List[str] = []
    for title, body, offset in split_numbered_sections(merged):
        if "说明" not in title:
            continue
        subject_hint = re.sub(r"[（(].*?[)）]|[一二三四五六七八九十]+[、.．]|\\s", "", title)[:24]
        count = 0
        for clause in split_clauses(body):
            amounts = extract_amounts(clause)
            if not amounts:
                continue
            value, unit = amounts[0]
            if "预算" in clause and "决算" not in clause:
                metric = "预算"
            elif any(w in clause for w in ("增加", "减少", "增长", "下降")):
                metric = "同比变化"
            else:
                metric = "决算"
            page = _locate_page(page_texts, clause)
            facts.append(
                f"[事实] {subject_hint} | {metric}={value:.2f}{unit} (P{page}) | 依据: {clause[:36]}"
            )
            count += 1
            if count >= 6:
                break
        if len(facts) >= _MAX_FACTS:
            break
    lines.extend(facts[:_MAX_FACTS])
    return lines


def _locate_page(page_texts: Sequence[str], clause: str) -> int:
    key = re.sub(r"\s+", "", clause)[:18]
    for idx, text in enumerate(page_texts or []):
        if key and key in re.sub(r"\s+", "", text or ""):
            return idx + 1
    return 1


def build_structured_context(
    page_texts: Sequence[str],
    page_tables: Optional[Sequence[Sequence[Any]]],
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """构建注入语义审计的结构化上下文；无内容时返回空串。"""
    parts: List[str] = []

    for page_idx, tables in enumerate(page_tables or [], start=1):
        # 页面文本首行通常是表题（决算公开表的表题独立成行）
        page_first_line = ""
        if page_texts and page_idx <= len(page_texts):
            page_first_line = next(
                (line.strip() for line in str(page_texts[page_idx - 1] or "").splitlines() if line.strip()),
                "",
            )
        for raw in tables or []:
            try:
                parts.extend(_table_summary(raw, page_idx, title_hint=page_first_line[:40]))
            except Exception:
                # 表格解析失败不阻断上下文构建，只标注风险
                parts.append(f"[表] P{page_idx} 解析失败（parse_error）")

    parts.extend(_fact_summary(page_texts))

    if not parts:
        return ""

    body: List[str] = []
    used = len(_CONTEXT_HEADER)
    truncated = False
    for line in parts:
        cost = len(line) + 1
        if used + cost > max_chars:
            truncated = True
            break
        body.append(line)
        used += cost
    header = _CONTEXT_HEADER
    if truncated:
        header += "（以下信息因长度预算被截断，未列出的部分不代表不存在）\n"
    return header + "\n".join(body) + "\n【结构化信息结束】\n"
