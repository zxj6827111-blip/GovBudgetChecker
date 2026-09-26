"""V33-PERF-PHASE-AMOUNT：项目绩效阶段金额披露一致性（WP4-H）。

真值来源（冻结，不得改来迁就实现）
--------------------------------
**Truth PA-1（REAL，文旅局）**：上海市普陀区文化和旅游局 2026 年部门预算
（27 页，SHA ``19447c3cec309322ad786b14f5cb751424a1bfe2a1f3c4c3a96d035440665cf9``）
P27「真如海心剧院精装修工程项目经费情况说明」：

    六、年度预算安排
    本项目可研批复总投资为13526.06万元，其中建筑安装工程费为11668.67万元，
    工程建设其他费用1213.29万元，预备费644.10万元。2025年安排建设资金5000万元。

章节标题承诺**年度**口径，内容只有**累计**（可研批复总投资，且
11668.67+1213.29+644.10=13526.06 内部自洽）与**往年**（2025 年安排 5000
万元）口径金额——2026 年度金额整体缺失、无口径说明。全语料 46 份唯一
预算文档扫描仅此 1 条命中、0 误报（2026-09-26 现场复核）。

**负例锚（REAL）**：建管委 2026 部门预算（SHA ``73528f0b…``）「兰溪路-
真南路下立交工程」（阶段性项目，2026-01-01 至 2027-12-31）：说明年度
22,614.51 万元 ↔ 申报表年度资金申请总额 226,145,100.00 元（归一后一致，
项目资金总额 771,325,800.00 元为跨年累计口径——禁止比较项）；城管执法局
2026 部门预算（SHA ``c56f4368…``）「拆违经费」（阶段性项目，2024-01-01
至 2026-12-31）：349.80 万元 ↔ 3,498,000.00 元。人工判定方式：原页渲染
逐字核验（tmp/wp4h_evidence 渲染件），无官方预算检查表行覆盖本义务。

夹具口径（诚实声明）：真值 PA-1 与两个负例锚是 **REAL**（全页冻结夹具，
SHA 双锁）；正/负例 Case A-F 与变异用例均为 **CONTRACT FIXTURE**（合成，
非真实样张）。
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

from src.engine import budget_rules as budget_rules_module  # noqa: E402
from src.engine.budget_rules import R33PerfPhaseAmount  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402
from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_NOT_IMPLEMENTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import (  # noqa: E402
    STATUS_FAIL,
    RuleDeferred,
    RuleNotApplicable,
)

RULE_ID = "V33-PERF-PHASE-AMOUNT"
OBLIGATION_ID = "OBL-PERF-PHASE-AMOUNT"

FIXTURE = ROOT / "tests" / "fixtures" / "perf_phase_amount_truth_page_data.json"
FIXTURE_SHA = "feaa4bf7d3bcc832bf4184db543d800748cb1e9355d48608fdd1418bc77f1c02"

#: 三份真实源 PDF 的 SHA256（ Truth Discovery 冻结，与 uploads 内文件一致）
WENLV_SHA = "19447c3cec309322ad786b14f5cb751424a1bfe2a1f3c4c3a96d035440665cf9"
JGW_SHA = "73528f0b78d178bd83d9b65bcaa77d30269f5e0f67f6ceb6064b819ae479e16a"
CGJF_SHA = "c56f4368be5dbf51c88214f1260ecb97ce57fe77950df18dc33d184f40b24d93"

#: 真值小节逐字冻结（夹具 P27 摘录，软换行保留）——独立于夹具内容变动的双保险
WENLV_TRUTH_CHUNK = (
    "真如海心剧院精装修工程项目经费情况说明\n"
    " 二、立项依据\n"
    "     依据2024年10月上海市普陀区财政局文件《关于真如海心剧院精装修工程"
    "项目估算评估的复函》普财建函[2024]26号正式立项。\n"
    " 六、年度预算安排\n"
    "    本项目可研批复总投资为13526.06万元，其中建筑安装工程费为11668.67万"
    "元，工程建设其他费用1213.29万元，预备费644.10万元\n"
    "。2025年安排建设资金5000万元。\n"
    " 七、绩效目标\n"
    "     详见单位的项目绩效目标表"
)


# ---------------------------------------------------------------------------
# 夹具装载（fail-closed：缺失或不符即失败，不静默跳过）
# ---------------------------------------------------------------------------


def _load_fixture() -> Dict[str, Dict[str, Any]]:
    assert FIXTURE.exists(), f"真值冻结夹具缺失: {FIXTURE}"
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == FIXTURE_SHA, (
        "夹具内容哈希不符（perf_phase_amount_truth_page_data.json）——"
        "夹具被修改或需重新生成"
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    materials = {
        mat["label"]: mat for mat in payload["materials"]
    }
    assert materials["wenlv"]["source_pdf_sha256"] == WENLV_SHA
    assert materials["jgw"]["source_pdf_sha256"] == JGW_SHA
    assert materials["cgjf"]["source_pdf_sha256"] == CGJF_SHA
    return materials


@pytest.fixture(scope="module")
def materials() -> Dict[str, Dict[str, Any]]:
    return _load_fixture()


def _document(mat: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{mat['doc_id']}.pdf",
        page_texts=list(mat["page_texts"]),
        page_tables=[],
        filesize=0,
    )


def _budget_doc(*pages: str) -> Any:
    """合成 contract 预算文档：页 1 提供文种与年度锚，其后每参量一页。"""
    return build_document(
        path="contract-perf-phase.pdf",
        page_texts=[
            "某某局2026年度部门预算公开\n2026年度部门预算编制说明\n2026年预算安排情况如下",
            *pages,
        ],
        page_tables=[],
        filesize=0,
    )


def _hits(issues: List[Any]) -> List[Any]:
    return [item for item in issues if item.rule == RULE_ID]


def _apply(doc: Any) -> List[Any]:
    return list(R33PerfPhaseAmount().apply(doc))


NARRATIVE_TMPL = (
    "{project}项目经费情况说明\n"
    " 一、项目概述\n  项目内容概述。\n"
    " 六、年度预算安排\n{annual}\n"
    " 七、绩效目标\n     详见单位的项目绩效目标表"
)

FORM_TMPL = (
    "财政项目支出绩效目标申报表\n"
    "（2026年度）\n"
    "项目名称\n{project}\n"
    "项目性质\n{nature}\n"
    "项目类别\n特定目标类\n"
    "主管部门\n某某局\n"
    "实施单位\n某某单位\n"
    "计划开始日期\n2026/1/1\n"
    "计划完成日期\n2026/12/31\n"
    "项目资金\n{unit_decl}\n"
    "项目资金总额\n{total}\n"
    "其中：财政资金\n{total}\n"
    "年度资金申请总额\n{annual}\n"
    "其中：当年财政拨款\n{annual}\n"
    "上年结转资金\n0.00\n"
    "其他资金\n0.00 其他资金\n0.00\n"
    "项目绩效目标\n项目总目标\n年度总体目标\n 完成目标任务。"
)


def _form(
    project: str,
    annual: str,
    *,
    nature: str = "一次性项目",
    unit_decl: str = "（元）",
    total: str = "",
) -> str:
    return FORM_TMPL.format(
        project=project,
        nature=nature,
        unit_decl=unit_decl,
        total=total or annual,
        annual=annual,
    )


# ---------------------------------------------------------------------------
# 真实 truth 正例（REAL）：文旅局真如海心——PA-1 阶段口径错配
# ---------------------------------------------------------------------------


def test_truth_pa1_wenlv_reports_exactly_one_phase_mismatch(materials):
    """Truth PA-1：真如海心「年度预算安排」只有累计+往年金额 → 恰好 1 条。

    同时锁死"不误报"：全文档 27 页仅此 1 条 finding。
    """
    issues = _hits(_apply(_document(materials["wenlv"])))

    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "medium", "人工判定：披露口径错配（medium），非金额勾稽差错"

    location = issue.location
    assert location["page"] == 27
    assert location["project"] == "真如海心剧院精装修工程"
    assert location["section"] == "真如海心剧院精装修工程项目经费情况说明"
    assert location["predicate"] == "annual_amount_missing"
    assert location["fiscal_year"] == 2026
    assert location["obligation_id"] == OBLIGATION_ID
    # 期望/实际必须可复核：本年度金额缺失 + 逐笔口径标签
    assert "未披露本年度（2026）金额" in issue.message
    assert "13526.06万元（累计/阶段总）" in issue.message
    assert "11668.67万元（累计/阶段总）" in issue.message
    assert "5000万元（往年）" in issue.message
    assert "13526.06" in issue.evidence_text


def test_truth_pa1_frozen_chunk_reproduces_the_hit():
    """真值小节逐字冻结文本必须复现命中（与夹具解耦的双保险）。"""
    issues = _hits(_apply(_budget_doc(WENLV_TRUTH_CHUNK)))
    assert len(issues) == 1
    assert issues[0].location["project"] == "真如海心剧院精装修工程"
    assert issues[0].location["fiscal_year"] == 2026


def test_truth_pa1_year_missing_is_fail_closed_not_finding():
    """材料财政年度无法确认时不得产出 PA-1（年度/往年口径不可区分）。"""
    doc = build_document(
        path="no-year.pdf",
        page_texts=[
            "某某局部门预算公开",
            NARRATIVE_TMPL.format(project="某项目", annual="本年度预算安排100万元。"),
        ],
        page_tables=[],
        filesize=0,
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _apply(doc)
    assert "财政年度" in str(excinfo.value.detail)


# ---------------------------------------------------------------------------
# 真实负例锚（REAL）：建管委 / 城管执法局——同项目年度金额归一后一致
# ---------------------------------------------------------------------------


def test_truth_anchor_jgw_staged_project_annual_consistent_passes(materials):
    """负例锚：建管委兰溪路工程（阶段性项目）年度↔年度归一一致 → 0 finding。

    项目资金总额 771,325,800.00 元（跨 2026-2027 累计）在场但**不得**参与
    比较——Case D 的真实形态。
    """
    issues = _apply(_document(materials["jgw"]))
    assert issues == [], [getattr(i, "message", "") for i in issues]


def test_truth_anchor_cgjf_unit_normalized_annual_consistent_passes(materials):
    """负例锚：城管执法局拆违经费 349.80 万元 ↔ 3,498,000.00 元 → 0 finding。

    Case E 的真实形态（万元/元可归一）。
    """
    issues = _apply(_document(materials["cgjf"]))
    assert issues == [], [getattr(i, "message", "") for i in issues]


# ---------------------------------------------------------------------------
# Contract 正/负例 Case A-F
# ---------------------------------------------------------------------------


def test_case_a_same_project_same_phase_equal_passes():
    """Case A：同项目同阶段金额一致（100 万元 ↔ 1,000,000.00 元）→ 0 finding。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某某改造工程", annual="2026年度预算安排100万元。"),
        _form("某某改造工程", "1,000,000.00"),
    )
    assert _apply(doc) == []


