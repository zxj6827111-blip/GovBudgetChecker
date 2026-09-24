"""V33-CROSS-SAN-GONG-ECON：三公经费表 × 基本支出经济分类表 跨表资金来源一致性。

真值来源（冻结，不得改来迁就实现）
--------------------------------
- 材料：上海市普陀区人民政府宜川路街道办事处 2025 年度部门决算（41 页）
- 源 PDF SHA-256：``f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03``
  （与 ``outputs/sample_validation_20260916/1/metadata.json`` 记录的 2026-09-16
  证据同一份文件）
- 人工判定：``outputs/sample_validation_20260916/comparison.md`` 的 Y02
  （程度：高）——「基本支出表与三公披露冲突：出国13.24 vs 0；接待8.31 vs 0.30；
  车辆运行81.11 vs 19.56 万元。基本支出分项已超过对应三公总口径。不能仅凭
  PDF 确定哪侧正确。」物理页 22（基本支出决算表）、24（三公经费支出决算表）。
- 该判定原文同时登记「漏报；未报告这组三项跨表矛盾」——本文件的正例就是
  把这条漏报变成回归。

这里只断言**业务事实**：两侧金额、差额、页码、表身份、严重度、以及"不报什么"。
不把实现细节（列组结构、车道推导）写进期望值，否则实现改了测试就得跟着改。
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_COMPLETED,
    OBLIGATION_INSUFFICIENT_DATA,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import build_document, run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import R33CrossSanGongEcon  # noqa: E402
from src.schemas.document_profile import DocumentProfile  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402
from src.services.material_detail_query_service import build_coverage  # noqa: E402
from src.services import review_lifecycle_service  # noqa: E402

RULE_ID = "V33-CROSS-SAN-GONG-ECON"
OBLIGATION_ID = "OBL-CROSS-SAN-GONG-ECON"

TRUTH_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
#: 源 PDF SHA（溯源）
TRUTH_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
#: 夹具内容 SHA（防夹具本体被改；与源 SHA 双锁，与既有样张集成测试同口径）
TRUTH_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 另一份真实样张（生态环境局）作为**同口径一致**的负例：
#: FIN_06 的三公经济分类行与 FIN_07 分项决算数逐项相等
#: （302-12=0.00、302-17=0.00、302-31=16.95、310-13=0.00 对 0.00/0.00/16.95/0.00）。
SAMPLE_FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"
SAMPLE_SOURCE_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"
SAMPLE_FIXTURE_SHA = "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1"

FIN_06_TITLE = "一般公共预算财政拨款基本支出决算表"
FIN_07_TITLE = "财政拨款“三公”经费支出决算表"

#: 样张物理页（1 基）：22 = 基本支出决算表（含续页 23），24 = 三公经费支出决算表
PAGE_FIN_06 = 22
PAGE_FIN_07 = 24


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
    """宜川路街道 2025 年度决算（Y02 漏报的冻结真值样张）。"""
    return _load_fixture(TRUTH_FIXTURE, TRUTH_SOURCE_SHA, TRUTH_FIXTURE_SHA)


@pytest.fixture(scope="module")
def consistent_sample() -> Dict[str, Any]:
    """生态环境局 2025 年度决算（同口径一致的真实负例）。"""
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
    copy = {
        "doc_id": payload.get("doc_id", "sample"),
        "page_texts": list(payload["page_texts"]),
        "page_tables": json.loads(json.dumps(payload["page_tables"])),
    }
    mutate(copy)
    return _document(copy)


def _run(doc: Any) -> List[Any]:
    return list(R33CrossSanGongEcon().apply(doc))


def _by_item(issues: Sequence[Any]) -> Dict[str, Any]:
    return {str(issue.location.get("san_gong_item") or ""): issue for issue in issues}


def _replace_cell(page_tables: List[Any], page: int, row: int, col: int, value: str) -> None:
    page_tables[page - 1][0][row][col] = value


def _find_row(page_tables: List[Any], page: int, needle: str) -> int:
    for index, row in enumerate(page_tables[page - 1][0]):
        if any(needle in str(cell or "") for cell in row):
            return index
    raise AssertionError(f"第 {page} 页未找到含「{needle}」的行")


def _fin07_amount_row(page_tables: List[Any], page: int = PAGE_FIN_07) -> int:
    """三公表金额行 = 该页表格的最后一行（表头/科目名/预算数决算数标签行在上方）。

    不能按关键词找行：三公表的表头行里同样有「公务接待费」字样，按关键词会拿到表头。
    """
    return len(page_tables[page - 1][0]) - 1


# ---------------------------------------------------------------------------
# 正例：Y02 真值必须命中
# ---------------------------------------------------------------------------


def test_y02_truth_reports_all_three_cross_table_conflicts(truth):
    """Y02（高）：三项基本支出分项大于三公表同一业务项的决算数，必须各报一条。"""
    issues = _run(_document(truth))

    assert len(issues) == 3, [issue.message for issue in issues]
    assert {issue.rule for issue in issues} == {RULE_ID}

    items = _by_item(issues)
    assert set(items) == {"overseas", "vehicle_operation", "reception"}

    expected = {
        "overseas": (Decimal("13.24"), Decimal("0.00"), Decimal("13.24")),
        "reception": (Decimal("8.31"), Decimal("0.30"), Decimal("8.01")),
        "vehicle_operation": (Decimal("81.11"), Decimal("19.56"), Decimal("61.55")),
    }
    for key, (basic, three_public, diff) in expected.items():
        issue = items[key]
        assert issue.location["basic_amount"] == str(basic)
        assert issue.location["three_public_amount"] == str(three_public)
        assert issue.location["difference"] == str(diff)
        assert issue.location["unit"] == "万元"
        assert issue.location["fiscal_year"] == 2025
        assert issue.severity == "error", "部分大于整体的跨表矛盾不得降级为提示"


def test_y02_findings_carry_locatable_evidence_for_both_tables(truth):
    """finding 必须回答"哪张表、第几页、哪个业务项、两侧多少"，用户能回原 PDF。"""
    issues = _run(_document(truth))
    overseas = _by_item(issues)["overseas"]

    # 两侧表身份 + 页码：基本支出表 P22、三公经费表 P24
    assert overseas.location["table"] == FIN_06_TITLE
    assert overseas.location["page"] == PAGE_FIN_06
    assert overseas.location["pages"] == [PAGE_FIN_06, PAGE_FIN_07]
    refs = overseas.location["table_refs"]
    assert {ref["table"] for ref in refs} == {FIN_06_TITLE, FIN_07_TITLE}
    assert {ref["page"] for ref in refs} == {PAGE_FIN_06, PAGE_FIN_07}
    # 两侧业务项身份：三公表列组主体 + 基本支出表经济分类科目（含编码）
    assert overseas.location["field"] == "因公出国（境）费"
    assert overseas.location["code"] == "30212"
    # 义务编号：finding 与台账实例可对照
    assert overseas.location["obligation_id"] == OBLIGATION_ID

    evidence = overseas.evidence_text or ""
    for token in (
        FIN_06_TITLE,
        FIN_07_TITLE,
        "因公出国（境）费",
        "13.24",
        "0.00",
        "13.24",
        "万元",
        "2025",
    ):
        assert token in evidence, f"证据缺少 {token}: {evidence}"


def test_y02_finding_does_not_claim_which_side_is_wrong(truth):
    """人工判定明确写了"不能仅凭PDF确定哪侧正确"，finding 不得替审校人下结论。"""
    issues = _run(_document(truth))
    for issue in issues:
        text = f"{issue.message}{issue.evidence_text or ''}"
        assert "两表不能同时成立" in text
        assert "无法判定哪一侧" in text or "无法判断哪一侧" in text


def test_vehicle_purchase_is_skipped_when_basic_table_lists_no_amount(truth):
    """基本支出表 310-13 公务用车购置未列金额（空白）→ 不比较、不报。

    空白不等于 0：把未列示当 0 参与比较，会凭空造出"未列示 > 0"的假冲突。
    """
    issues = _run(_document(truth))
    assert "vehicle_purchase" not in _by_item(issues)


# ---------------------------------------------------------------------------
# 负例：同口径一致不得报
# ---------------------------------------------------------------------------


def test_consistent_real_sample_reports_nothing(consistent_sample):
    """生态环境局样张：基本支出三公经济分类行与三公表分项决算数逐项一致。

    302-12=0.00、302-17=0.00、302-31=16.95、310-13=0.00，三公表为
    0.00/0.00/16.95/0.00——部分等于整体（三公支出全部发生在基本支出），
    必须零 finding。
    """
    assert _run(_document(consistent_sample)) == []


def test_component_smaller_than_total_is_normal_not_a_conflict(truth):
    """基本支出分项 < 三公表决算数（差额为项目支出）是正常口径，不得报。

    真值样张的"接待 8.31 vs 0.30"之所以是冲突，是因为方向反了；
    改成 8.31 ≤ 8.31（含项目支出）后同一份材料不应再报这一项。
    """
    def mutate(payload: Dict[str, Any]) -> None:
        row = _fin07_amount_row(payload["page_tables"])
        # 三公表公务接待费决算数 = 9.00（基本支出 8.31 + 项目支出部分）
        _replace_cell(payload["page_tables"], PAGE_FIN_07, row, 11, "9.00")
        # 合计同步：19.86 - 0.30 + 9.00 = 28.56
        _replace_cell(payload["page_tables"], PAGE_FIN_07, row, 1, "28.56")

    issues = _run(_clone(truth, mutate))
    assert "reception" not in _by_item(issues)
    # 其余两项仍按真值报出（变异不影响它们）
    assert set(_by_item(issues)) == {"overseas", "vehicle_operation"}


def test_rounding_level_excess_is_a_hint_not_a_hard_error(truth):
    """纯显示舍入差（≤ 两侧半步长）不得报高严重度正式问题。"""

    def mutate(payload: Dict[str, Any]) -> None:
        # 基本支出接待 8.31 → 0.31，三公表接待 0.30：差 0.01 ≤ 0.005+0.005
        row = _find_row(payload["page_tables"], PAGE_FIN_06, "公务接待费")
        _replace_cell(payload["page_tables"], PAGE_FIN_06, row, 5, "0.31")
        row3 = _find_row(payload["page_tables"], PAGE_FIN_06, "因公出国")
        _replace_cell(payload["page_tables"], PAGE_FIN_06, row3, 5, "0.00")
        row4 = _find_row(payload["page_tables"], PAGE_FIN_06, "公务用车运行维")
        _replace_cell(payload["page_tables"], PAGE_FIN_06, row4, 5, "19.56")

    issues = _run(_clone(truth, mutate))
    reception = [issue for issue in issues if issue.location.get("san_gong_item") == "reception"]
    assert len(reception) == 1
    assert reception[0].severity == "info"
    assert "舍入" in reception[0].message


# ---------------------------------------------------------------------------
# 误配反例：按业务项身份比，不按"最近的数字"比
# ---------------------------------------------------------------------------


def test_amounts_are_bound_by_business_identity_not_proximity(truth):
    """公务接待/因公出国数字交叉：规则必须跟着业务项走。

    变异后 0.00 属于因公出国（对三公表 0.00 → 不报），
    13.24 属于公务接待（对三公表 0.30 → 报 13.24 vs 0.30）。
    "取最近的数字配一对"的实现会把 13.24 配给因公出国，从而报错业务项。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        tabel6 = payload["page_tables"]
        row_overseas = _find_row(tabel6, PAGE_FIN_06, "因公出国")
        row_reception = _find_row(tabel6, PAGE_FIN_06, "公务接待费")
        _replace_cell(tabel6, PAGE_FIN_06, row_overseas, 5, "0.00")
        _replace_cell(tabel6, PAGE_FIN_06, row_reception, 5, "13.24")
        # 其余两项拉到一致，隔离出"身份绑定"这一个变量
        row_op = _find_row(tabel6, PAGE_FIN_06, "公务用车运行维")
        _replace_cell(tabel6, PAGE_FIN_06, row_op, 5, "19.56")

    issues = _run(_clone(truth, mutate))
    assert len(issues) == 1, [issue.message for issue in issues]
    issue = issues[0]
    assert issue.location["san_gong_item"] == "reception"
    assert issue.location["field"] == "公务接待费"
    assert issue.location["basic_amount"] == "13.24"
    assert issue.location["three_public_amount"] == "0.30"


