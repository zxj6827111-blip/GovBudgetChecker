"""V33-TREND-COMPLETION-RATE：预算完成率分母口径与确定性复算。

真值来源（冻结，不得改来迁就实现）
--------------------------------
- **R004 政策合同正例（POLICY_CONTRACT，非真实样张）**：AGENTS.md R004 /
  rules/v3_3.yaml R004——「年初预算为0…支出决算为307.82万元，完成年初预算
  的95.89%」。仓库全部真实样张（宜川/石泉/文旅/普陀/长风/9-05 生态）的零
  预算条目都合规地写「决算数大于预算数的主要原因」，未出现「零分母+完成率」
  正例，如实按政策合同登记，不伪造成真实漏报。
- **真实负例（REAL，fixture SHA 双锁）**：
  - DOC-20260905-001（普陀区生态环境局 2025 决算）P21 总述句
    4628.17/4733.14/102.27%（复算 102.2696…%）；P26 三公总额 21.00/
    16.95/80.71%（复算 80.714…%）——含无分项预算的分项完成率声明
    （82.68%/0.00%），必须 fail-closed 不比而非错绑总额分母。
  - 石泉 P33 公益性岗位补贴 256/200.18/78.20%、P34 其他城市生活救助
    30/30/100%；死亡抚恤「年初预算为 0 元」跨页且无完成率声明 → 0 finding。
  - 宜川 P28 总述 20707.11/20218.21/98%（复算 97.64%，容差内）。
  - 文旅 P22 总述 27780.16/24535.67/88.32%。
- **交叉负例（石泉 S03）**：机关事业单位职业年金条目首句 257.14 vs
  「支出决算为 245.53」，完成率 93.57% 按首句自洽（257.14/274.82）——
  该矛盾由 V33-NARRATIVE-INDICATOR-REPEAT 报告，本规则必须 parse_ambiguity
  fail-closed，不得任选其一复算而误判完成率本身。

夹具口径（诚实声明）：R004 正例与合同用例 A~J/变体是 **POLICY_CONTRACT /
CONTRACT FIXTURE**（合成），真实负例与交叉负例是 **REAL**。
百分比容差复用仓库统一策略（V33-234/BUD-111 同款 abs 0.5pp / 相对 1%，
Decimal 化沿用 CMM-007 实现形态），金额侧 Decimal 全程，不自造容差。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_COMPLETED,
    OBLIGATION_NOT_IMPLEMENTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import STATUS_FAIL, RuleDeferred  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    R33TrendCompletionRate,
    build_document,
)

RULE_ID = "V33-TREND-COMPLETION-RATE"
OBLIGATION_ID = "OBL-TREND-COMPLETION-RATE"

#: DOC-20260905-001：上海市普陀区生态环境局 2025 年度部门决算（31 页）
SAMPLE_FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"
SAMPLE_SOURCE_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"
SAMPLE_FIXTURE_SHA = "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1"

#: 石泉路街道 2025 年度部门决算（44 页；与 WP4-C 共用冻结夹具，SHA 双锁）
SHIQUAN_FIXTURE = ROOT / "tests" / "fixtures" / "shiquan_narrative_truth_page_data.json"
SHIQUAN_SOURCE_SHA = "e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4"
SHIQUAN_FIXTURE_SHA = "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7"

#: 宜川路街道 2025 年度决算（41 页；与 WP4-A/B/C/D 共用冻结夹具，SHA 双锁）
YICHUAN_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
YICHUAN_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
YICHUAN_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 文化和旅游局 2025 年度部门决算（33 页；与 WP4-D 共用冻结夹具，SHA 双锁）
WENLV_FIXTURE = ROOT / "tests" / "fixtures" / "wenlv_narrative_truth_page_data.json"
WENLV_SOURCE_SHA = "74e6afd06669098f57391a13dad63a6f2a15cac620ac9f32018e79649fe6a367"
WENLV_FIXTURE_SHA = "07813ae61b04cf9f3afd2bf93c69b357ff9aeefee3ba6a893b34d77d956e8252"


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
def sample() -> Dict[str, Any]:
    return _load_fixture(SAMPLE_FIXTURE, SAMPLE_SOURCE_SHA, SAMPLE_FIXTURE_SHA)


@pytest.fixture(scope="module")
def shiquan() -> Dict[str, Any]:
    return _load_fixture(SHIQUAN_FIXTURE, SHIQUAN_SOURCE_SHA, SHIQUAN_FIXTURE_SHA)


@pytest.fixture(scope="module")
def yichuan() -> Dict[str, Any]:
    return _load_fixture(YICHUAN_FIXTURE, YICHUAN_SOURCE_SHA, YICHUAN_FIXTURE_SHA)


@pytest.fixture(scope="module")
def wenlv() -> Dict[str, Any]:
    return _load_fixture(WENLV_FIXTURE, WENLV_SOURCE_SHA, WENLV_FIXTURE_SHA)


def _document(payload: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{payload.get('doc_id', 'sample')}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload.get("page_tables", []))),
        filesize=0,
    )


def _run(doc: Any) -> List[Any]:
    return list(R33TrendCompletionRate().apply(doc))


def _contract_doc(*sections: str) -> Any:
    """合成 contract 文档：页 1 提供年度锚，其后每参量一页。"""
    return build_document(
        path="contract-completion-rate.pdf",
        page_texts=["某某局 2025 年度部门决算公开", *sections],
        page_tables=[],
        filesize=0,
    )


def _final_page(*clauses: str) -> str:
    """把合同用例放进同一个决算说明章节（同节才允许绑定披露）。"""
    return "三、支出决算情况说明\n" + "\n".join(clauses)


#: 类款项条目模板：项名后带首句金额（真实样张形态，首句金额参与分子交叉验证）
_ENTRY = (
    "1、一般公共服务支出（类）统计信息事务（款）专项普查活动（项）"
    "{amount} 万元，主要用于：开展经济普查。"
)


# ---------------------------------------------------------------------------
# R004 政策合同正例（POLICY_CONTRACT——非真实样张，禁止冒充真实漏报）
# ---------------------------------------------------------------------------


def test_r004_policy_contract_zero_denominator_is_reported():
    """R004 正例：「年初预算为0」（无万元后缀）+ 完成率 95.89% → 恰好 1 条
    error finding，recomputed=undefined（不伪造 0%/∞%），Mutation A 守卫：
    删掉零分母特判后此测试必红。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为0，支出决算为307.82万元，完成年初预算的95.89%。"
        )
    )
    issues = _run(doc)

    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "error", "R004 政策级 critical → engine 最高级 error，不得降级"

    location = issue.location
    assert location["indicator"] == "行政运行"
    assert location["denominator_type"] == "年初预算"
    assert location["denominator_amount"] == "0"
    assert location["actual_amount"] == "307.82"
    assert location["declared_completion_rate"] == "95.89"
    assert location["recomputed_completion_rate"] == "undefined"
    assert location["difference_pp"] == "undefined"
    assert location["zero_denominator"] is True
    assert location["identity_conflict"] is False
    assert location["obligation_id"] == OBLIGATION_ID
    assert location["unit"] == "万元"
    assert location["fiscal_year"] == 2025
    refs = {ref["role"]: ref for ref in location["table_refs"]}
    assert "完成年初预算的95.89%" in refs["完成率声明"]["span"]
    assert "年初预算为0" in refs["分母披露"]["span"]
    assert "支出决算为307.82万元" in refs["分子披露"]["span"]
    assert "没有数学定义" in issue.message
    assert "有效分母" in issue.message


