"""V33-TXT-FUND-DETAIL：基金/国资决算表 × 对应说明 逐项支出一致性。

真值来源（冻结，不得改来迁就实现）
--------------------------------
- 材料：上海市普陀区人民政府宜川路街道办事处 2025 年度部门决算（41 页）
- 源 PDF SHA-256：``f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03``
  （与 ``outputs/sample_validation_20260916/1/metadata.json`` 记录的 2026-09-16
  证据同一份文件；与 WP4-A 共用同一份冻结真值夹具）
- 人工判定：``outputs/sample_validation_20260916/comparison.md`` 的 Y03
  （程度：高）——「基金支出说明不一致：基金表各相关行 105.00 万元；说明总额
  105.00，但唯一项目及其决算写 135.00，差 30.00 万元。」物理页 25（基金表）、
  37（说明第八节）。该判定原文登记「漏报；未报告」——本文件的正例就是把这条
  漏报变成回归。

夹具口径（诚实声明）：
- FIN_08 逐项正例/反例：**真实 truth**（宜川样张）；
- FIN_09 逐项路径：仓库无国资真实正例，用**结构化 contract fixture**
  （合成但业务身份严格：完整表头 + 三级层级 + 类款项说明句）——它是 contract
  test，不是真实 truth；FIN_09 的真实样张只覆盖"空表 + 无收支说明"路径
  （宜川 P26/P37、生态环境局 P19/P27）。

这里只断言**业务事实**：两侧金额、差额、页码、表身份、严重度、以及"不报什么"。
不把实现细节（列定位方式、绑定正则）写进期望值。
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_COMPLETED,
    OBLIGATION_NOT_EXECUTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import build_document, run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import STATUS_FAIL, RuleDeferred  # noqa: E402
from src.engine.rules_v33 import R33TxtFundDetail  # noqa: E402

RULE_ID = "V33-TXT-FUND-DETAIL"
OBLIGATION_ID = "OBL-TXT-FUND-DETAIL"

TRUTH_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
#: 源 PDF SHA（溯源）：宜川路街道 2025 年度决算（Y03 真值所在文件）
TRUTH_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
#: 夹具内容 SHA（防夹具本体被改；与源 SHA 双锁）
TRUTH_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 另一份真实样张（生态环境局）作为"无相关业务"负例：
#: FIN_08/FIN_09 均为空表，说明写"2025 年度无政府性基金预算财政拨款收入和
#: 支出""无国有资本经营预算财政拨款收入和支出"——依法无相关收支，不得造假 finding。
SAMPLE_FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"
SAMPLE_SOURCE_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"
SAMPLE_FIXTURE_SHA = "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1"

FIN_08_TITLE = "政府性基金预算财政拨款收入支出决算表"
FIN_09_TITLE = "国有资本经营预算财政拨款收入支出决算表"

#: 宜川样张物理页（1 基）：25 = 基金表，37 = 说明第八节（基金）/第九节（国资）
PAGE_FIN_08 = 25
PAGE_FUND_NARRATIVE = 37


# ---------------------------------------------------------------------------
# 夹具装载（fail-closed：缺失或不符即失败，不静默跳过）
# ---------------------------------------------------------------------------


def _load_fixture(path: Path, source_sha: str, fixture_sha: str) -> Dict[str, Any]:
    assert path.exists(), f"固定夹具缺失: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fixture_sha, (
        f"夹具内容哈希不符（{path.name}）——夹具被修改或需重新生成"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_pdf_sha256"] == source_sha, f"夹具源 PDF SHA 不符（{path.name}）"
    return payload


@pytest.fixture(scope="module")
def truth() -> Dict[str, Any]:
    """宜川路街道 2025 年度决算（Y03 漏报的冻结真值样张）。"""
    return _load_fixture(TRUTH_FIXTURE, TRUTH_SOURCE_SHA, TRUTH_FIXTURE_SHA)


@pytest.fixture(scope="module")
def no_business_sample() -> Dict[str, Any]:
    """生态环境局 2025 年度决算（无基金/国资业务的真实负例）。"""
    return _load_fixture(SAMPLE_FIXTURE, SAMPLE_SOURCE_SHA, SAMPLE_FIXTURE_SHA)


def _document(payload: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{payload.get('doc_id', 'sample')}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )


def _clone(payload: Dict[str, Any], mutate: Any) -> Any:
    """深拷贝一份夹具并施加变异，返回 Document（不动冻结夹具本体）。"""
    data = {
        "doc_id": payload.get("doc_id", "sample"),
        "page_texts": list(payload["page_texts"]),
        "page_tables": copy.deepcopy(payload["page_tables"]),
    }
    mutate(data)
    return _document(data)


def _run(doc: Any) -> List[Any]:
    return list(R33TxtFundDetail().apply(doc))


def _rule_detail(exc: RuleDeferred) -> str:
    return f"{exc.detail} {' '.join(exc.unresolved_reasons or [])}"


def _truth_mutate_narrative(data: Dict[str, Any], old: str, new: str) -> None:
    """对真值 P37 说明页做单点文本变异（fail-closed：找不到原文即测试失败）。"""
    page = data["page_texts"][PAGE_FUND_NARRATIVE - 1]
    assert old in page, f"P37 未找到待变异原文: {old!r}"
    data["page_texts"][PAGE_FUND_NARRATIVE - 1] = page.replace(old, new)


# ---------------------------------------------------------------------------
# 正例：Y03 真值必须命中（FIN_08 真实路径）
# ---------------------------------------------------------------------------


def test_y03_truth_reports_the_item_level_conflict(truth):
    """Y03（高）：表内 2219899 本年支出 105.00 vs 说明项金额 135.00，必须报。"""
    issues = _run(_document(truth))

    assert len(issues) == 1, [issue.message for issue in issues]
    assert issues[0].rule == RULE_ID
    assert issues[0].severity == "error", "逐项金额矛盾不得降级为提示"

    location = issues[0].location
    assert location["code"] == "2219899"
    assert location["row"] == "2219899 其他住房保障支出"
    assert location["table_amount"] == "105.00"
    assert location["narrative_amount"] == "135.00"
    assert location["difference"] == "30.00"
    assert location["unit"] == "万元"
    assert location["fiscal_year"] == 2025
    assert location["fund_scope"] == "政府性基金"
    assert location["obligation_id"] == OBLIGATION_ID


def test_y03_binding_ignores_other_amounts_in_the_same_section(truth):
    """measure 绑定（反例 E）：说明同段有 105/105/0.00/135 四个数。

    「年初预算为 0.00」与收支总额 105.00 都不是项金额；只有「（项）」后
    紧跟的 135.00 是项金额。绑错任何一个都会让 finding 的金额侧失真。
    """
    issues = _run(_document(truth))
    assert issues[0].location["narrative_amount"] == "135.00"
    assert issues[0].location["table_amount"] == "105.00"


def test_y03_budget_number_in_same_paragraph_is_never_bound(truth):
    """反例 E 加固（Mutation B 守卫）：把同段年初预算改成 888.00 也不得被绑定。"""

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(data, "年初预算为 0.00 万元", "年初预算为 888.00 万元")

    issues = _run(_clone(truth, mutate))
    assert len(issues) == 1
    assert issues[0].location["narrative_amount"] == "135.00"
    assert "888" not in issues[0].message


def test_y03_findings_carry_locatable_evidence_for_both_sides(truth):
    """证据契约（任务 §14）：finding 必须同时回答表格侧与说明侧"哪页、哪项、多少"。"""
    issues = _run(_document(truth))
    location = issues[0].location

    assert location["table"] == FIN_08_TITLE
    assert location["page"] == PAGE_FIN_08
    assert location["narrative_page"] == PAGE_FUND_NARRATIVE
    assert location["pages"] == [PAGE_FIN_08, PAGE_FUND_NARRATIVE]
    assert location["narrative_section"] == "八、政府性基金预算财政拨款收入支出决算情况说明"
    refs = {ref["role"]: ref for ref in location["table_refs"]}
    assert set(refs) == {"表格", "情况说明"}
    assert refs["表格"]["page"] == PAGE_FIN_08
    assert refs["表格"]["code"] == "2219899"
    assert refs["情况说明"]["page"] == PAGE_FUND_NARRATIVE
    # 说明侧证据必须保留命中原文（能回到 PDF 原句）
    assert "住房保障支出（项）135.00 万元" in refs["情况说明"]["span"].replace("\n", "")

    evidence = issues[0].evidence_text or ""
    for token in (FIN_08_TITLE, "八、政府性基金预算财政拨款收入支出决算情况说明",
                  "2219899", "105.00", "135.00", "30.00", "万元", "2025"):
        assert token in evidence, f"证据缺少 {token}: {evidence}"


def test_y03_finding_does_not_claim_which_side_is_wrong(truth):
    """人工判定只确认两处不一致，finding 不得替审校人判定哪一侧有误。"""
    for issue in _run(_document(truth)):
        text = f"{issue.message}{issue.evidence_text or ''}"
        assert "不能同时成立" in text
        assert "无法判定哪一侧" in text or "无法判断哪一侧" in text


def test_y03_state_capital_scope_stays_silent_on_real_empty_table(truth):
    """FIN_09 真实路径（宜川）：国资表为空 + 说明"无…收入和支出"→ 不报不阻塞。

    一个 scope 无业务不得拖垮另一个 scope（任务 §十）。
    """
    issues = _run(_document(truth))
    assert {issue.location["fund_scope"] for issue in issues} == {"政府性基金"}


def test_truth_pipeline_reports_the_finding_in_full_final_run(truth):
    """真实 truth pipeline：完整 final 规则集执行必须产出本规则的 fail 结论。

    Mutation C 的守卫：把 V33-TXT-FUND-DETAIL 从注册表拿掉后，这条测试必须红
    （finding 消失 = 漏报回归）。
    """
    doc = _document(truth)
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="final")

    assert RULE_ID in registered_rule_ids("final")
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    assert any(issue.rule == RULE_ID for issue in issues), (
        "完整 final 执行未保留 Y03 finding（Contract C1：fail 的 finding 不得被丢弃）"
    )


def test_rule_is_final_only_not_in_budget_registry(truth):
    """任务 §十九：registered_rule_ids('final') 含、('budget') 不含；预算执行集不跑它。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")

    doc = _document(truth)
    _unused, outcomes = run_rules_with_outcomes(doc, False, report_kind="budget")
    assert RULE_ID not in {outcome.rule_id for outcome in outcomes}


