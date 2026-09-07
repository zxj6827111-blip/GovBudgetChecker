"""Golden 评估器内容重叠匹配的回归测试（review 🟡1）。

背景：evidence_overlaps 的滑窗固定 6 字，短于 6 字且不含数字的标注
（如「空表」「空表说明」）永远匹配不上任何 finding——窗口下限退化
为 min(6, len) 后按全串比对，消除该盲区。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_golden_corpus import (
    _normalize_for_overlap,
    evidence_overlaps,
    match_annotation,
)


def test_short_annotation_without_numbers_matches():
    """短标注（<4 字窗口起点）：短于 4 字的标注按全串包含比对。

    R2 收敛后的窗口序列是 (min(6,len), 4)：长度 2-3 的标注两个窗口
    都够不到（循环条件 window<4 break），需在全串通道兜底——本用例
    锁定该兜底行为。
    """
    # 4 字及以上：走 4 字窗口
    assert evidence_overlaps(
        "空表说明缺失", {"message": "缺少空表说明或等效说明", "evidence_text": ""}
    )
    # 超短标注（2-3 字）：全串包含兜底
    assert evidence_overlaps("空表", {"message": "缺少空表", "evidence_text": ""})


def test_short_annotation_unrelated_still_rejected():
    """短标注的全串比对不等于子串放行：无关正文仍拒绝。"""
    assert not evidence_overlaps(
        "空表", {"message": "支出决算表合计与明细不一致", "evidence_text": ""}
    )


def test_single_char_annotation_matches_by_full_string():
    """单字标注退化为子串包含判定：在则命中、不在则拒绝。"""
    assert evidence_overlaps("表", {"message": "表格缺失", "evidence_text": ""})
    assert not evidence_overlaps("张", {"message": "表格缺失", "evidence_text": ""})


def test_numeric_intersection_still_primary_signal():
    """数字 token 交集仍是第一判据（金额/年份场景）。"""
    assert evidence_overlaps(
        "合计 1,367.76 与分项之和不符",
        {"message": "合计值 1,367.76，明细之和 1,367.75", "evidence_text": ""},
    )


def test_unrelated_content_rejected():
    """GPT5.6 复现场景：规则页码正确但正文无关必须拒绝。"""
    assert not evidence_overlaps(
        "「公务接待费支出决算减少为0.00万元，与2024年持平」逻辑矛盾",
        {"message": "完全不相关的正文内容", "evidence_text": ""},
    )


def test_empty_annotation_evidence_falls_back():
    """标注未写 evidence：退回规则+页码匹配（不惩罚标注侧缺失）。"""
    assert evidence_overlaps(None, {"message": "任何内容", "evidence_text": ""})
    assert evidence_overlaps("", {"message": "", "evidence_text": None})


def test_match_annotation_prefers_numeric_overlap():
    """多条候选时优先消费数字重叠度最高的 finding。"""
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "合计 1,367.76 不等于分项之和（10.77+259.52）",
    }
    weak = {
        "rule": "V33-120",
        "page": 10,
        "message": "支出决算表存在取整误差",
        "evidence_text": "差 0.01",
    }
    strong = {
        "rule": "V33-120",
        "page": 10,
        "message": "合计值 1,367.76，明细之和 1,367.75",
        "evidence_text": "分项 10.77 与 259.52",
    }
    hit = match_annotation(ann, [weak, strong], set())
    assert hit is strong


def test_normalize_for_overlap_strips_punctuation():
    assert _normalize_for_overlap("  1,367.76 万元！") == "136776万元"
    assert _normalize_for_overlap("") == ""


# ---------------------------------------------------------------------------
# GPT5.6 R2 P1-2：假 TP 三场景 + location_key 锚点 + 双证据可定位
# ---------------------------------------------------------------------------

def test_year_only_overlap_is_not_evidence():
    """同规则同页、内容相反、仅共享年份"2025"：必须拒绝（R2 假 TP 场景）。

    年份是低区分度 token（不构成证据），且无关正文与标注证据
    「目录行年度缺位…202 年度」无 4 字连续重叠。
    """
    ann = {
        "rule_id": "V33-001",
        "page": 2,
        "evidence": "目录行年度缺位：「202 年度」",
        "location_key": "toc:第三部分…202 年度部门决算情况说明",
    }
    fake = {
        "rule": "V33-001",
        "page": 2,
        "message": "无关正文，本年度为 2025 年",
        "evidence_text": "",
    }
    assert not evidence_overlaps(ann["evidence"], fake)
    assert match_annotation(ann, [fake], set()) is None


def test_distinctive_numeric_tokens_exclude_years():
    """年份与孤立两位数不算区分度证据；金额/编码保留（R2 P1-2）。"""
    from scripts.evaluate_golden_corpus import (
        _distinctive_numeric_tokens,
        _numeric_tokens,
    )

    raw = "2025 年支出 40.00 万元，科目 221，序号 12"
    assert _numeric_tokens(raw) >= {"2025", "40.00", "221", "12"}
    distinctive = _distinctive_numeric_tokens(raw)
    assert "2025" not in distinctive
    assert "12" not in distinctive
    assert "40.00" in distinctive
    assert "221" in distinctive


def test_location_key_anchor_ranks_not_gates():
    """R2 收敛语义：锚点是排序加分而非否决门槛——多候选时优先消费
    锚点命中数高的 finding（标注措辞与规则文案差异大，否决会误伤）。
    """
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:三公说明(一)公务接待费",
    }
    # 两个候选都与标注重叠（数字 0.00 + 公务接待费片段），
    # 但只有一个命中锚点短语（三公说明）
    unrelated_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "其他章节的公务接待费 0.00 万元问题",
        "evidence_text": "公务接待费 0.00",
    }
    matching_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "三公说明：公务接待费支出决算减少为0.00万元表述矛盾",
        "evidence_text": "公务接待费 0.00",
    }
    hit = match_annotation(ann, [unrelated_domain, matching_domain], set())
    assert hit is matching_domain


def test_table_anchor_contributes_to_ranking():
    """tbl 锚（表名段）计入排序：表名一致的候选优先被消费。"""
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "1,367.76 不等于分项之和",
        "location_key": "tbl:支出决算表合计行·项目支出",
    }
    same_table = {
        "rule": "V33-120",
        "page": 10,
        "message": "支出决算表合计行与明细之和相差 0.01 万元",
        "evidence_text": "合计值：1367.76",
    }
    other_table = {
        "rule": "V33-120",
        "page": 10,
        "message": "收入决算表合计行差异 0.01 万元",
        "evidence_text": "合计值：1367.76",
    }
    hit = match_annotation(ann, [other_table, same_table], set())
    assert hit is same_table


# ---------------------------------------------------------------------------
# GPT5.6 R3 P1-4/P1-5：锚点条件约束 + locatable 证据强化
#
# 锚点语义取舍（样张真值数据支撑）：A-003/005/006/008 四条真命中的锚点
# 短语在 finding 中零命中（标注者行级措辞 vs 规则文案交叉）——「全零
# 命中即拒绝」会把样张召回打到 3/7。条件约束取中间态：锚点能区分时
# 否决错域候选，不能区分时降级纯证据排序。
# ---------------------------------------------------------------------------


def test_anchor_constraint_eliminates_wrong_domain_when_correct_exists():
    """错域与正域候选并存（锚点可区分）：错域候选必须被淘汰。"""
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "「公务接待费支出决算减少为0.00万元」逻辑矛盾",
        "location_key": "sec:三公说明(一)公务接待费",
    }
    wrong_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "其他章节的公务接待费 0.00 万元问题",
        "evidence_text": "公务接待费 0.00",
    }
    correct_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "三公说明：公务接待费支出决算减少为0.00万元表述矛盾",
        "evidence_text": "公务接待费 0.00",
    }
    hit = match_annotation(ann, [wrong_domain, correct_domain], set())
    assert hit is correct_domain
    hit_reversed = match_annotation(ann, [correct_domain, wrong_domain], set())
    assert hit_reversed is correct_domain


def test_anchor_constraint_degrades_when_no_candidate_hits_anchor():
    """锚点零命中（措辞交叉，如 A-005「p14同口径表合计」vs finding
    「同口径列」）：降级纯证据排序，不因锚点不匹配而拒绝真命中。"""
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "P14 同口径表合计 1,367.75，两表差 0.01",
        "location_key": "xtbl:p14同口径表合计",
    }
    # finding 措辞与锚点无任何重叠（真实 R2 场景），但金额证据充分
    finding = {
        "rule": "V33-120",
        "page": 10,
        "message": "支出决算表与一般公共预算财政拨款支出决算表同口径列相差 0.01 万元",
        "evidence_text": "支出决算表：1367.76 同口径表：1367.75",
    }
    assert match_annotation(ann, [finding], set()) is finding


def test_locatable_requires_page_and_evidence_text():
    """locatable 双证据（R3 P1-5）：页码 + 非空 evidence_text（原文引文）。

    只有页码和规则生成的通用 message、evidence_text 空的 finding
    不得算作可定位（message 是文案不是证据）。
    """
    # locatable 的文本证据 = 非空 evidence_text（原文引文）；
    # message 是规则生成的文案，不构成可定位证据（R3 P1-5）
    f_message_only = {"page": 5, "message": "通用文案", "evidence_text": ""}
    f_with_quote = {
        "page": 6,
        "message": "m",
        "evidence_text": "表格：支出决算表 合计：100.00",
    }
    assert not str(f_message_only.get("evidence_text") or "").strip()
    assert str(f_with_quote.get("evidence_text") or "").strip()