def test_vehicle_purchase_binds_through_the_310_economic_class(truth):
    """公务用车购置（31013 资本性支出）也按「名称 + 精确编码」绑定，不能只扫 302 类。"""

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row_purchase = _find_row(page_tables, PAGE_FIN_06, "公务用车购置")
        _replace_cell(page_tables, PAGE_FIN_06, row_purchase, 5, "20.00")

    issues = _run(_clone(truth, mutate))
    purchase = _by_item(issues)["vehicle_purchase"]
    assert purchase.location["code"] == "31013"
    assert purchase.location["basic_amount"] == "20.00"
    assert purchase.location["three_public_amount"] == "0.00"


def test_amount_in_the_other_lane_is_not_bound_to_this_item(truth):
    """同一行的左栏金额不属于右栏业务项：跨栏取数必须被拦住。

    把因公出国的金额从右栏移入左栏（左栏那一行是住房公积金）并清空右栏，
    因公出国就"没有可比的金额"，不得把它左侧的数字当成自己的金额。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _find_row(page_tables, PAGE_FIN_06, "因公出国")
        _replace_cell(page_tables, PAGE_FIN_06, row, 2, "13.24")
        _replace_cell(page_tables, PAGE_FIN_06, row, 5, "")

    issues = _run(_clone(truth, mutate))
    assert set(_by_item(issues)) == {"vehicle_operation", "reception"}


# ---------------------------------------------------------------------------
# 精确经济分类代码：名称 + 编码必须同时成立（同类错码 / 缺码 → 取数不足）
# ---------------------------------------------------------------------------

#: 真值样张 FIN_06 的续页（基本支出表跨 22-23 页，合并后的续页行单元格带真实页码）
PAGE_FIN_06_CONTINUATION = 23


def test_each_business_item_binds_its_exact_economic_code(truth):
    """原始真值继续全部命中精确编码：30212 / 30217 / 30231 / 31013。

    三项冲突 finding 的 code 必须分别是 30212/30217/30231；
    31013（公务用车购置）由 test_vehicle_purchase_binds_through_the_310_economic_class 守住。
    """
    items = _by_item(_run(_document(truth)))
    assert items["overseas"].location["code"] == "30212"
    assert items["reception"].location["code"] == "30217"
    assert items["vehicle_operation"].location["code"] == "30231"


def test_same_class_wrong_code_is_an_identity_conflict_not_a_finding(truth):
    """Case A：「30231 公务接待费」——同 302 类但编码指向别的业务项。

    若只判 302 类级前缀，30231（公务用车运行维护费）会被当成接待费继续比，
    产生 8.31 > 0.30 的假正式 finding。正确做法：名称与精确编码必须同时成立，
    否则记取数不足；其余已确认冲突（出国/运行维护）经 partial_issues 保留。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _find_row(page_tables, PAGE_FIN_06, "公务接待费")
        _replace_cell(page_tables, PAGE_FIN_06, row, 3, "30231")  # 原值 30217

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    reason = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "expected 30217" in reason
    assert "actual 30231" in reason

    partial = _by_item(getattr(excinfo.value, "partial_issues", []) or [])
    assert set(partial) == {"overseas", "vehicle_operation"}, (
        "一项错码不得吞掉其它已确认冲突，也不得给 reception 出正式 finding"
    )