# ---------------------------------------------------------------------------
# 反例 A：完全一致 → 0 finding
# ---------------------------------------------------------------------------


def test_consistent_narrative_reports_nothing(truth):
    """反例 A：说明项金额改成与表一致（135→105）→ 零 finding。"""

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(data, "135.00", "105.00")

    assert _run(_clone(truth, mutate)) == []


def test_rounding_envelope_diff_is_a_hint_not_a_hard_error(truth):
    """纯显示舍入差（0.01 ≤ 两侧半步长和）不得报高严重度正式问题（任务 §十三）。"""

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(
            data,
            "住房保障支出（项）135.00 万元",
            "住房保障支出（项）105.01 万元",
        )

    issues = _run(_clone(truth, mutate))
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "舍入" in issues[0].message
    assert issues[0].location["narrative_amount"] == "105.01"


# ---------------------------------------------------------------------------
# 反例 B：不同业务项 → 不得互相比较（Mutation A 守卫）
# ---------------------------------------------------------------------------


def test_foreign_classification_chain_never_pairs_with_the_table_item(truth):
    """反例 B：说明改成"城乡社区支出（类）…"——项名相同但类层不同 = 不同业务项。

    只按项级名称/金额匹配的实现（Mutation A）会把 135 配给 2219899 报假冲突；
    正确行为是记取数不足、不出正式 finding。
    """

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(
            data,
            "住房保障支出（类）超长期特别国债安排的支出（款）",
            "城乡社区支出（类）超长期特别国债安排的支出（款）",
        )

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    assert "未在表内定位到" in _rule_detail(excinfo.value)
    # 没有任何一侧被"就近配对"出正式比较
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 反例 C：不同年度 → 不比较
# ---------------------------------------------------------------------------