def test_r004_zero_wan_unit_and_zero_yuan_unit_both_reported():
    """零分母的等价披露形态：「年初预算为0.00 万元」与「年初预算为 0 元」
    （石泉真实形态）都必须命中——V33-234 的正则要求万元后缀，这两个形态
    恰好是它漏检的 R004 主形态。"""
    for text in (
        "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
        "年初预算为0.00 万元，支出决算为90万元，完成年初预算的100%。",
        "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
        "年初预算为 0 元，支出决算为 177.17 万元，完成年初预算的50%。",
    ):
        issues = _run(_contract_doc(_final_page(text)))
        assert len(issues) == 1, text
        assert issues[0].severity == "error"
        assert issues[0].location["zero_denominator"] is True


def test_near_zero_disclosure_is_not_zero_denominator():
    """任务 §十九：「年初预算为0.004万元」是显示出来的非零披露，绝不能按
    abs<0.01 当成 0 触发 R004；非零分母走正常复算（0.004/0.004=100% 一致）。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为0.004万元，支出决算为0.004万元，完成年初预算的100%。"
        )
    )
    assert _run(doc) == []


# ---------------------------------------------------------------------------
# 合同负例 B~J（CONTRACT FIXTURE，非真实样张；任务 §二十六）
# ---------------------------------------------------------------------------


def test_case_b_normal_100_percent_passes():
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="100"),
            "年初预算数为100万元，支出决算数为100万元，完成年初预算的100%。",
        )
    )
    assert _run(doc) == []


def test_case_c_normal_90_percent_passes():
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "年初预算数为100万元，支出决算数为90万元，完成年初预算的90%。",
        )
    )
    assert _run(doc) == []


def test_case_d_wrong_rate_is_a_finding():
    """100/90/110% → 复算 90% ≠ 110% → error（仓库已有 V33-234 合同测试的
    同款场景，本规则有界重叠）。Mutation C 守卫：取消真正复算、只查关键词后
    此测试必须红。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "年初预算数为100万元，支出决算数为90万元，完成年初预算的110%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.location["denominator_type"] == "年初预算"
    assert issue.location["recomputed_completion_rate"] == "90.00"
    assert issue.location["declared_completion_rate"] == "110"
    assert issue.location["difference_pp"] == "20.00"
    assert issue.location["zero_denominator"] is False