def test_missing_code_never_produces_a_formal_finding(truth):
    """Case B：名称命中但编码清空了——不能凭名称"看起来对"就放行正式比较。

    编码不识别时该项记取数不足；若其它业务项已有确认冲突，经
    partial_issues 保留，不因一项缺码整体开天窗。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _find_row(page_tables, PAGE_FIN_06, "因公出国")
        _replace_cell(page_tables, PAGE_FIN_06, row, 3, "")  # 原值 30212

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    assert excinfo.value.status == "insufficient_data"
    reason = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "expected 30212" in reason

    partial = _by_item(getattr(excinfo.value, "partial_issues", []) or [])
    assert "overseas" not in partial
    assert set(partial) == {"vehicle_operation", "reception"}


# ---------------------------------------------------------------------------
# 证据页码：跨页表的续页行，finding 必须写单元格的真实页
# ---------------------------------------------------------------------------


def test_finding_uses_the_actual_cell_page_of_a_continued_row(truth):
    """基本支出表的行续到第 23 页：证据不得落成表起始页 22。

    真值样张的 FIN_06 本来就跨 22-23 两页（合并后 31021/31022 等行的页码是 23）。
    变异把「公务接待费」从 P22 挪到 P23 的既有数据行上（表头签名不动，
    结构化 continuation 合并照旧成立），冲突结论不变，但证据必须改写
    单元格的真实页：FIN_06 第 23 页、FIN_07 第 24 页。

    注：接收行只能用 P23 的 detail 行（原 31022 无形资产购置）。该续页的
    前两行被结构化层归类为 header（续页盲行是解析层的已知形态，见交付文档
    残余风险），与"真实页码"这一资产无关。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _find_row(page_tables, PAGE_FIN_06, "公务接待费")
        for col in (3, 4, 5):  # P22 上接待费的右栏（编码/名称/金额）清掉
            _replace_cell(page_tables, PAGE_FIN_06, row, col, "")
        # P23 续页第一行 detail 行（原 31022 无形资产购置，无金额）换成接待费
        _replace_cell(page_tables, PAGE_FIN_06_CONTINUATION, 2, 3, "30217")
        _replace_cell(page_tables, PAGE_FIN_06_CONTINUATION, 2, 4, "公务接待费")
        _replace_cell(page_tables, PAGE_FIN_06_CONTINUATION, 2, 5, "8.31")

    issues = _run(_clone(truth, mutate))
    items = _by_item(issues)
    assert set(items) == {"overseas", "vehicle_operation", "reception"}, (
        "换一页后三项 Y02 冲突仍应全部命中"
    )

    reception = items["reception"]
    assert reception.location["page"] == PAGE_FIN_06_CONTINUATION  # 23
    assert reception.location["pages"] == [PAGE_FIN_06_CONTINUATION, PAGE_FIN_07]
    refs = {ref["role"]: ref for ref in reception.location["table_refs"]}
    assert refs["基本支出经济分类"]["page"] == PAGE_FIN_06_CONTINUATION
    assert refs["三公经费"]["page"] == PAGE_FIN_07

    evidence = reception.evidence_text or ""
    assert f"第{PAGE_FIN_06_CONTINUATION}页" in evidence
    assert f"第{PAGE_FIN_07}页" in evidence
    assert f"第{PAGE_FIN_06}页" not in evidence, "续页证据不得错标成表起始页"
    assert f"第{PAGE_FIN_06}页" not in reception.message

    # 仍在 P22 的两项不受续页影响
    for key in ("overseas", "vehicle_operation"):
        assert items[key].location["page"] == PAGE_FIN_06