def test_foreign_year_in_statement_prefix_blocks_binding(truth):
    """反例 C：项句前缀出现 2024 年度（与材料年度 2025 不同）→ 不跨期绑定。"""

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(
            data,
            "支出具体情况如下：\n住房保障支出（类）",
            "支出具体情况如下：\n2024年度住房保障支出（类）",
        )

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    detail = _rule_detail(excinfo.value)
    assert "2024" in detail and "不跨期绑定" in detail
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 反例 D：单位不能确认 → 不正式报错
# ---------------------------------------------------------------------------


def test_unknown_table_unit_produces_no_formal_finding(truth):
    """反例 D：表页抹掉「单位：万元」→ 单位不可确认 → 记取数不足，不出 finding。"""

    def mutate(data: Dict[str, Any]) -> None:
        page = data["page_texts"][PAGE_FIN_08 - 1]
        assert "单位：万元" in page
        data["page_texts"][PAGE_FIN_08 - 1] = page.replace("单位：万元", "")

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    assert "金额单位" in _rule_detail(excinfo.value)
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 反例 E 加固：同一项给出多个不同金额 → 不猜
# ---------------------------------------------------------------------------


def test_conflicting_item_amounts_in_narrative_block_comparison(truth):
    """同一业务项在说明里出现两个不同金额：取哪个无从证明 → 该项不形成正式比较。"""

    def mutate(data: Dict[str, Any]) -> None:
        _truth_mutate_narrative(
            data,
            "住房保障支出（项）135.00 万元，主要用于住宅老旧电梯更新\n改造。",
            "住房保障支出（项）135.00 万元，主要用于住宅老旧电梯更新\n改造。"
            "住房保障支出（类）超长期特别国债安排的支出（款）其他"
            "住房保障支出（项）200.00 万元，再次列示。",
        )

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    assert "多个不同金额" in _rule_detail(excinfo.value)
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 表侧 fail-closed：空白单元格、表身份歧义
# ---------------------------------------------------------------------------


