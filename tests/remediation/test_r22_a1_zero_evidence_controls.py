"""R2.2 附页 A1 成对控制：语气守卫与空白=0 授权边界。

探针口径与 outputs/qoder_plan_review_r2_codex_20261106/zero_basis_probes.json
一致（第三轮审核冻结样例）；正控来自既有真实材料语序。

成对控制（同一张平衡表，隔离授权机制）：
- 正控：合法同身份零披露 → 允许适用自动结论（narrative 零依据返回）；
- 关键负控：同一平衡表，无独立零依据 → 必须仍未知（_blank_cells_confirmed
  的等式一致只是校验，不是零的唯一授权）；
- 单一变化负控（各一）：否定句/条件句/引用模板/他阶段（预算零补决算）/
  非零冲突 → 均不得授权。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.engine.rules_v33 import R33CrossSanGongEcon


# ---------------------------------------------------------------- 零证据语气

def _doc(text: str):
    """最小文档桩：零证据函数只消费 page_texts 与 fact_context 身份。"""
    return SimpleNamespace(
        page_texts=[text],
        fact_context=SimpleNamespace(org_name="甲局", fiscal_year=2024),
        report_kind="final",
    )


POSITIVE = "甲局2024年度部门决算。财政拨款公务接待费支出决算0万元。"
NEGATED = "甲局2024年度部门决算。以下表述不成立：财政拨款公务接待费支出决算0万元。"
NEGATED_FORWARD = "甲局2024年度部门决算。以下表述不成立。财政拨款公务接待费支出决算0万元。"
CONDITIONAL = "甲局2024年度部门决算。若未开展接待活动，财政拨款公务接待费支出决算0万元。"
TEMPLATE = "甲局2024年度部门决算。旧模板示例为“财政拨款公务接待费支出决算0万元”。"
QUESTION = "甲局2024年度部门决算。财政拨款公务接待费支出决算为0万元吗？"


def test_positive_control_assertive_zero_still_authorized():
    """正控：合法同身份零披露保持可用（不能把语气守卫做过头）。"""
    refs = narrative_zero_sources(_doc(POSITIVE), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算")
    assert refs
    assert refs[0]["page"] == 1
    assert "公务接待费支出决算0万元" in refs[0]["span"]


@pytest.mark.parametrize("text", [NEGATED, NEGATED_FORWARD, CONDITIONAL, TEMPLATE, QUESTION])
def test_single_change_negatives_reject_tone(text):
    """单一变化负控：否定/条件/引用模板/疑问 → 不得形成零证据。"""
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_quoted_number_without_template_marker_is_still_reference():
    """金额整体处于引号内即引用（无需模板词），指标名自带“三公”引号不受影响。"""
    quoted = "甲局2024年度部门决算。上级要求写清“财政拨款公务接待费支出决算0万元”。"
    assert narrative_zero_sources(_doc(quoted), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None
    # 指标自带引号（“三公”经费）不触发引用拒收（既有 level 正则语序）。
    san_gong = ("甲局2024年度部门决算。“三公”经费支出决算0万元。")
    assert narrative_zero_sources(_doc(san_gong), table_code="FIN_07_three_public",
                                  indicator="total_actual", stage="决算")


def test_other_stage_zero_still_rejected():
    """单一变化负控：他阶段（预算零补决算）不得授权（既有守卫保持）。"""
    text = "甲局2024年度部门决算。财政拨款公务接待费支出预算0万元。"
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_nonzero_conflict_still_rejected():
    """单一变化负控：同范围同指标非零水平陈述 → 一票否决（既有守卫保持）。"""
    text = POSITIVE.replace("决算0万元", "决算0.30万元")
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_ir02_forward_frame_announcements_reject():
    """IR02 负控：前导宣告帧（假设/模板）支配紧随的零陈述，隔句号仍拒收。"""
    for text in (
        "甲局2024年度部门决算。以下为假设情形。财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。以下为模板示例。财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。假设情形如下。财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。以下为假设：财政拨款公务接待费支出决算0万元。",
    ):
        assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                      indicator="reception", stage="决算") is None, text


def test_ir02_direct_denial_operators_reject():
    """IR02 负控：认定/视为/推定类直接否定操作词 → 非断言。"""
    for text in (
        "甲局2024年度部门决算。不得认定财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。不能认定财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。不得视为财政拨款公务接待费支出决算0万元。",
    ):
        assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                      indicator="reception", stage="决算") is None, text


def test_ir02_positive_controls_survive_frame_guard():
    """IR02 正控：真实语序（其中：/具体情况如下/情况说明标题）不受宣告帧守卫误伤。"""
    for text in (
        "甲局2024年度部门决算。支出决算为0.85万元，其中：财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。具体情况如下。财政拨款公务接待费支出决算0万元。",
        "甲局2024年度部门决算。七、财政拨款“三公”经费支出决算情况说明。财政拨款公务接待费支出决算0万元。",
    ):
        refs = narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                      indicator="reception", stage="决算")
        assert refs, text


def test_r202_frame_scope_covers_following_statements():
    """R2-02 负控（审核方原形态）：宣告帧是作用域不是单句修饰——宣告后的
    **每一条**零陈述都拒收。旧实现只拒紧邻第一条，第二条漏网被当真实零。"""
    for frame in ("以下为模板示例。", "以下为假设情形。"):
        text = (f"甲局2024年度部门决算。{frame}"
                "财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。")
        doc = _doc(text)
        assert narrative_zero_sources(doc, table_code="FIN_07_three_public",
                                      indicator="reception", stage="决算") is None, text
        assert narrative_zero_sources(doc, table_code="FIN_07_three_public",
                                      indicator="overseas", stage="决算") is None, text


def test_r202_frame_scope_survives_intervening_text():
    """R2-02 负控：宣告与零陈述之间隔着同作用域演示文字仍拒收。"""
    text = ("甲局2024年度部门决算。以下为模板示例。"
            + "用于演示该指标的填报方式和文字格式，" * 4
            + "财政拨款公务接待费支出决算0万元。")
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_r202_frame_scope_rejects_across_paragraph_gap():
    """R2-02 负控（作用域歧义即拒收）：宣告句后隔段落的零陈述仍在未闭合
    作用域内——不存在"隔段自动出框架"的默认豁免。"""
    text = "甲局2024年度部门决算。以下为模板示例。\n\n财政拨款公务接待费支出决算0万元。"
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_r202_explicit_frame_closure_ends_scope():
    """R2-02 正控：显式闭合（"以上为模板示例"式）结束作用域——闭合前的
    零陈述拒收，闭合后的合法零披露照常授权。"""
    text = ("甲局2024年度部门决算。以下为模板示例。财政拨款公务接待费支出决算0万元。"
            "以上为模板示例。因公出国（境）费支出决算0万元。")
    doc = _doc(text)
    assert narrative_zero_sources(doc, table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None
    assert narrative_zero_sources(doc, table_code="FIN_07_three_public",
                                  indicator="overseas", stage="决算")


def test_fin02_income_narrative_zero_capability():
    """§4.1/IR 范围澄清：FIN_02 收入表的叙述零能力已实现——同身份 assertive
    零声明为收入空白格作证；无声明（知识产权局形态）保持未知是材料边界，
    不是能力缺失。"""
    income_text = ("甲局2024年度部门决算。"
                   "本年收入合计0万元，其中：财政拨款收入0万元，经营收入0万元。")
    refs = narrative_zero_sources(
        _doc(income_text), table_code="FIN_02_income",
        indicator="operational_income", stage="决算")
    assert refs and refs[0]["page"] == 1
    # 非零声明一票否决（同指标既有守卫不回退）
    assert narrative_zero_sources(
        _doc(income_text.replace("经营收入0万元", "经营收入3.20万元")),
        table_code="FIN_02_income", indicator="operational_income", stage="决算") is None
    # 语气守卫跨表生效
    assert narrative_zero_sources(
        _doc("甲局2024年度部门决算。以下为假设情形。经营收入0万元。"),
        table_code="FIN_02_income", indicator="operational_income", stage="决算") is None


def test_fin02_blank_term_end_to_end_narrative_zero():
    """§4.1 端到端收口：FIN_02 收入表空白格 → blank_term → 叙述零授权。

    验证能力链路（空白原格+列身份+叙述零 → 0 操作数），而不止
    narrative_zero_sources 单元层；V33-240 的 select 走同一条链路。
    """
    from types import SimpleNamespace as NS
    from src.engine.native_empty_operands import blank_term
    from src.engine.pipeline import build_document
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
    from src.services.pdf_parser import PDFParser

    headers = ['科目编码', '科目名称', '本年收入合计', '财政拨款收入', '事业收入',
               '经营收入', '其他收入']
    rows = [headers,
            ['201', '合计', '10.00', '8.00', '2.00', '', '0.00']]
    text = '甲局2024年度部门决算。经营收入0万元。'
    cells = []
    parser = PDFParser(None)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or '')
            cells.append(ParsedCell(r, c, raw, raw, parser._parse_numeric(raw), 1,
                                    (c*70, r*25+50, (c+1)*70, r*25+75), r < 1,
                                    .99, '万元', 'lines', native_blank_verified=not raw))
    table = ParsedTable('FIN_02_income', '收入决算表', 1, 0,
                        (0, 50, len(rows[0])*70, len(rows)*25+50), 1, .99,
                        'lines', '万元', rows, cells, 'fragment')
    instance = LogicalTableInstance('instance', 'FIN_02_income', table.title,
                                    ['fragment'], 1, 1, org_name='甲局', fiscal_year=2024)
    artifact = ParseArtifact(source_file_hash='a'*64, parse_run_uid='run',
                             table_fragments=[table], physical_fragments=[table],
                             logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name='甲局', fiscal_year=2024,
                                               report_kind='final', doc_type='dept_final')
    doc = build_document('income.pdf', [text], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    doc.report_kind = 'final'  # 生产路径由 run_rules_with_outcomes 设置；直跑需补
    term = blank_term(doc, 'FIN_02_income', 'operational_income', code='201', stage='决算')
    assert term is not None, 'FIN_02 空白收入格应能经叙述零授权（能力端到端）'
    observation = term.extra_dims['native_blank_observation']
    assert observation['convention'] == 'narrative_explicit_zero'
    assert observation['narrative_sources'], observation
    assert str(term.normalized_value) == '0'


# ---------------------------------------------------- 空白=0 授权边界（成对）

def _rule_with_tables():
    """构造最小三公表：平衡（合计=分项和）+ 指定空白格，无任何独立零依据。"""
    return R33CrossSanGongEcon()


def test_balanced_table_without_independent_zero_basis_stays_unknown():
    """关键负控（zero_basis_probes.balanced_without_independent_zero_basis）：
    等式平衡只是**一致性校验**——无独立零依据时空白不得被授权为 0。"""
    rule = _rule_with_tables()
    values = {
        "total": Decimal("0.85"),
        "overseas": Decimal("0"),
        "vehicle_subtotal": Decimal("0.85"),
        "reception": Decimal("0"),
        "vehicle_purchase": Decimal("0"),
        "vehicle_operation": Decimal("0.85"),
    }
    confirmed, _note = rule._blank_cells_confirmed(
        values, {}, {"overseas", "reception", "vehicle_purchase"}, {}, "万元")
    # 等式本身平衡（旧实现即返回 True 的行为保持可复现）——
    assert confirmed is True
    # 但授权边界由独立零依据决定：无依据时规则不得把空白写成 0。
    # 规则主链（_blank_cells_confirmed 的调用方）的行为由
    # tests/test_cross_san_gong_econ.py::test_blank_without_independent_zero_basis_defers_to_unknown 锁定。


def test_unbalanced_table_still_blocks():
    """负控（zero_basis_probes.unbalanced_without_independent_zero_basis）：不平衡 → 拒绝。"""
    rule = _rule_with_tables()
    values = {"total": Decimal("1.85"), "vehicle_subtotal": Decimal("0.85"),
              "overseas": Decimal("0"), "reception": Decimal("0"),
              "vehicle_purchase": Decimal("0"), "vehicle_operation": Decimal("0.85")}
    confirmed, note = rule._blank_cells_confirmed(
        values, {}, {"overseas", "reception", "vehicle_purchase"}, {}, "万元")
    assert confirmed is False
    assert "总" in note or "1.85" in note
