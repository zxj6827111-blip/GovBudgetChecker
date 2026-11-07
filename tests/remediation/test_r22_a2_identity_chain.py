"""R2.2 附页 A2：名称独立身份链（QP-04）。

判据（计划闭合版）：
1. 原文显式编码 → 直接身份约束（须原件可核）；
2. 无编码时：款名/项名在完整原表逐字唯一 + 链一致 + 原件行共现；
3. 金额事实保留原有准入限制（低置信/ambiguous 不因名称绑定放行——本文件
   不覆盖该准入，由取数层守住）；
4. 独立身份不成立 → 金额仍未知，名称差异只作提示。

负控（计划 A2）：禁"农林→农林水"近似特例；同名跨父级不绑定；原件两个
候选而解析只产出一个时不得凭"已解析唯一"绑定（原件共现核验缺席即拒绝）；
禁金额相同反推身份（binder 从不读金额）。
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.engine.native_final_disclosure import (
    _bind_klass_via_independent_chain,
    _chain_visible_in_original,
    _hierarchy_fact,
    _name,
)
from src.services.fiscal_code_hierarchy import hierarchy_paths
from src.schemas.parse_artifact import FiscalFact

ORG = "上海市某局"
YEAR = 2024


def _fact(code, name, raw="100.00"):
    from src.schemas.parse_artifact import EvidenceRef

    return FiscalFact(
        fact_uid=f"fact-{code}",
        indicator="total_actual",
        raw_value=raw,
        unit="万元",
        normalized_value=Decimal(raw.replace(",", "")),
        org_name=ORG,
        org_scope="部门",
        fiscal_year=YEAR,
        period="本年",
        fund_scope="general_public_budget",
        budget_stage="决算",
        subject_code=code,
        subject_name=name,
        table_code="FIN_05_general_public_expenditure",
        instance_uid="instance-1",
        extra_dims={"evidence_refs": [EvidenceRef(
            file_hash="a" * 64, physical_page=2, source_cell_id=f"cell-{code}",
            quote=f"{code} {name}", location_level="exact_cell").to_dict()]},
    )


def _facts():
    return [
        _fact("213", "农林水支出"),
        _fact("21303", "水利"),
        _fact("2130314", "防汛"),
        _fact("214", "交通运输支出"),
        _fact("21403", "水利"),  # 同名跨父级（另一个类下也有"水利"款）
    ]


def _doc(pages):
    return SimpleNamespace(page_texts=list(pages), fact_context=None, report_kind="final",
                           org_name=ORG, fiscal_year=YEAR)


TABLE_PAGES = [
    "一般公共预算财政拨款支出决算表\n单位：万元\n",
    "  213 农林水支出 100.00 90.00 10.00\n"
    "  21303 水利 100.00 90.00 10.00\n"
    "  2130314 防汛 60.00 55.00 5.00\n",
]


_ITEM_PATTERN = re.compile(
    r"(?P<klass>[^\d，,。；;：:、.()“”]{1,60}?)\(类\)"
    r"(?P<kuan>[^\d，,。；;：:、.()“”]{1,60}?)\(款\)"
    r"(?P<xiang>[^\d，,。；;：:、.()“”]{1,60}?)\(项\)")


def _item_match(klass, kuan, xiang):
    """条目 Match 与引擎同管道：NFKC + 去空白后的 compact 文本上匹配。"""
    from src.engine.native_narrative_checks import _Text

    text = f"23、{klass}（类）{kuan}（款）{xiang}（项）。支出决算为 60.00 万元。"
    compact = _Text.from_pages([text]).compact
    match = _ITEM_PATTERN.search(compact)
    assert match, compact
    return match


def test_chain_binds_when_unique_and_visible_in_original():
    """判据 2 正控：款/项逐字唯一 + 链一致 + 原件行共现 → 绑定类级并记录依据。"""
    doc = _doc(TABLE_PAGES)
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    aliases = []
    klass_code = _bind_klass_via_independent_chain(doc, facts, item, paths, aliases)
    assert klass_code == "213"
    roles = {a["role"] for a in aliases}
    assert "independent_hierarchy_chain" in roles
    difference = next(a for a in aliases if a["role"] == "klass_name_difference")
    assert difference["narrative_name"] == "农林支出"
    assert "农林水" in difference["table_name"]


def test_missing_water_char_alone_never_binds():
    """负控：仅凭"农林支出"近似"农林水支出"（差一字）不得绑定。"""
    doc = _doc(["一般公共预算财政拨款支出决算表\n"])  # 原件无表格行 → 共现缺失
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, []) is None


def test_same_kuan_name_under_two_klasses_never_binds():
    """负控：同名款跨父级（21303 与 21403 都叫"水利"）→ 全表不唯一，不绑定。"""
    doc = _doc(TABLE_PAGES)
    facts = _facts()
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, []) is None


def test_original_two_candidates_parsed_one_never_binds():
    """IR03 关键负控：原件两个候选而**解析只产出一个** → 不得绑定。

    解析 facts 只含 213/21303/2130314 链；第二条 214/21403 水利/2140314 防汛
    链只存在于原件页文本（解析漏行）。旧实现"facts 唯一 + 原文至少出现一次"
    会误绑 213；正确行为是唯一性以完整原表扫描为权威 → 拒绝绑定。
    """
    doc = _doc([
        "一般公共预算财政拨款支出决算表\n单位：万元\n",
        "  213 农林水支出 100.00\n  21303 水利 100.00\n  2130314 防汛 60.00\n"
        "  214 交通运输支出 200.00\n  21403 水利 200.00\n  2140314 防汛 200.00\n",
    ])
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    aliases = []
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, aliases) is None
    assert aliases == []


def test_original_duplicates_parsed_one_never_binds():
    """IR03 负控（同页重复形态）：原件同表两行同名款，解析只留一条 → 拒绝。"""
    doc = _doc([
        "一般公共预算财政拨款支出决算表\n单位：万元\n",
        "  213 农林水支出 100.00\n  21303 水利 100.00\n  2130314 防汛 60.00\n"
        "  215 农林水支出 90.00\n  21503 水利 90.00\n  2150314 防汛 90.00\n",
    ])
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, []) is None


def test_explicit_code_in_narrative_binds():
    """判据 1 正控：条目行带显式 3 位类编码且表内唯一 → 直接身份约束。"""
    doc = _doc(TABLE_PAGES)
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    item = _item_match("农林支出", "水利", "防汛")
    # 条目行须能在原件页文本中找到（_item_source_line 按行匹配）
    doc.page_texts = [
        "一般公共预算财政拨款支出决算表\n单位：万元\n",
        "  213 农林水支出 100.00\n  21303 水利 100.00\n  2130314 防汛 60.00\n",
        "23、213 农林支出（类）水利（款）防汛（项）。支出决算为 60.00 万元。\n",
    ]
    aliases = []
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, aliases) == "213"
    assert any(a["role"] == "explicit_klass_code_in_narrative" for a in aliases)


def test_hierarchy_fact_falls_back_to_unknown_without_chain():
    """判据不成立 → 金额仍未知（text_binding_failed），名称差异只进提示通道。"""
    # doc 页文本须包含条目原文（_record_text_gap 按 item 偏移取原文出处）
    narrative = "23、农林支出（类）水利（款）防汛（项）。支出决算为 60.00 万元。"
    doc = _doc([narrative])
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    from src.engine.native_narrative_checks import _Text

    compact = _Text.from_pages([narrative]).compact
    item = _ITEM_PATTERN.search(compact)
    assert item
    fact, error = _hierarchy_fact(doc, facts, item)
    assert fact is None
    assert "找不到同名科目" in str(error)
    assert "农林支出" in str(error)


def test_chain_visible_in_original_requires_every_level():
    """原件共现逐级核验：任何一级核不进原件行即拒绝。"""
    doc = _doc(TABLE_PAGES)
    assert _chain_visible_in_original(doc, [("213", "农林水支出"), ("21303", "水利"),
                                            ("2130314", "防汛")])
    assert not _chain_visible_in_original(doc, [("213", "农林水支出"), ("21303", "水利"),
                                                ("2130399", "防汛")])  # 编码不在原件
    assert not _chain_visible_in_original(doc, [("213", "农林水支出"), ("21303", "乡镇水利"),
                                                ("2130314", "防汛")])  # 名称对不上


def test_amount_digits_on_item_line_are_not_codes():
    """负控：条目行内（项）之后的金额数字（如 7,650 里的 650）不当作类编码。

    判据 1 只扫类名标记之前的行前缀；金额数字不进显式编码判据，
    也不得阻断判据 2 的合法链绑定。
    """
    doc = _doc(TABLE_PAGES)
    facts = [_fact("213", "农林水支出"), _fact("21303", "水利"), _fact("2130314", "防汛")]
    paths = hierarchy_paths({str(f.subject_code) for f in facts})
    # 行内带大额金额（含 3 位数字段 650），且页文本把该行印在说明页
    doc.page_texts = [
        "一般公共预算财政拨款支出决算表\n单位：万元\n",
        "  213 农林水支出 100.00\n  21303 水利 100.00\n  2130314 防汛 60.00\n",
        "23、农林支出（类）水利（款）防汛（项）。支出决算为 7,650.32 万元。\n",
    ]
    item = _item_match("农林支出", "水利", "防汛")
    aliases = []
    # 判据 2 正常走链绑定（金额数字不干扰）
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, aliases) == "213"
    assert any(a["role"] == "independent_hierarchy_chain" for a in aliases)


def test_r203_continuation_page_candidate_never_misbinds():
    """R2-03（审核方跨页控制复刻）：facts 只剩 213 链且证据在第 1 页时，
    逻辑表范围（1—2 页）第 2 页续表里的 214/21403 水利/2140314 防汛候选
    必须参与唯一性裁决——解析幸存事实不能自证续页不存在，范围完整性不能
    由 facts 证明。旧实现看到事实证据页就提前返回，只扫第 1 页误绑 213。"""
    from src.engine.native_final_disclosure import GENERAL

    facts = [
        SimpleNamespace(subject_code=c, subject_name=n, indicator="total_actual",
                        table_code=GENERAL,
                        extra_dims={"evidence_refs": [{"physical_page": 1}]})
        for c, n in [("213", "农林水支出"), ("21303", "水利"), ("2130314", "防汛")]
    ]
    paths = hierarchy_paths({"213", "21303", "2130314"})
    item = _item_match("农林支出", "水利", "防汛")
    first = "一般公共预算财政拨款支出决算表\n213 农林水支出\n21303 水利\n2130314 防汛\n"
    second = "一般公共预算财政拨款支出决算表（续）\n214 交通运输支出\n21403 水利\n2140314 防汛\n"

    # 形态一：解析产物声明逻辑表范围 1—2 → 扫描完整范围 → 两候选不唯一
    doc = SimpleNamespace(
        page_texts=[first, second],
        parse_artifact=SimpleNamespace(logical_tables=[
            SimpleNamespace(table_code=GENERAL, page_start=1, page_end=2)]))
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, []) is None

    # 形态二：无产物范围信息 → 全页扫描（按构造完整）→ 同样不绑定
    doc_no_artifact = SimpleNamespace(page_texts=[first, second])
    assert _bind_klass_via_independent_chain(doc_no_artifact, facts, item, paths, []) is None

    # 形态三：产物声明范围越界（1—9 > 文档 2 页）→ 产物不完整，退全页
    doc_overrun = SimpleNamespace(
        page_texts=[first, second],
        parse_artifact=SimpleNamespace(logical_tables=[
            SimpleNamespace(table_code=GENERAL, page_start=1, page_end=9)]))
    assert _bind_klass_via_independent_chain(doc_overrun, facts, item, paths, []) is None


def test_r203_single_page_with_complete_range_still_binds():
    """R2-03 正控：范围完整（1—1 页）且原表唯一候选 → 照常绑定（水务局
    真件路径不回退）；事实证据页与产物范围取并集参与扫描。"""
    from src.engine.native_final_disclosure import GENERAL

    facts = [
        SimpleNamespace(subject_code=c, subject_name=n, indicator="total_actual",
                        table_code=GENERAL,
                        extra_dims={"evidence_refs": [{"physical_page": 1}]})
        for c, n in [("213", "农林水支出"), ("21303", "水利"), ("2130314", "防汛")]
    ]
    paths = hierarchy_paths({"213", "21303", "2130314"})
    item = _item_match("农林支出", "水利", "防汛")
    first = "一般公共预算财政拨款支出决算表\n213 农林水支出\n21303 水利\n2130314 防汛\n"
    doc = SimpleNamespace(
        page_texts=[first],
        parse_artifact=SimpleNamespace(logical_tables=[
            SimpleNamespace(table_code=GENERAL, page_start=1, page_end=1)]))
    aliases = []
    assert _bind_klass_via_independent_chain(doc, facts, item, paths, aliases) == "213"
    assert any(a.get("role") == "independent_hierarchy_chain" for a in aliases)