def test_blank_expenditure_cell_is_not_treated_as_zero(truth):
    """表内该项的「本年支出」单元格清空：空白 ≠ 0，不得拿 0 去比出"差 135"的假 finding。"""

    def mutate(data: Dict[str, Any]) -> None:
        rows = data["page_tables"][PAGE_FIN_08 - 1][0]
        row = next(r for r in rows if str(r[0]) == "2219899")
        assert row[4] == "105.00"
        row[4] = ""

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    assert "不可读" in _rule_detail(excinfo.value)
    assert "空白不按 0 处理" in _rule_detail(excinfo.value)
    assert excinfo.value.partial_issues == []


def test_duplicate_fund_tables_are_an_ambiguity_not_first_match(truth):
    """同一表名命中两张结构化表：表身份有歧义 → 记取数不足，不得随手取第一张。"""

    def mutate(data: Dict[str, Any]) -> None:
        data["page_tables"][PAGE_FIN_08 + 1] = copy.deepcopy(
            data["page_tables"][PAGE_FIN_08 - 1]
        )
        data["page_texts"][PAGE_FIN_08 + 1] = (
            "政府性基金预算财政拨款收入支出决算表\n单位：万元\n"
        )

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    assert "表身份有歧义" in _rule_detail(excinfo.value)
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# 反例 F：无相关业务 → 不造假 finding（真实负例）
# ---------------------------------------------------------------------------


def test_real_sample_with_no_fund_business_reports_nothing(no_business_sample):
    """生态环境局样张：基金/国资均无收支（空表 + 明确"无…收入和支出"）→ 零 finding。

    「无相关收支」在这里有完整证据链：表为空 + 文种决算 + 同主体同年度说明。
    不得因出现"无"字误判 not_applicable，也不得在空表上造假逐项冲突。
    """
    assert _run(_document(no_business_sample)) == []


# ---------------------------------------------------------------------------
# FIN_09 逐项路径：结构化 contract fixture（合成，业务身份严格）
# 诚实口径：这不是真实 truth；真实样张只覆盖 FIN_09 的"无相关业务"路径。
# ---------------------------------------------------------------------------

#: 合同夹具的功能分类层级（类/款/项三级，编码 7 位项级可逐级回溯）
_CONTRACT_CODE = "2299901"
_CONTRACT_KLASS = "其他支出"
_CONTRACT_KUAN = "国有资本经营预算支出"
_CONTRACT_XIANG = "其他国有资本经营预算支出"
_CONTRACT_LABEL = (
    f"{_CONTRACT_KLASS}（类）{_CONTRACT_KUAN}（款）{_CONTRACT_XIANG}（项）"
)


def _contract_rows(amounts: str = "50.00") -> List[List[str]]:
    """FIN_09 合同表：版式与真值 P25 同构（子表头与合计行金额同行）。"""
    blank = ""
    return [
        ["项目", blank, "年初结转和\n结余", "本年收入", "本年支出", blank, blank, "年末结转和\n结余"],
        ["功能分类\n科目编码", "科目名称", blank, blank, blank, blank, blank, blank],
        ["合计", blank, blank, amounts, "合计", "基本支出", "项目支出", blank],
        ["229", _CONTRACT_KLASS, blank, amounts, amounts, blank, amounts, blank],
        ["22999", _CONTRACT_KUAN, blank, amounts, amounts, blank, amounts, blank],
        [_CONTRACT_CODE, _CONTRACT_XIANG, blank, amounts, amounts, blank, amounts, blank],
    ]


def _contract_payload(narrative_amount: str = "50.00", *, empty_table: bool = False,
                      negative_statement: bool = False) -> Dict[str, Any]:
    """FIN_09 合同材料：有国资、无基金（独立适用性的关键合同点）。"""
    amounts = "" if empty_table else "50.00"
    if negative_statement:
        narrative = (
            "九、国有资本经营预算财政拨款收入支出决算情况说明\n"
            "某某局 2025 年度无国有资本经营预算财政拨款收入和支出。\n"
            "十、预算绩效管理情况\n本年度无预算绩效管理项目。\n"
        )
    else:
        narrative = (
            "九、国有资本经营预算财政拨款收入支出决算情况说明\n"
            "本年度国有资本经营预算财政拨款收入50.00万元，支出50.00万元。"
            "支出具体情况如下：\n"
            f"{_CONTRACT_LABEL}{narrative_amount} 万元，主要用于补充国有企业资本金。"
            "年初预算为30.00万元，支出决算为" + narrative_amount + "万元。\n"
            "十、预算绩效管理情况\n本年度无预算绩效管理项目。\n"
        )
    return {
        "doc_id": "CONTRACT-STATE-CAPITAL-2025",
        "page_texts": [
            "某某区某某局\n2025 年度部门决算\n",
            "国有资本经营预算财政拨款收入支出决算表\n单位：万元\n",
            "第三部分 2025年度部门决算情况说明\n" + narrative,
        ],
        "page_tables": [[], [_contract_rows(amounts)], []],
    }


def test_fin09_consistent_contract_reports_nothing():
    """FIN_09 一致路径：表 50.00 = 说明 50.00 → 零 finding（同段预算数 30 不误绑）。"""
    assert _run(_document(_contract_payload("50.00"))) == []


