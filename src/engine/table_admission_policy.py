"""表格准入延期的二次分类：把“材料格式缺陷”与“技术映射失败”分开。

规则准入和事后归因共用表格读取回执。准入时继续证据独立的子检查，
捕获 ``RuleDeferred`` 后按回执区分未完成的原因：

- 整表阻断只由“原文无法解析成数值”造成 → 改写为材料缺陷表述，并登记
  单元格级结构化事实（cell_unbound + 真实格 ID + 物理页），首选动作人工确认；
- 存在身份冲突/列未映射/标签缺失/算术冲突 → 保留技术表述，但把位置改到
  真实未绑定格所在页；
- 两者并存 → 分别列出，不互相吞掉。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

#: 只有“这一格取值本身不可解析”能由数字格式解释；列归属、身份冲突、
#: 标签缺失各有独立原因，不能因为归类格式缺陷就一并消失。
FORMAT_EXPLAINED_REASONS = frozenset({"fact_unbound"})

_ADMISSION_PREFIX = "来源表映射/读取未完成"


def check_unblocked_table_scopes(document, rule_id: str, blocked: list) -> tuple[list, list]:
    """在保留准入失败的同时，执行并收集独立表或独立页的既有检查。"""
    from .rule_outcome import RuleDeferred

    excluded = {table["table_code"] for table in blocked}
    scoped = cell_scoped_table_codes(document._table_admission_receipt[1], blocked)
    try:
        if rule_id in {"V33-005", "V33-120"} and document.fact_context.report_kind == "final":
            from .native_hierarchy_checks import DETAIL_TABLES, check_native_detail_tables
            from .rules_v33 import R33005_TableTotalConsistency, R33120_DetailTableCheck

            rule = R33005_TableTotalConsistency() if rule_id == "V33-005" else R33120_DetailTableCheck()
            present = {instance.table_code for instance in document.parse_artifact.logical_tables}
            targets = [code for code in DETAIL_TABLES if code in present and (code not in excluded or code in scoped)]
            if targets:
                return check_native_detail_tables(
                    rule, document, table_codes=targets, excluded_mirror_tables=excluded,
                    require_grid_coverage_for=scoped,
                ), []
        elif rule_id == "V33-240":
            from .native_income_sources import check_income_sources, missing_header_findings
            from .rules_v33 import R33240_Table2_IncomeAdvancedCheck

            income_rule = R33240_Table2_IncomeAdvancedCheck()
            if "FIN_02_income" in scoped:
                return check_income_sources(income_rule, document, require_grid_coverage=True), []
            return missing_header_findings(income_rule, document), []
        elif rule_id == "V33-241" and "FIN_03_expenditure" in scoped:
            from .native_table_arithmetic import check_expenditure_arithmetic
            from .rules_v33 import R33241_Table3_ExpenseAdvancedCheck

            return check_expenditure_arithmetic(
                R33241_Table3_ExpenseAdvancedCheck(), document, require_grid_coverage=True,
            ), []
        elif rule_id == "V33-004":
            from .rules_v33 import R33004_CellNumberValidity

            pages, cells = number_scan_scope(
                document, blocked, native_only=getattr(document, "report_kind", None) == "unknown",
            )
            return R33004_CellNumberValidity().apply(document, excluded_pages=pages, native_cells=cells), []
        elif rule_id in {"V33-109", "V33-114"}:
            from .rules_v33 import R33109_EmptyTables_Statement, R33114_EmptyTableStatementCheck

            rule = R33109_EmptyTables_Statement() if rule_id == "V33-109" else R33114_EmptyTableStatementCheck()
            return rule.apply(document, excluded_table_codes=excluded), []
        elif rule_id == "V33-122" and "FIN_04_fiscal_grant_total" not in excluded:
            from .rules_v33 import R33122_EmptyTableCheck

            return R33122_EmptyTableCheck().apply(document, excluded_table_codes=excluded), []
    except RuleDeferred as signal:
        return list(signal.partial_issues), list(signal.unresolved_reasons) or [signal.detail]
    return [], []


def cells_by_fragment(receipt: dict) -> Dict[Any, List[dict]]:
    """未绑定金额格在回执的两层结构里都可能出现。

    ``build_table_import_receipt`` 返回外层回执，逐格明细挂在
    ``reading_quality.unbound_money_cells``；持久化的 reading_import 则把
    部分字段摊平。两处都取，避免因结构层级差异漏掉真实格。
    """
    cells = list(receipt.get("unbound_money_cells") or [])
    cells += list((receipt.get("reading_quality") or {}).get("unbound_money_cells") or [])
    seen = set()
    unique: List[dict] = []
    for cell in cells:
        key = (cell.get("fragment_uid"), cell.get("source_cell_id"), cell.get("page"),
               cell.get("raw_text"), cell.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(cell)
    grouped: Dict[Any, List[dict]] = {}
    for cell in unique:
        grouped.setdefault(cell.get("fragment_uid"), []).append(cell)
    return grouped


def number_scan_scope(document, blocked, *, native_only=False):
    """用原格和物理表区域限制数字扫描的缺口。"""
    from src.services.cell_identity import source_cell_id
    from src.services.source_reconstruction import native_source_conflict
    from src.services.table_reading_assessment import assess_table_reading, intersects, valid_box

    artifact = document.parse_artifact
    if artifact is None:
        return set(range(1, len(document.page_texts or ()) + 1)), []
    cached = getattr(document, "_table_admission_receipt", None)
    receipt = cached[1] if cached else {"reading_quality": assess_table_reading(artifact)}
    grouped = cells_by_fragment(receipt)
    fragments = {table.fragment_uid: table for table in artifact.table_fragments}
    pages_by_code = defaultdict(set)
    for instance in artifact.logical_tables:
        pages_by_code[instance.table_code].update(range(instance.page_start, instance.page_end + 1))
    for fragment in artifact.table_fragments:
        pages_by_code[fragment.table_code].add(fragment.page_number)
        pages_by_code[fragment.table_code].update(cell.page_number for cell in fragment.cells)

    # 只认解析选中的原生格及其唯一物理来源。重复坐标或同 ID 异文不能当作已证实。
    native_methods = {"lines", "lines_strict"}
    originals = defaultdict(list)
    for table in artifact.physical_fragments:
        for cell in table.cells:
            originals[source_cell_id(table, cell)].append((table, cell))
    native = {}
    counts = Counter(source_cell_id(table, cell) for table in artifact.table_fragments for cell in table.cells)
    for table in artifact.table_fragments:
        if table.extraction_method not in native_methods:
            continue
        for cell in table.cells:
            uid = source_cell_id(table, cell)
            matches = originals[uid]
            if counts[uid] != 1 or len(matches) != 1:
                continue
            source, original = matches[0]
            box = valid_box(cell.bbox)
            if (source.extraction_method in native_methods and cell.extraction_method in native_methods
                    and original.extraction_method in native_methods and box
                    and not native_source_conflict(cell)
                    and (cell.page_number, cell.raw_text, box)
                    == (original.page_number, original.raw_text, valid_box(original.bbox))):
                native[uid] = (table, cell, box)

    protected, unresolved_pages = set(), set()
    if native_only:
        protected.update(range(1, len(document.page_texts or ()) + 1))
    affected_cells = set()
    boxes = defaultdict(list)
    physical_by_uid = {table.fragment_uid: table for table in artifact.physical_fragments}

    def protect_fragment(uid, fallback_pages):
        fragment = fragments.get(uid)
        sources = {*getattr(fragment, "source_fragment_uids", ()), uid}
        regions = [(table.page_number, box) for src in sources
                   if (table := physical_by_uid.get(src)) and (box := valid_box(table.bbox))]
        protected.update(fallback_pages)
        if not fragment or not regions:
            unresolved_pages.update(fallback_pages)
            return
        for page, box in regions:
            boxes[page].append(box)
        unresolved_pages.update(set(fallback_pages) - {page for page, _ in regions})

    for entry in blocked:
        code, uid = entry["table_code"], entry.get("fragment_uid")
        table = fragments.get(uid)
        pages = ({entry["page"], *(cell.page_number for cell in table.cells)}
                 if table is not None else {entry["page"], *pages_by_code[code]})
        protected.update(pages)
        cells = grouped.get(uid, [])
        sources = [native.get(cell.get("source_cell_id")) for cell in cells]
        if (uid not in fragments or not cells or entry.get("arithmetic_conflicts")
                or entry.get("unresolved_money_cell_count") != len(cells)
                or entry.get("reasons") != Counter(cell.get("reason") for cell in cells)
                or not all(source and source[0] is fragments[uid]
                           and (source[1].page_number, source[1].raw_text, source[2])
                           == (cell.get("page"), cell.get("raw_text"), valid_box(cell.get("bbox")))
                           for source, cell in zip(sources, cells, strict=True))):
            protect_fragment(uid, pages)
            continue
        for cell, source in zip(cells, sources, strict=True):
            affected_cells.add(cell["source_cell_id"])
            boxes[cell["page"]].append(source[2])

    for gap in (receipt.get("reading_quality") or {}).get("gaps", []):
        if gap.get("source_cell_id") in affected_cells:
            continue
        page, box = gap["page"], valid_box(gap.get("bbox"))
        if not native_only and page not in protected:
            continue
        # 有原件框才缩小范围。被缺口触及的表走整表保护，无坐标的页保持原保护。
        hits = [existing for table in artifact.physical_fragments if table.page_number == page
                and (existing := valid_box(table.bbox)) and intersects(box, existing)]
        if hits:
            boxes[page].extend(hits)
        else:
            unresolved_pages.add(page)
    if native_only:
        for entry in receipt.get("mapping_incomplete_tables", []):
            if entry not in blocked:
                protect_fragment(entry.get("fragment_uid"), {entry["page"]})
    scan_pages = protected - unresolved_pages
    by_region = defaultdict(list)
    for uid, (table, cell, box) in native.items():
        if (cell.page_number in scan_pages and uid not in affected_cells
                and not any(intersects(box, gap_box) for gap_box in boxes[cell.page_number])):
            by_region[(cell.page_number, tuple(box))].append((table, cell))
    candidates = [items[0] for items in by_region.values()
                  if len({cell.raw_text for _, cell in items}) == 1]
    return protected, candidates


def cell_scoped_table_codes(receipt: dict, blocked: list) -> set[str]:
    """仅在全部准入缺口可定位到金额格时，允许依赖完整原格的规则续查。"""
    from src.services.cell_number_text import invalid_number_text

    grouped = cells_by_fragment(receipt)
    local, wide = set(), set()
    for table in blocked:
        cells = grouped.get(table.get("fragment_uid"), [])
        is_local = (bool(cells) and not table.get("arithmetic_conflicts")
                    and table.get("unresolved_money_cell_count") == len(cells)
                    and table.get("reasons") == Counter(c["reason"] for c in cells)
                    and all(c.get("source_cell_id") and c.get("page") and c.get("bbox")
                            and ((c["reason"] == "fact_unbound" and invalid_number_text(c.get("raw_text")))
                                 or (c["reason"] == "rule_admission_blocked"
                                     and c.get("localized_reading_conflict")))
                            for c in cells))
        (local if is_local else wide).add(table["table_code"])
    return local - wide


def has_cell_scoped_table(document, table_code: str) -> bool:
    cached = getattr(document, "_table_admission_receipt", None)
    context = getattr(document, "fact_context", None)
    artifact = getattr(document, "parse_artifact", None)
    if (not cached or context is None or artifact is None or context.report_kind != "final"
            or cached[0] != (artifact.source_file_hash, artifact.parse_run_uid, context.version_token)
            or context.file_hash != artifact.source_file_hash or context.parse_run_uid != artifact.parse_run_uid):
        return False
    receipt = cached[1]
    blocked = [table for table in receipt.get("mapping_incomplete_tables", [])
               if table["table_code"] == table_code]
    return table_code in cell_scoped_table_codes(receipt, blocked)


EMPTY_PREFIX = "原件表格无可用金额数据"


def classify_blocked_tables(receipt: dict, blocked: list) -> Tuple[list, list, list]:
    """把阻断表分成“只为原件值非法”和“确有技术/映射冲突”两组。

    判定条件必须全部成立才算材料缺陷：该表所有未绑定格都是单个数词且原文
    小数点重复、原因恰为 ``fact_unbound``、且与上游计数一致。任何一条不满足
    （身份冲突、列未映射、标签缺失、算术冲突、计数不吻合）都按技术冲突处理，
    不借格式归类放行。
    """
    from src.services.cell_number_text import invalid_number_text

    grouped = cells_by_fragment(receipt)
    material: List[tuple] = []
    technical: list = []
    empty: list = []
    for table in blocked:
        cells = grouped.get(table.get("fragment_uid"), [])
        reasons = {str(k): int(v) for k, v in (table.get("reasons") or {}).items()}
        format_cells = [
            cell for cell in cells
            if invalid_number_text(cell.get("raw_text"))
            and str(cell.get("reason") or "") in FORMAT_EXPLAINED_REASONS
        ]
        format_only = (
            bool(format_cells)
            and len(format_cells) == len(cells)
            and set(reasons) == {"fact_unbound"}
            and reasons.get("fact_unbound") == len(format_cells)
        )
        if format_only:
            material.append((table, format_cells))
        elif (not cells and not reasons and not table.get("unresolved_money_cell_count")
              and not table.get("arithmetic_conflicts")):
            # 没有任何未绑定格、也没有任何逐格原因，却被标为映射不完整：
            # 这是“表里没有可用金额事实”（空表/缺表），属材料侧，不是系统
            # 读取故障。第 4/1 份的 p11/p12 空表与缺表即此形态。
            empty.append(table)
        else:
            technical.append(table)
    return material, technical, empty


def _cell_pages(table: dict, cells: list) -> list:
    pages = {cell.get("page") for cell in cells if isinstance(cell.get("page"), int)}
    return sorted(pages) or [table["page"]]


def build_message(receipt: dict, material: list, technical: list, empty: list = ()) -> str:
    """材料侧与技术侧分别列示，不互相吞掉。"""
    grouped = cells_by_fragment(receipt)
    parts: List[str] = []
    for table, cells in material:
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        cell_refs = "、".join(
            f"{table['table_code']}:{cell.get('source_cell_id') or cell.get('row')}"
            for page in _cell_pages(table, cells)
            for cell in cells if (cell.get("page") or table["page"]) == page
        )
        parts.append(
            f"原件金额格式非法：{refs} 有金额格原文无法解析为数值"
            f"（{cell_refs}），数值不确定，需回原件补正；依赖勾稽保持材料阻断"
        )
    for table in empty:
        pages = "、".join(str(page) for page in _cell_pages(table, []))
        parts.append(
            f"{EMPTY_PREFIX}：第{pages}页{table['table_code']}《{table.get('title') or table['table_code']}》"
            "没有可用金额事实（空表或缺表），需按材料补正核对"
        )
    for table in technical:
        cells = grouped.get(table.get("fragment_uid"), [])
        refs = "、".join(f"第{page}页{table['table_code']}" for page in _cell_pages(table, cells))
        parts.append(f"{_ADMISSION_PREFIX}：{refs} {table['reasons']}")
    return "；".join(parts)


def register_material_format_gap(document, material: list, message: str) -> None:
    """把材料格式缺陷登记成单元格级结构化事实（不是整表技术故障）。

    原因码只从冻结表里选：``cell_unbound`` 的允许动作含人工确认/系统重读，
    比 ``table_mapping_incomplete``（只允许 tech_fix）更符合“回原件补正”。
    """
    from .diagnostic_fields import record_gap, table_missing_operand

    blocked_by: List[dict] = []
    missing: List[dict] = []
    for table, cells in material:
        for cell in cells:
            page = cell.get("page") if isinstance(cell.get("page"), int) else table["page"]
            ref = f"第{page}页{table['table_code']}:{cell.get('source_cell_id') or cell.get('row')}"
            blocked_by.append({"dimension": "cell", "ref": ref, "reason_code": "cell_unbound"})
            missing.append(table_missing_operand(
                table_code=str(table["table_code"]),
                role="金额格原文非法，数值不确定",
                page=page,
                row_key=cell.get("source_cell_id") or cell.get("row"),
                column="金额",
            ))
    if not blocked_by:
        return
    record_gap(document, [message], reason_codes=["cell_unbound"],
               missing_operands=missing, blocked_by=blocked_by,
               preferred_action="human_confirm")


def register_empty_table_gap(document, empty: list, message: str) -> None:
    """空表/缺表按材料侧登记：原因码 table_missing，位置是表所在页。"""
    from .diagnostic_fields import record_gap, table_missing_operand

    blocked_by: List[dict] = []
    missing: List[dict] = []
    for table in empty:
        ref = f"第{table['page']}页{table['table_code']}"
        blocked_by.append({"dimension": "instance_uid", "ref": ref, "reason_code": "table_missing"})
        missing.append(table_missing_operand(
            table_code=str(table["table_code"]), role="整表无可用金额事实",
            page=table["page"], column="金额"))
    if not blocked_by:
        return
    record_gap(document, [message], reason_codes=["table_missing"],
               missing_operands=missing, blocked_by=blocked_by,
               preferred_action="human_confirm")


def reclassify_admission_deferral(document, rule_id: str, detail: str) -> str | None:
    """改写表格准入延期文本；不是该前缀时返回 None。

    在 ``pipeline`` 捕获 ``RuleDeferred`` 之后调用。
    """
    if not detail.startswith(_ADMISSION_PREFIX):
        return None
    cached = getattr(document, "_table_admission_receipt", None)
    if not cached:
        return None
    from .rule_identity import LEGACY_TABLE_INPUTS, effective_legacy_table_inputs

    if rule_id not in LEGACY_TABLE_INPUTS:
        return None
    codes = effective_legacy_table_inputs(rule_id, getattr(document, "fact_context", None))
    receipt = cached[1]
    blocked = [
        table
        for table in receipt.get("mapping_incomplete_tables", [])
        if (codes is None and str(table["table_code"]).startswith(("FIN_", "BUD_")))
        or (codes is not None and table["table_code"] in codes)
    ]
    if not blocked:
        return None
    material, technical, empty = classify_blocked_tables(receipt, blocked)
    if not material and not technical and not empty:
        return None
    message = build_message(receipt, material, technical, empty)
    if material and not technical:
        register_material_format_gap(document, material, message)
    elif empty and not technical and not material:
        register_empty_table_gap(document, empty, message)
    return message