def test_case_b_same_project_same_phase_mismatch_is_finding():
    """Case B：同项目同阶段 100 万元 vs 120 万元 → 1 条 high finding。

    finding 必须带 project/phase/amount_A/amount_B/unit/page/section 可复
    核字段（任务 §八），不得只写"绩效金额不一致"。
    """
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某某改造工程", annual="2026年度预算安排100万元。"),
        _form("某某改造工程", "1,200,000.00"),
    )
    issues = _apply(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "high"
    location = issue.location
    assert location["project"] == "某某改造工程"
    assert location["phase"] == "年度（当年）"
    assert location["amount_a"] == "100" and location["unit_a"] == "万元"
    assert location["amount_b"] == "1,200,000.00" and location["unit_b"] == "元"
    assert location["amount_a_wan"] == "100.00"
    assert location["amount_b_wan"] == "120.00"
    assert location["diff_wan"] == "20.00"
    assert location["comparison"] == "annual_vs_annual"
    assert location["obligation_id"] == OBLIGATION_ID
    assert "100" in issue.message and "1,200,000.00" in issue.message
    assert "20.00" in issue.message


def test_case_c_two_phase_forms_are_not_compared():
    """Case C：一期/二期两张申报表 → 阶段归属不可确认，不比较不判等。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某延续项目", annual="2026年度预算安排100万元。"),
        _form("某延续项目", "1,000,000.00", nature="阶段性项目", total=""),
        _form("某延续项目", "1,000,000.00", nature="阶段性项目", total=""),
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _apply(doc)
    assert "阶段归属" in str(excinfo.value.detail)
    assert excinfo.value.partial_issues == []


def test_case_d_cumulative_total_is_never_compared_with_annual():
    """Case D：年度累计 1000 万（项目资金总额）≠ 阶段 100 万（年度）→ 不比较。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某跨年项目", annual="2026年度预算安排100万元。"),
        _form("某跨年项目", "", nature="阶段性项目", total="10,000,000.00"),
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _apply(doc)
    detail = str(excinfo.value.detail)
    assert "累计口径" in detail and "不得与年度金额直接比较" in detail
    assert excinfo.value.partial_issues == []


def test_case_e_unit_normalized_equal_passes():
    """Case E：单位不同（万元/元）但可归一且相等 → 0 finding。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某服务项目", annual="本年度预算金额为349.80万元。"),
        _form("某服务项目", "3,498,000.00"),
    )
    assert _apply(doc) == []


def test_case_f_unknown_unit_is_insufficient_data():
    """Case F：申报表单位未声明 → insufficient_data，不产出确定性结论。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(project="某项目", annual="2026年度预算安排100万元。"),
        _form("某项目", "1,000,000.00", unit_decl="（口径未注明）"),
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _apply(doc)
    assert "单位未声明" in str(excinfo.value.detail)
    assert excinfo.value.partial_issues == []


def test_pa1_explanation_escape_hatch():
    """年度安排缺失但存在等效说明（已在上年安排）→ 不报。"""
    doc = _budget_doc(
        NARRATIVE_TMPL.format(
            project="某收尾项目",
            annual="2025年安排建设资金5000万元，建设资金已在上年安排。",
        ),
    )
    assert _apply(doc) == []


def test_pa2_explanation_escape_hatch():
    """同项目年度金额不一致但文中已给出差异原因 → 不报（义务 basis：
    不一致时须给出解释——解释在场即满足）。"""
    narrative = NARRATIVE_TMPL.format(
        project="某改造工程",
        annual="2026年度预算安排100万元，与申报表差异原因系含上年结转资金。",
    )
    doc = _budget_doc(narrative, _form("某改造工程", "1,200,000.00"))
    assert _apply(doc) == []


def test_mixed_finding_and_unresolved_preserves_partial_issues():
    """PA-1 finding 与配对面 unresolved 并存：partial finding 不得被丢弃。"""
    narrative_pair = NARRATIVE_TMPL.format(
        project="某改造工程",
        annual="2026年度预算安排100万元。",
    )
    doc = _budget_doc(
        WENLV_TRUTH_CHUNK,  # PA-1 正例（说明侧）
        narrative_pair,     # 与申报表同名配对，但申报表单位未声明（Case F）
        _form("某改造工程", "1,000,000.00", unit_decl="（口径未注明）"),
    )
    with pytest.raises(RuleDeferred) as excinfo:
        _apply(doc)
    detail = str(excinfo.value.detail)
    assert "单位未声明" in detail, "配对面的取数不足必须记 unresolved"
    partial = excinfo.value.partial_issues
    assert partial, "PA-1 finding 不得被 unresolved 吞掉"
    assert len(partial) == 1
    assert partial[0].location["project"] == "真如海心剧院精装修工程"


def test_neither_disclosure_element_is_not_applicable():
    """两种披露形态都不存在 → not_applicable（不是 insufficient_data）。"""
    doc = _budget_doc("一、部门预算总体说明\n2026年预算安排1000万元。")
    with pytest.raises(RuleNotApplicable):
        _apply(doc)


# ---------------------------------------------------------------------------
# 变异测试（任务 §十：删除身份/忽略阶段/撤册 必须红）
# ---------------------------------------------------------------------------


def _two_project_doc() -> Any:
    """双项目夹具：甲项目一致、乙项目不一致（乙差 20 万元）。

    申报表按名称倒序排列——按位置配对会把甲项目和乙项目的申报表凑对。
    """
    narrative_a = NARRATIVE_TMPL.format(project="甲项目", annual="2026年度预算安排100万元。")
    narrative_b = NARRATIVE_TMPL.format(project="乙项目", annual="2026年度预算安排100万元。")
    form_b = _form("乙项目", "1,200,000.00")  # 乙项目不一致
    form_a = _form("甲项目", "1,000,000.00")  # 甲项目一致
    return _budget_doc(narrative_a, narrative_b, form_b, form_a)


def test_mutation_a_deleting_project_identity_must_be_red(monkeypatch):
    """Mutation A：删除项目身份（按位置配对）必须红。

    正确行为：恰好 1 条 finding 且落在乙项目（名称配对）。变异后按位置
    配对把甲项目对上乙项目的申报表——project 断言必红。
    """
    issues = _apply(_two_project_doc())
    assert len(issues) == 1
    assert issues[0].location["project"] == "乙项目"
    assert issues[0].location["amount_b"] == "1,200,000.00"

    # 模拟变异：配对退化为位置 zip（项目身份被删除）
    monkeypatch.setattr(
        budget_rules_module,
        "_pair_perf_records",
        lambda sections, forms: (list(zip(sections, forms)), []),
    )
    mutated = _apply(_two_project_doc())
    assert len(mutated) == 1
    # 变异后命中的项目与金额都变了——原断言（project==乙项目）必红
    assert mutated[0].location["project"] == "甲项目"
    assert mutated[0].location["amount_b"] == "1,200,000.00"


def test_mutation_b_ignoring_phase_identity_must_be_red(monkeypatch):
    """Mutation B：忽略阶段身份（拿累计口径项目资金总额当年度金额比）必须红。

    真实建管委材料上：年度↔年度归一一致（0 finding）；把项目资金总额
    （77,132.58 万累计）当年度金额 →凭空出现 54,518.07 万元差异——
    误报即变异证据。
    """
    assert _apply(_document(materials_loader()["jgw"])) == []

    original = R33PerfPhaseAmount.__dict__["_collect_forms"].__func__

    def _ignore_phase(doc):
        forms = original(doc)
        for form in forms:
            if form["total_raw"] is not None:
                form["annual_raw"] = form["total_raw"]  # 阶段累计被当年度金额
        return forms

    monkeypatch.setattr(R33PerfPhaseAmount, "_collect_forms", staticmethod(_ignore_phase))
    mutated = _apply(_document(materials_loader()["jgw"]))
    assert len(mutated) == 1
    assert mutated[0].location["diff_wan"] == "54518.07"


def materials_loader() -> Dict[str, Dict[str, Any]]:
    """模块内便捷装载（变异测试用；SHA 校验同 fixture）。"""
    return _load_fixture()


def test_mutation_c_withdrawing_registry_must_be_red(monkeypatch):
    """Mutation C：从 budget 注册表撤下 checker 必须红——义务回到
    not_implemented、registered_rule_ids 不含本规则。"""
    monkeypatch.setattr(
        budget_rules_module,
        "ALL_BUDGET_RULES",
        [
            rule
            for rule in budget_rules_module.ALL_BUDGET_RULES
            if getattr(rule, "code", "") != RULE_ID
        ],
    )
    assert RULE_ID not in registered_rule_ids("budget")
    ledger = build_obligation_ledger(None, report_kind="budget")
    items = [
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, "budget 台账缺少 OBL-PERF-PHASE-AMOUNT 实例"
    assert items[0]["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert items[0]["missing_checkers"] == [RULE_ID]


# ---------------------------------------------------------------------------
# 注册、管线与义务台账
# ---------------------------------------------------------------------------


def test_rule_registered_in_budget_only():
    """budget 必含；final 不得含（OBL-PERF-PHASE-AMOUNT 是 budget only）。"""
    assert RULE_ID in registered_rule_ids("budget")
    assert RULE_ID not in registered_rule_ids("final")


def test_truth_pipeline_executes_in_full_budget_run(materials):
    """pipeline 面：budget 规则集必须执行本规则且 STATUS_FAIL（文旅局真值）。"""
    issues, outcomes = run_rules_with_outcomes(
        _document(materials["wenlv"]), False, report_kind="budget"
    )
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "budget 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    hits = [issue for issue in issues if issue.rule == RULE_ID]
    assert len(hits) == 1, "完整 budget 执行未保留阶段口径错配 finding"


def test_truth_anchor_pipeline_passes_in_full_budget_run(materials):
    """真实负例锚在完整 budget 管线下必须 pass（不得产生任何本规则 finding）。"""
    for label in ("jgw", "cgjf"):
        issues, outcomes = run_rules_with_outcomes(
            _document(materials[label]), False, report_kind="budget"
        )
        outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
        hits = [issue for issue in issues if issue.rule == RULE_ID]
        assert not hits, f"{label} 出现误报: {[i.message for i in hits]}"
        assert outcome.status != STATUS_FAIL


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：OBL-PERF-PHASE-AMOUNT 不再 not_implemented；回执驱动状态。"""
    assert RULE_ID in registered_rule_ids("budget")

    ledger = build_obligation_ledger(None, report_kind="budget")
    items = [
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    ]
    assert items, "budget 台账缺少 OBL-PERF-PHASE-AMOUNT 实例"
    instance = items[0]
    assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None,
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {RULE_ID: "fail"}},
    )
    receipt = next(
        item
        for item in completed["instances"]
        if item["obligation_id"] == OBLIGATION_ID
    )
    assert receipt["status"] == "completed"
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None,
        report_kind="budget",
        rule_execution_summary={"rule_statuses": {RULE_ID: "insufficient_data"}},
    )
    deferred = next(
        item
        for item in insufficient["instances"]
        if item["obligation_id"] == OBLIGATION_ID
    )
    assert deferred["status"] != "completed", (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )
