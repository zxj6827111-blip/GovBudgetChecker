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
from typing import Any, Dict, List, Mapping, Optional, Sequence

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
    "下列 fact_uid / evidence_id / check_relation_id 是唯一可引用的身份；"
    "只能逐字复制，不得编造；没有出现在本块中的 ID 一律不要输出。\n"
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
            page_label = f"P{page}" if page is not None else "页码未定位"
            facts.append(f"[事实] {subject_hint} | {metric}={value:.2f}{unit} ({page_label}) | 依据: {clause[:36]}")
            count += 1
            if count >= 6:
                break
        if len(facts) >= _MAX_FACTS:
            break
    lines.extend(facts[:_MAX_FACTS])
    return lines


def _fact_identity_lines(fact_context: Optional[Mapping[str, Any]]) -> List[str]:
    """可引用的事实身份行：fact_uid + evidence_id + 页码/口径。"""
    if not isinstance(fact_context, Mapping):
        return []
    facts = fact_context.get("facts")
    if not isinstance(facts, list):
        return []
    lines: List[str] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            continue
        fact_uid = str(fact.get("fact_uid") or "").strip()
        if not fact_uid:
            continue
        evidence_ids: List[str] = []
        page = None
        raw_ids = fact.get("evidence_ids")
        if isinstance(raw_ids, list):
            evidence_ids.extend(str(item).strip() for item in raw_ids if str(item).strip())
        extra = fact.get("extra_dims") if isinstance(fact.get("extra_dims"), Mapping) else {}
        refs = extra.get("evidence_refs") if isinstance(extra, Mapping) else None
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, Mapping):
                    continue
                evidence_id = str(ref.get("evidence_id") or "").strip()
                if evidence_id and evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
                if page is None:
                    raw_page = ref.get("physical_page") or ref.get("page")
                    try:
                        page = int(raw_page) if raw_page is not None else None
                    except (TypeError, ValueError):
                        page = None
        if page is None:
            try:
                page = int(fact.get("page")) if fact.get("page") is not None else None
            except (TypeError, ValueError):
                page = None
        value = fact.get("normalized_value")
        parts = [
            f"[事实ID] fact_uid={fact_uid}",
            f"指标={fact.get('indicator') or '未识别'}",
            f"值={value if value is not None else '未知'}",
            f"单位=万元归一",
            f"年度={fact.get('fiscal_year') or '未知'}",
            f"主体={fact.get('org_name') or '未知'}",
            f"范围={fact.get('fund_scope') or '未知'}",
            f"阶段={fact.get('budget_stage') or '未知'}",
            f"科目={fact.get('subject_code') or ''}{('/' + str(fact.get('subject_name')) if fact.get('subject_name') else '')}",
            f"页码={'P' + str(page) if page else '未定位'}",
            f"evidence_ids={','.join(evidence_ids[:4]) if evidence_ids else '无'}",
        ]
        lines.append(" | ".join(parts))
        if len(lines) >= 30:
            break
    return lines


def _relation_identity_lines(check_receipts: Optional[Sequence[Any]]) -> List[str]:
    if not isinstance(check_receipts, Sequence) or isinstance(check_receipts, (str, bytes)):
        return []
    lines: List[str] = []
    for item in check_receipts:
        if isinstance(item, Mapping):
            rule_id = str(item.get("rule_id") or "").strip()
            sub_check_id = str(item.get("sub_check_id") or "").strip()
            relation = str(item.get("check_relation_id") or "").strip()
            if not relation and rule_id and sub_check_id:
                relation = f"{rule_id}:{sub_check_id}"
            if not relation:
                continue
            status = str(item.get("status") or "").strip()
            lines.append(
                f"[检查关系] check_relation_id={relation} | rule_id={rule_id or '未知'}"
                f" | sub_check_id={sub_check_id or '未知'} | status={status or '未知'}"
            )
        elif isinstance(item, str) and item.strip():
            lines.append(f"[检查关系] check_relation_id={item.strip()}")
        if len(lines) >= 30:
            break
    return lines


def _locate_page(page_texts: Sequence[str], clause: str) -> Optional[int]:
    key = re.sub(r"\s+", "", clause)
    pages = [idx + 1 for idx, text in enumerate(page_texts or [])
             if key and key in re.sub(r"\s+", "", text or "")]
    return pages[0] if len(pages) == 1 else None


def build_structured_context(
    page_texts: Sequence[str],
    page_tables: Optional[Sequence[Sequence[Any]]],
    max_chars: int = _DEFAULT_MAX_CHARS,
    *,
    fact_context: Optional[Mapping[str, Any]] = None,
    check_receipts: Optional[Sequence[Any]] = None,
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
    identity_lines = _fact_identity_lines(fact_context)
    if identity_lines:
        parts.append("【可引用事实与证据 ID】")
        parts.extend(identity_lines)
    relation_lines = _relation_identity_lines(check_receipts)
    if relation_lines:
        parts.append("【可引用检查关系 ID】")
        parts.extend(relation_lines)

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