# ---------------------------------------------------------------------------
# 不可比较：取数不足 / 身份不确定一律不产生正式 finding
# ---------------------------------------------------------------------------


def test_missing_economic_classification_table_defers(truth):
    def mutate(payload: Dict[str, Any]) -> None:
        payload["page_tables"][PAGE_FIN_06 - 1] = []
        payload["page_tables"][PAGE_FIN_06] = []  # 续页 23

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def test_missing_three_public_table_defers(truth):
    def mutate(payload: Dict[str, Any]) -> None:
        payload["page_tables"][PAGE_FIN_07 - 1] = []

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def test_unknown_amount_unit_defers(truth):
    """两侧金额单位无法确认（或两侧不一致）时不得比较。"""

    def mutate(payload: Dict[str, Any]) -> None:
        payload["page_texts"][PAGE_FIN_06 - 1] = payload["page_texts"][
            PAGE_FIN_06 - 1
        ].replace("单位：万元", "")

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def test_unresolved_fiscal_year_defers(truth):
    """财政年度无法确认时不得跨表比较（期间一致性是可比性的前提）。"""

    def mutate(payload: Dict[str, Any]) -> None:
        payload["page_texts"] = [text.replace("2025", "") for text in payload["page_texts"]]

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def test_ambiguous_three_public_column_identity_defers(truth):
    """三公表出现两个同名业务项列组：列身份有歧义，不得猜一个来用。"""

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        # 把「因公出国（境）费」表头改成「公务接待费」→ 接待费列组出现两次
        _replace_cell(page_tables, PAGE_FIN_07, 1, 2, "公务接待费")

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def test_non_numeric_cell_in_amount_column_defers(truth):
    """决算数列出现非数值文本（非空白、非破折号）：单元格语义不确定 → 取数不足。"""

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _fin07_amount_row(page_tables)
        _replace_cell(page_tables, PAGE_FIN_07, row, 11, "见说明")

    with pytest.raises(RuleDeferred):
        _run(_clone(truth, mutate))