def test_case_e_over_100_but_mathematically_correct_passes():
    """100/120/120% → 完成率数学本身 PASS。超 100% 不自动报错；「超支需
    说明原因」是 V33-234 的 warn 职责，本规则不得重复报。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="120"),
            "年初预算数为100万元，支出决算数为120万元，完成年初预算的120%。",
        )
    )
    assert _run(doc) == []


def test_case_f_full_year_denominator_is_legal():
    """任务 §二十五：年初预算 0、全年预算 320、决算 307.82、写「完成全年
    预算的96.19%」→ 合法，0 finding。Mutation B 守卫：把「全年预算」也当
    「年初预算」后此测试必须红。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="307.82"),
            "年初预算为0万元，全年预算为320万元，支出决算为307.82万元，"
            "完成全年预算的96.19%。",
        )
    )
    assert _run(doc) == []


def test_case_g_wrong_denominator_identity_is_a_finding():
    """任务 §二十五：同样数据写「完成年初预算的96.19%」→ 分母身份错误——
    声明身份的年初预算为 0，数值实际与全年预算复算吻合。Mutation B 守卫：
    忽略 denominator identity 后 Case F/G 至少一条红。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="307.82"),
            "年初预算为0万元，全年预算为320万元，支出决算为307.82万元，"
            "完成年初预算的96.19%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.location["identity_conflict"] is True
    assert issue.location["zero_denominator"] is True
    assert issue.location["denominator_type"] == "年初预算"
    alt = issue.location["alternative_denominator"]
    assert alt["type"] == "全年预算"
    assert alt["amount_wan"] == "320.00"
    assert "分母身份" in issue.message


def test_case_h_yoy_percent_is_not_completion_rate():
    """任务 §二十三：「比上年增长100%」是同比（CMM-007 领域）→ 本规则
    0 finding。Mutation D 守卫：把「增长X%」纳入完成率 matcher 后此测试
    必须红（matcher 结构断言先红，行为断言兜底）。"""
    text = "本年支出90万元，比上年增长100%。公务接待费支出0.5万元，占总支出的95%。"
    from src.engine.rules_v33 import _WP4E_CLAIM_NOUN_RE, _WP4E_CLAIM_RE

    assert not list(_WP4E_CLAIM_RE.finditer(text)), "同比/占比句进入了完成率 matcher"
    assert not list(_WP4E_CLAIM_NOUN_RE.finditer(text)), "同比/占比句进入了完成率 matcher"
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "本年支出90万元，比上年增长100%。",
        )
    )
    assert _run(doc) == []


def test_case_i_share_percent_is_not_completion_rate():
    """任务 §二十四：「占总支出的95%」是占比，不是完成率 → 0 finding。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="0.5"),
            "公务接待费支出0.5万元，占总支出的95%。",
        )
    )
    assert _run(doc) == []


def test_case_j_multiple_budget_candidates_is_ambiguity():
    """任务 §二十六：同条目两个不一致年初预算（100/120）+ 决算 90 +
    完成率 75% → parse_ambiguity，禁止取首个/最近。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "年初预算为100万元。年初预算为120万元。"
            "支出决算为90万元，完成年初预算的75%。",
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "parse_ambiguity" in detail and "年初预算" in detail
    assert excinfo.value.partial_issues == []


def test_default_denominator_identity_resolved_from_same_clause():
    """缺省身份「完成预算的90%」由同句显式披露（年初预算 100）补全 → PASS；
    不能因缺省身份就跳过，也不能错绑泛身份之外的身份。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "年初预算为100万元，支出决算为90万元，完成预算的90%。",
        )
    )
    assert _run(doc) == []