def test_fin09_mismatch_contract_reports_item_conflict():
    """FIN_09 逐项冲突路径：表 50.00 vs 说明 80.00 → 1 条 error，资金性质=国有资本经营。"""
    issues = _run(_document(_contract_payload("80.00")))

    assert len(issues) == 1, [issue.message for issue in issues]
    location = issues[0].location
    assert issues[0].severity == "error"
    assert location["fund_scope"] == "国有资本经营"
    assert location["table"] == FIN_09_TITLE
    assert location["code"] == _CONTRACT_CODE
    assert location["table_amount"] == "50.00"
    assert location["narrative_amount"] == "80.00"
    assert location["difference"] == "30.00"
    assert location["page"] == 2 and location["narrative_page"] == 3
    assert location["fiscal_year"] == 2025
    # FIN_08 缺席（无基金表、无基金说明）不得拖垮 FIN_09 的处理
    assert "政府性基金" not in issues[0].message


def test_fin09_empty_table_with_item_narrative_blocks_for_manual_review():
    """FIN_09 空表但说明列示了项级金额：两侧矛盾到无法逐项核对 → 记取数不足。"""
    with pytest.raises(RuleDeferred) as excinfo:
        _run(_document(_contract_payload("50.00", empty_table=True)))
    assert excinfo.value.status == "insufficient_data"
    assert "未取到任何金额" in _rule_detail(excinfo.value)


def test_fin09_table_with_data_but_negative_statement_is_a_contradiction():
    """表有非零金额、说明却称"无相关收支"：正式矛盾 finding（不是取数不足）。"""
    issues = _run(_document(_contract_payload(negative_statement=True)))

    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "矛盾" in issues[0].message
    assert "无国有资本经营预算财政拨款收入和支出" in issues[0].message
    location = issues[0].location
    assert location["fund_scope"] == "国有资本经营"
    assert location["obligation_id"] == OBLIGATION_ID
    assert location["table_amount"] == "150.00", "三个功能分类项的本年支出合计（50×3）"


# ---------------------------------------------------------------------------
# 台账联动：OBL-TXT-FUND-DETAIL 的缺口收口（与 WP4-A 同口径）
# ---------------------------------------------------------------------------


def _profile_receipt(rule_status: str) -> Dict[str, Any]:
    """台账回执：与生产 rule_execution_summary 同构（rule_statuses 包装）。"""
    return {"rule_statuses": {RULE_ID: rule_status}}


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：不再 not_implemented；回执 pass/fail → completed（任务 §二十二）。"""
    assert "V33-TXT-FUND-DETAIL" in registered_rule_ids("final")

    from src.engine.check_obligations import OBLIGATION_NOT_IMPLEMENTED

    def _instance(ledger: Dict[str, Any]):
        items = [
            item
            for item in ledger["instances"]
            if item["obligation_id"] == OBLIGATION_ID
        ]
        assert items, "台账缺少 OBL-TXT-FUND-DETAIL 实例"
        return items[0]

    # 预算文种不含该义务（final only）
    budget_ledger = build_obligation_ledger(None, report_kind="budget")
    assert not [i for i in budget_ledger["instances"] if i["obligation_id"] == OBLIGATION_ID]

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = _instance(ledger)
    assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == []
    assert set(instance["depends_on"]) == {"table:FIN_08", "table:FIN_09"}

    completed = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("fail")
    )
    receipt = _instance(completed)
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert "V33-TXT-FUND-DETAIL=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("insufficient_data")
    )
    deferred = _instance(insufficient)
    assert deferred["status"] != OBLIGATION_COMPLETED, (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation C 自动化：规则从注册表撤下 → 义务必须回到 not_implemented。

    否则"缺口数下降"可能来自清单改动，而不是真实能力增加。
    """
    from src.engine import rules_v33

    monkeypatch.setattr(
        rules_v33,
        "ALL_RULES",
        [
            rule
            for rule in rules_v33.ALL_RULES
            if getattr(rule, "code", "") != RULE_ID
        ],
    )
    assert RULE_ID not in registered_rule_ids("final")

    from src.engine.check_obligations import OBLIGATION_NOT_IMPLEMENTED

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = next(
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    )
    assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == [RULE_ID]