def _retitle_vehicle_subtotal(payload: Dict[str, Any]) -> None:
    """把「小计」列组标题改掉，模拟不列小计列组的三公表。"""
    page_tables = payload["page_tables"]
    _replace_cell(page_tables, PAGE_FIN_07, 2, 4, "公车费用")


def test_missing_vehicle_subtotal_column_does_not_block_explicit_items(truth):
    """三公表没有「小计」列组，但四个分项都是明确数值：照样逐项比较。

    汇总结（合计/小计）只在"有空白单元格需要确认为 0"时才必需；把它们
    无条件当成前提条件，会让不含小计列组的合规三公表永远记取数不足。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        _retitle_vehicle_subtotal(payload)
        row = _fin07_amount_row(payload["page_tables"])
        # 出国与购置由空白改为明确 0.00 → 不再需要勾稽复算
        _replace_cell(payload["page_tables"], PAGE_FIN_07, row, 3, "0.00")
        _replace_cell(payload["page_tables"], PAGE_FIN_07, row, 7, "0.00")

    issues = _run(_clone(truth, mutate))
    assert set(_by_item(issues)) == {"overseas", "vehicle_operation", "reception"}


def test_missing_vehicle_subtotal_plus_blank_cell_defers(truth):
    """既没有小计列组、又有空白单元格：空白无法确认为 0 → 该项取数不足。"""

    def mutate(payload: Dict[str, Any]) -> None:
        _retitle_vehicle_subtotal(payload)
        row = _fin07_amount_row(payload["page_tables"])
        _replace_cell(payload["page_tables"], PAGE_FIN_07, row, 3, "0.00")

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    # 公务用车购置仍为空白且勾稽复算不可能 → 不参与正式比较
    partial = list(getattr(excinfo.value, "partial_issues", []) or [])
    assert "vehicle_purchase" not in {
        issue.location["san_gong_item"] for issue in partial
    }


def test_blank_cell_is_zero_only_when_the_table_closes(truth):
    """空白单元格要靠本表勾稽确认为 0；勾稽不成立时不得当 0 用。

    变异后三公表合计 19.86 ≠ 出国(空白) + 小计 19.56 + 接待(空白)，
    因此"接待 = 0"无法确认 → 该分项取数不足；但公务用车运行维护费
    （两侧都明确）已确认的冲突必须作为 partial finding 保留下来，
    不能被取数不足吞掉。
    """

    def mutate(payload: Dict[str, Any]) -> None:
        page_tables = payload["page_tables"]
        row = _fin07_amount_row(page_tables)
        _replace_cell(page_tables, PAGE_FIN_07, row, 11, "")

    with pytest.raises(RuleDeferred) as excinfo:
        _run(_clone(truth, mutate))
    partial = list(getattr(excinfo.value, "partial_issues", []) or [])
    assert [issue.location["san_gong_item"] for issue in partial] == ["vehicle_operation"]
    assert excinfo.value.status == "insufficient_data"


# ---------------------------------------------------------------------------
# 注册与台账：checker 必须真的进注册表，台账状态随回执真实形成
# ---------------------------------------------------------------------------


def test_rule_is_registered_for_final_and_absent_from_budget():
    """本义务当前只适用决算：不得注册成预算 checker。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")