def test_noun_form_budget_completion_rate_recompute():
    """名词形态「年初预算完成率为X%」同样复算：写错必报。"""
    doc = _contract_doc(
        _final_page(
            _ENTRY.format(amount="90"),
            "年初预算为100万元，支出决算为90万元，年初预算完成率为110%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].location["declared_completion_rate"] == "110"


def test_multiple_items_are_isolated():
    """任务 §二十一：两条目（行政运行 100/90/90%、职业年金 200/150/75%）
    分别绑定；第二条完成率写错（76%）时只报第二条目，指标名必须对位，
    不得串线成「行政运行预算 + 职业年金决算」。"""
    doc = _contract_doc(
        _final_page(
            "1、一般公共服务支出（类）统计信息事务（款）专项普查活动（项）"
            "90 万元，主要用于：开展经济普查。"
            "年初预算为100万元，支出决算为90万元，完成年初预算的90%。",
            "2、社会保障和就业支出（类）行政事业单位养老支出（款）职业年金"
            "（项）150 万元，主要用于：职业年金缴费。"
            "年初预算为200万元，支出决算为150万元，完成年初预算的76%。",
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    assert issues[0].location["indicator"] == "职业年金"
    assert issues[0].location["recomputed_completion_rate"] == "75.00"


def test_cross_page_entry_binds_via_merged_text():
    """任务 §二十二：分母/分子在页 N、完成率声明在页 N+1（同条目跨页、
    无句读中断）→ 仍按合并文本绑定，页码证据取自披露所在页。"""
    doc = build_document(
        path="cross-page.pdf",
        page_texts=[
            "某某局 2025 年度部门决算公开",
            "三、支出决算情况说明\n"
            "1、一般公共服务支出（类）统计信息事务（款）专项普查活动（项）"
            "200.18 万元，主要用于：开展经济普查。"
            "年初预算为 256 万元，支出决算为 200.18 万元，",
            "完成年初预算的 78.20%。决算数小于预算数的主要原因：年中调减。",
        ],
        page_tables=[],
        filesize=0,
    )
    issues = _run(doc)
    assert issues == []  # 200.18/256=78.195…%，容差内 PASS


def test_san_gong_item_rate_without_item_budget_is_not_bound_to_total():
    """三公真实形态：分项完成率（车辆 82.68%）的分项预算未在叙事披露，
    不得把同条目内的三公总额预算 21 错绑给分项声明 → fail-closed 不比。"""
    doc = _contract_doc(
        _final_page(
            "七、财政拨款“三公”经费支出决算情况说明",
            "（一）“三公”经费财政拨款支出决算总体情况说明。",
            "“三公”经费财政拨款支出年初预算为 21.00 万元，支出决算\n"
            "为 16.95 万元，完成预算的 80.71%，其中：因公出国（境）费决\n"
            "算为 0.00 万元，完成预算的 0.00%；公务用车购置及运行维护费\n"
            "支出决算为 16.95 万元，完成预算的 82.68%。",
        )
    )
    assert _run(doc) == []


# ---------------------------------------------------------------------------
# 真实负例（REAL）
# ---------------------------------------------------------------------------


def test_real_sample_doc_positive_passes(sample):
    """DOC-20260905-001（REAL）：P21 总述句 102.27%、P26 三公 80.71% 均
    复算一致；无分项预算的分项声明 fail-closed 不比 → 全文档 0 finding。"""
    issues = _run(_document(sample))
    assert issues == [], [getattr(i, "message", "") for i in issues]


def test_real_yichuan_summary_rate_passes(yichuan):
    """宜川（REAL）：P28 总述 20707.11/20218.21/98%，复算 97.64%，在
    abs 0.5pp / 相对 1% 容差内 → 0 finding。"""
    assert _run(_document(yichuan)) == []


def test_real_wenlv_summary_rate_passes(wenlv):
    """文旅（REAL）：P22 总述 27780.16/24535.67/88.32% 复算一致 → 0 finding。"""
    assert _run(_document(wenlv)) == []


def test_real_shiquan_rates_pass_and_zero_budget_entry_not_flagged(shiquan):
    """石泉（REAL 交叉负例）：P33/P34 全部完成率复算一致（78.20%/100%…）；
    死亡抚恤「年初预算为 0 元」无完成率声明 → 不触发 R004。同条目首句
    257.14 与「支出决算为 245.53」矛盾（WP4-C 的 S03，由
    V33-NARRATIVE-INDICATOR-REPEAT 报告）→ 完成率分子 parse_ambiguity
    fail-closed，整份材料 deferred 且 partial_issues 为空——绝不把按首句
    自洽的 93.57% 误判成复算不一致。"""
    with pytest.raises(RuleDeferred) as excinfo:
        _run(_document(shiquan))
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "parse_ambiguity" in detail and "首句金额" in detail
    assert excinfo.value.partial_issues == [], "交叉负例上不得产生任何完成率 finding"


def test_real_shiquan_isolated_zero_budget_entry_alone_passes(shiquan):
    """真实负例的单条目形态：只含死亡抚恤跨页零预算条目（无完成率声明）
    的材料 → 0 finding、0 deferred。"""
    texts = [str(t) for t in shiquan["page_texts"]]
    page33 = texts[32]
    page34 = texts[33]
    start = page33.find("“社会保障和就业支出（类）抚恤（款）死亡抚恤（项）”")
    assert start >= 0, "夹具缺死亡抚恤条目（P33）"
    doc = build_document(
        path="shiquan-zero-budget.pdf",
        page_texts=[
            "上海市普陀区人民政府石泉路街道办事处 2025 年度部门决算",
            "五、一般公共预算财政拨款支出决算情况说明\n" + page33[start:],
            page34,
        ],
        page_tables=[],
        filesize=0,
    )
    assert _run(doc) == []


# ---------------------------------------------------------------------------
# truth pipeline：完整 final 规则集执行必须产出本规则的 fail 结论
# ---------------------------------------------------------------------------


def test_truth_pipeline_reports_r004_in_full_final_run():
    """Mutation E 守卫（pipeline 面）：把 V33-TREND-COMPLETION-RATE 从
    final 注册表拿掉后，final 规则集不会执行它——outcome 集里必须出现
    本规则且 STATUS_FAIL。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为0，支出决算为307.82万元，完成年初预算的95.89%。"
        )
    )
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="final")

    assert RULE_ID in registered_rule_ids("final")
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    assert any(
        issue.rule == RULE_ID and "没有数学定义" in issue.message for issue in issues
    ), "完整 final 执行未保留 R004 finding（fail 的 finding 不得被丢弃）"


# ---------------------------------------------------------------------------
# final-only 注册与台账收口（Mutation E 的固化守卫）
# ---------------------------------------------------------------------------


def test_rule_registered_in_final_only():
    """任务 §三十二：final 必含；budget 不得含（本规则是 final only——
    rules_v33.ALL_RULES 只进 final 注册表）。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")


def _obligation_instance(ledger: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, "台账缺少 OBL-TREND-COMPLETION-RATE 实例"
    return items[0]


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：final 台账不再 not_implemented；回执驱动完成状态。"""
    assert RULE_ID in registered_rule_ids("final")

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = _obligation_instance(ledger)
    assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: "fail"}},
    )
    receipt = _obligation_instance(completed)
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: "insufficient_data"}},
    )
    deferred = _obligation_instance(insufficient)
    assert deferred["status"] != OBLIGATION_COMPLETED, (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_budget_ledger_has_no_completion_rate_instance():
    """final only：budget 台账不展开该义务实例。"""
    ledger = build_obligation_ledger(None, report_kind="budget")
    items = [
        item
        for item in ledger["instances"]
        if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items == []


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation E 自动化：从 ALL_RULES 撤下 → final 台账回到 not_implemented、
    registered_rule_ids 不含——否则"缺口数下降"可能来自清单改动而非真实能力。"""
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
    assert RULE_ID not in registered_rule_ids("budget")

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = _obligation_instance(ledger)
    assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == [RULE_ID]


# ---------------------------------------------------------------------------
# R2 整改 P1-A：财政年度无法确认时 fail-closed（任务 §二~六）
# ---------------------------------------------------------------------------


def _no_year_doc(*sections: str) -> Any:
    """无任何可确认财政年度的合成材料：封面与正文都不含年份。"""
    return build_document(
        path="no-year-completion-rate.pdf",
        page_texts=["某某局部门决算公开", *sections],
        page_tables=[],
        filesize=0,
    )


def test_no_year_material_defers_before_any_finding():
    """任务 §四：完成率复算会得出 error 的材料（100/90/110%），但整份
    Document 无任何可确认财政年度 → 必须 RuleDeferred insufficient_data、
    partial_issues 为空——期间敏感检查不得在期间身份不明时正式比较。"""
    doc = _no_year_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为100万元，支出决算为90万元，完成年初预算的110%。"
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    assert "财政年度" in excinfo.value.detail
    assert excinfo.value.partial_issues == []


def test_no_year_zero_denominator_still_defers_not_reports():
    """任务 §五：R004 零分母文本（数学上明显错误）在年度未知时同样必须
    deferred、partial_issues 为空——不能因为数学明显错误就绕过期间身份。"""
    doc = _no_year_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为0，支出决算为307.82万元，完成年初预算的95.89%。"
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    assert excinfo.value.partial_issues == []


# ---------------------------------------------------------------------------
# R2 整改 P1-B：非零金额缺单位不得默认「万元」（任务 §七~二十一）
# ---------------------------------------------------------------------------


def test_unit_case_a_nonzero_denominator_missing_unit_defers():
    """任务 §十六：分母「年初预算为100」未显式单位、分子有万元 → 不得默认
    万元后正式复算，必须 insufficient_data、0 partial_issues。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为100，支出决算为90万元，完成年初预算的110%。"
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "未显式金额单位" in detail
    assert excinfo.value.partial_issues == []


def test_unit_case_b_nonzero_actual_missing_unit_defers():
    """任务 §十七：分子「支出决算为90」未显式单位、分母有万元 → 同样
    insufficient_data、0 partial_issues。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为100万元，支出决算为90，完成年初预算的110%。"
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "未显式金额单位" in detail
    assert excinfo.value.partial_issues == []


def test_unit_case_c_both_missing_unit_defers():
    """任务 §十八：分母与分子都未显式单位 → deferred；不得因为数字比例
    恰好能算（90/100）而形成正式判断。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为100，支出决算为90，完成年初预算的110%。"
        )
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    assert excinfo.value.partial_issues == []


def test_zero_denominator_without_unit_still_hits_r004():
    """任务 §十九：零值豁免——「年初预算为0」无单位仍必须命中 R004
    （0 元 = 0 万元 = 0 亿元，零值不受单位换算影响），且 evidence 不伪造
    原文写了万元：denominator_unit 为 None、归一值单独记录。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为0，支出决算为307.82万元，完成年初预算的95.89%。"
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.location["zero_denominator"] is True
    assert issue.location["denominator_unit"] is None, "原文未写单位，不得伪造"
    assert issue.location["denominator_unit_confirmed"] is False
    assert issue.location["normalized_denominator_wan"] == "0.00"
    assert issue.location["actual_unit"] == "万元"
    assert issue.location["actual_unit_confirmed"] is True
    assert issue.location["normalized_actual_wan"] == "307.82"
    assert issue.location["unit"] == "万元", "顶层 unit 是归一计算口径"


def test_explicit_yuan_unit_normalizes_to_wan():
    """任务 §二十：非零显式元单位换算——1000000元=100 万元、90 万元决算、
    90% → 0 finding。证明单位纪律不是「必须万元」，而是「必须可归一」。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为1000000元，支出决算为90万元，完成年初预算的90%。"
        )
    )
    assert _run(doc) == []


def test_explicit_yi_unit_normalizes_to_wan():
    """任务 §二十一：亿元换算——1亿元=10000 万元、9000 万元决算、90% → 0 finding。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为1亿元，支出决算为9000万元，完成年初预算的90%。"
        )
    )
    assert _run(doc) == []


def test_zero_yuan_unit_r004_kept():
    """任务 §二十上：显式元单位的零分母（真实石泉形态）继续命中 R004。"""
    doc = _contract_doc(
        _final_page(
            "1、行政运行（类）综合（款）行政运行（项），主要用于日常运转。"
            "年初预算为 0 元，支出决算为177.17万元，完成年初预算的50%。"
        )
    )
    issues = _run(doc)
    assert len(issues) == 1
    assert issues[0].location["zero_denominator"] is True
    assert issues[0].location["denominator_unit"] == "元"
    assert issues[0].location["normalized_denominator_wan"] == "0.00"