def test_truth_defect_survives_the_real_final_pipeline(truth):
    """端到端：走真实 final 规则集执行后，Y02 的三条冲突仍在 findings 与回执里。

    这条同时守住"真注册、真执行"：checker 一旦从 ALL_RULES 撤下（或执行时抛错），
    findings 与 ``outcomes`` 都会缺这条规则，测试立刻红。
    """
    doc = _document(truth)
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="final")
    mine = [issue for issue in issues if issue.rule == RULE_ID]
    assert len(mine) == 3, [issue.message for issue in mine]
    statuses = {
        outcome.rule_id: outcome.status
        for outcome in outcomes
        if outcome.rule_id == RULE_ID
    }
    assert statuses == {RULE_ID: "fail"}


def _profile(kind: str = "final") -> DocumentProfile:
    profile = resolve_document_profile(
        doc_type="dept_final" if kind == "final" else "dept_budget",
        filename=f"2024年度{'部门决算' if kind == 'final' else '部门预算'}.pdf",
        page_texts=[f"2024年度{'部门决算' if kind == 'final' else '部门预算'}"],
    )
    assert profile.kind == kind
    return profile


def _instance(ledger: Dict[str, Any], obligation_id: str) -> Dict[str, Any]:
    for item in ledger["instances"]:
        if item["obligation_id"] == obligation_id:
            return item
    raise AssertionError(f"obligation {obligation_id} not found in ledger")


def _ledger_with(status: str) -> Dict[str, Any]:
    return build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary={"rule_statuses": {RULE_ID: status}},
    )


@pytest.mark.parametrize("status", ["pass", "fail"])
def test_obligation_completes_on_legal_terminal_statuses(status):
    """pass / fail 都代表 checker 已执行完成——finding 与义务完成是两件事。"""
    ledger = _ledger_with(status)
    instance = _instance(ledger, OBLIGATION_ID)
    assert instance["status"] == OBLIGATION_COMPLETED
    assert instance["checkers"] == [RULE_ID]
    assert instance["missing_checkers"] == []
    # 已完成 → 不再计入门禁的"待补核"集合（blocking = blocks_gate + 未完成状态）
    assert OBLIGATION_ID not in ledger["blocking_obligation_ids"]


def test_rule_self_reported_not_applicable_leaves_the_denominator():
    """只有 checker 自己判定不适用时才移出分母：不阻塞门禁，且依据可读。"""
    ledger = _ledger_with("not_applicable")
    instance = _instance(ledger, OBLIGATION_ID)
    assert instance["status"] == "not_applicable"
    assert OBLIGATION_ID not in ledger["blocking_obligation_ids"]
    assert RULE_ID in instance["detail"]


def test_obligation_stays_blocking_when_the_checker_lacks_data():
    """取数不足仍属未完成：不得因为"checker 已实现"就当成通过。"""
    ledger = _ledger_with("insufficient_data")
    instance = _instance(ledger, OBLIGATION_ID)
    assert instance["status"] == OBLIGATION_INSUFFICIENT_DATA
    assert instance["blocks_gate"] is True
    assert OBLIGATION_ID in ledger["blocking_obligation_ids"]


def test_catalog_no_longer_declares_the_checker_as_a_gap():
    """清单里不得再把它写成 pending：声明了 checker 就必须真在注册表里。"""
    instance = _instance(_ledger_with("pass"), OBLIGATION_ID)
    assert instance["gap_note"] == ""
    assert set(instance["depends_on"]) == {"table:FIN_06", "table:FIN_07"}


def test_review_gate_drops_the_obligation_once_the_checker_completes():
    """WP3-B 联动：checker 执行完成后该项不再进人工补核待办。

    同一条链路上，取数不足时仍必须留在待补核里——门禁口径来自
    ``blocks_gate + 未完成状态``，不由"checker 是否已实现"决定。
    """
    from src.services import review_context_query  # noqa: F401  # 保持装配顺序

    completed_ledger = _ledger_with("fail")
    blocked_ledger = _ledger_with("insufficient_data")

    completed_coverage = build_coverage(completed_ledger)
    blocked_coverage = build_coverage(blocked_ledger)
    assert completed_coverage.available and blocked_coverage.available

    completed_instance = next(
        item for item in completed_coverage.items if item.obligation_id == OBLIGATION_ID
    )
    blocked_instance = next(
        item for item in blocked_coverage.items if item.obligation_id == OBLIGATION_ID
    )
    assert completed_instance.status == OBLIGATION_COMPLETED
    assert blocked_instance.status == OBLIGATION_INSUFFICIENT_DATA

    # 门禁的待补核集合：completed 不再计入，insufficient_data 仍计入
    unhandled_completed = [
        item
        for item in completed_coverage.items
        if item.blocks_gate and item.status != OBLIGATION_COMPLETED
    ]
    unhandled_blocked = [
        item
        for item in blocked_coverage.items
        if item.blocks_gate and item.status != OBLIGATION_COMPLETED
    ]
    assert OBLIGATION_ID not in {item.obligation_id for item in unhandled_completed}
    assert OBLIGATION_ID in {item.obligation_id for item in unhandled_blocked}
    assert len(unhandled_blocked) == len(unhandled_completed) + 1
    assert review_lifecycle_service is not None  # 门禁模块可用（口径同源）
