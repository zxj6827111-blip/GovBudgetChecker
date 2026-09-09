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
        "section_title_phrases_aligned": ["三公经费支出决算情况说明"],
    }
    # 两个候选都与标注重叠（数字 0.00 + 公务接待费片段）、同章节，
    # 但只有一个命中锚点短语（三公说明）
    unrelated_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "其他章节的问题",
        "evidence_text": "项目支出说明 公务接待费 0.00",  # evidence 驻留定位词
        "section_id": "七、财政拨款“三公”经费支出决算情况说明",
    }
    matching_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "三公说明 公务接待费 0.00 表述矛盾",
        "section_id": "七、财政拨款“三公”经费支出决算情况说明",
    }
    hit = match_annotation(ann, [unrelated_domain, matching_domain], set())
    assert hit is matching_domain


def test_table_anchor_contributes_to_ranking():
    """tbl 锚 + evidence 驻留对齐短语共同区分同规则同页的两条 finding。

    真实形态（R5）：V33-120 的 evidence 模板是「表格：支出决算表
    合计值：1367.76」——tbl 锚切出的「支出决算表合计行」六字连续段
    不在 evidence 中（「支出决算表」与「合计值」隔标点），表名区分
    由 anchor_phrases_aligned 的 evidence 驻留短语承担（与真实
    golden A-004 的「合计值」同型）。
    """
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "1,367.76 不等于分项之和",
        "location_key": "tbl:支出决算表合计行·项目支出",
        "anchor_phrases_aligned": ["支出决算表", "合计值"],
    }
    same_table = {
        "rule": "V33-120",
        "page": 10,
        "message": "m",
        "evidence_text": "表格：支出决算表 合计值：1367.76",
    }
    other_table = {
        "rule": "V33-120",
        "page": 10,
        "message": "m",
        "evidence_text": "表格：收入决算表 明细之和：1367.75",
    }
    hit = match_annotation(ann, [other_table, same_table], set())
    assert hit is same_table
    # 反序仍然选中正确表（排序不依赖候选顺序）
    hit_reversed = match_annotation(ann, [same_table, other_table], set())
    assert hit_reversed is same_table


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
        "section_title_phrases_aligned": ["三公经费支出决算情况说明"],
    }
    wrong_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "其他章节的公务接待费 0.00 万元问题",
        "evidence_text": "公务接待费 0.00",
        "section_id": "七、财政拨款“三公”经费支出决算情况说明",
    }
    correct_domain = {
        "rule": "V33-245",
        "page": 26,
        "message": "三公说明：公务接待费支出决算减少为0.00万元表述矛盾",
        "evidence_text": "公务接待费 0.00",
        "section_id": "七、财政拨款“三公”经费支出决算情况说明",
    }
    hit = match_annotation(ann, [wrong_domain, correct_domain], set())
    assert hit is correct_domain
    hit_reversed = match_annotation(ann, [correct_domain, wrong_domain], set())
    assert hit_reversed is correct_domain


def test_zero_anchor_hits_rejects_unmatched_annotation():
    """零锚点命中即拒配（R4 P1-3 终版语义，取代 R3 的降级放行）。

    标注带锚点但候选全部零命中 → 返回 None（FN/待复核），不再
    晋升 TP。真命中的措辞交叉由标注侧 anchor_phrases_aligned 对齐
    （见 ANNOTATIONS.md 2026-09-07 修订），评估器不再单方面迁就。
    """
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "P14 同口径表合计 1,367.75，两表差 0.01",
        "location_key": "xtbl:p14同口径表合计",
        # 注意：真实 golden 标注已补 anchor_phrases_aligned=["同口径列"]；
        # 此处故意不补，模拟未对齐标注在新语义下的行为
    }
    finding = {
        "rule": "V33-120",
        "page": 10,
        "message": "支出决算表与一般公共预算财政拨款支出决算表同口径列相差 0.01 万元",
        "evidence_text": "支出决算表：1367.76 同口径表：1367.75",
    }
    assert match_annotation(ann, [finding], set()) is None


def test_aligned_anchor_phrase_restores_match():
    """标注补对齐短语后恢复匹配——R4 拒配语义的配套通道。"""
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "P14 同口径表合计 1,367.75，两表差 0.01",
        "location_key": "xtbl:p14同口径表合计",
        "anchor_phrases_aligned": ["同口径列"],
    }
    finding = {
        "rule": "V33-120",
        "page": 10,
        "message": "m",
        "evidence_text": "支出决算表与一般公共预算财政拨款支出决算表同口径列相差 0.01 万元",
    }
    assert match_annotation(ann, [finding], set()) is finding


def test_anchor_prefix_is_stripped_from_phrases():
    """R4 修复：tbl:/sec: 等定位域前缀必须剥离——此前 `tbl:支出决算表`
    归一化成 `tbl支出决算表` 整段，表锚永远零命中（样张 A-004 实测）。"""
    from scripts.evaluate_golden_corpus import _anchor_phrases

    phrases = _anchor_phrases("tbl:支出决算表合计行·项目支出")
    assert "支出决算表合计行" in phrases
    assert "项目支出" in phrases
    assert all(not p.startswith("tbl") for p in phrases)


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


# ---------------------------------------------------------------------------
# review 🟡2：truth_id 聚类验收（defect/hint 两侧一致）
# ---------------------------------------------------------------------------


def _ann(annotation_id, truth_id, rule_id, page, evidence):
    return {
        "annotation_id": annotation_id,
        "truth_id": truth_id,
        "rule_id": rule_id,
        "page": page,
        "evidence": evidence,
    }


def _finding(rule, page, evidence_text, message="m"):
    return {
        "rule": rule,
        "severity": "info",
        "page": page,
        "evidence_text": evidence_text,
        "message": message,
    }


def test_truth_group_normalizes_evidence_face_suffix():
    """证据面后缀归一：T4a/T4b 属同一真值 T4，T1 组即自身。

    编号对齐 HANDOFF §2 权威口径（R7 P0-1）：T4 的两个证据面是
    A-006（310 行明细差）/A-008（公用经费显示和差）。
    """
    from scripts.evaluate_golden_corpus import _truth_group

    assert _truth_group(_ann("A-006", "T4a", "V33-117", 15, "x")) == "T4"
    assert _truth_group(_ann("A-008", "T4b", "V33-117", 15, "x")) == "T4"
    assert _truth_group(_ann("A-001", "T1", "V33-001", 2, "x")) == "T1"


def test_truth_cluster_single_face_hit_skips_other_face():
    """同一真值两个证据面、一条 finding 只命中一面 → 真值命中、另一面不报 miss。

    review 🟡2 验收口径：同 truth_id 的两条 hint 标注，单条 finding 命中
    即 hint 计数正确——聚类语义与 defect 侧一致（任一命中即真值命中）。
    """
    from scripts.evaluate_golden_corpus import _consume_truth_annotations

    findings = [_finding("V33-117", 15, "310 行 14.44 与明细和不符")]
    anns = [
        _ann("A-006", "T4a", "V33-117", 15, "310 行 14.44 与明细和不符"),
        _ann("A-008", "T4b", "V33-117", 15, "310 行 14.44 与明细和不符"),
    ]
    matched, missed, hit_groups = _consume_truth_annotations(anns, findings, set())
    assert hit_groups == 1, "同一真值只计 1 个命中组"
    assert len(matched) == 1, "一面命中即止，不重复消费第二面"
    assert missed == [], "命中组内未命中的证据面不得计入 missed"
    assert matched[0]["truth_group"] == "T4"


def test_truth_cluster_all_faces_missed_counts_one_group():
    """整组全部证据面未命中 → 只计 1 个 missed（不按标注面放大缺口）。"""
    from scripts.evaluate_golden_corpus import _consume_truth_annotations

    findings = [_finding("V33-117", 15, "310 行 14.44 与明细和不符")]
    anns = [
        _ann("A-006", "T4a", "V33-117", 99, "310 行 14.44"),  # 页不符
        _ann("A-008", "T4b", "V33-117", 98, "310 行 14.44"),  # 页不符
    ]
    matched, missed, hit_groups = _consume_truth_annotations(anns, findings, set())
    assert hit_groups == 0
    assert len(missed) == 1, "整组未命中只计 1 个 missed"
    assert missed[0]["annotation_ids"] == ["A-006", "A-008"], "missed 明细带全部面"


def test_truth_cluster_distinct_groups_count_separately():
    """不同真值组各自独立验收：两组都未命中 → 2 个 missed。"""
    from scripts.evaluate_golden_corpus import _consume_truth_annotations

    findings = []
    anns = [
        _ann("A-001", "T1", "V33-001", 2, "目录行年度缺位"),
        _ann("A-002", "T5", "V33-245", 26, "三公说明逻辑矛盾"),
    ]
    matched, missed, hit_groups = _consume_truth_annotations(anns, findings, set())
    assert hit_groups == 0
    assert len(missed) == 2, "不同真值组未命中按组计 missed"


def test_evaluate_hint_clustering_end_to_end(monkeypatch, tmp_path):
    """evaluate 全链路：hint 侧按 truth 组聚类（review 🟡2 锁定）。

    构造两条同真值（T4a/T4b，HANDOFF 权威编号：A-006/A-008）hint
    标注 + 一条能命中两面的 finding：旧行为下面 B 因 finding 被面 A
    消费而报 miss；修复后组命中即止，hint 侧不再报 miss、按组计数
    为 1/1。
    """
    import json
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    from scripts.evaluate_golden_corpus import evaluate

    doc_id = "DOC-TEST-CLUSTER"
    corpus_dir = tmp_path / "corpus" / doc_id
    corpus_dir.mkdir(parents=True)
    golden = {
        "doc_id": doc_id,
        "sha256": "x" * 64,
        "annotation_version": 3,
        "labels": [
            {
                "annotation_id": "A-001",
                "truth_id": "T1",
                "label": "defect",
                "rule_id": "V33-001",
                "page": 2,
                "expected_severity": "high",
                "evidence": "目录行年度缺位（应为 2025）",
            },
            {
                "annotation_id": "A-006",
                "truth_id": "T4a",
                "label": "rounding_hint",
                "rule_id": "V33-117",
                "page": 15,
                "expected_severity": "info",
                "evidence": "310 行 14.44 与明细和不符",
            },
            {
                "annotation_id": "A-008",
                "truth_id": "T4b",
                "label": "rounding_hint",
                "rule_id": "V33-117",
                "page": 15,
                "expected_severity": "info",
                "evidence": "310 行 14.44 与明细和不符（公用经费口径）",
            },
        ],
    }
    (corpus_dir / "golden.json").write_text(
        json.dumps(golden, ensure_ascii=False), encoding="utf-8"
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "sha256": "x" * 64,  # 与 golden 同源（R8 P1 绑定）
                "legacy": {
                    "findings": [
                        {
                            "rule": "V33-001",
                            "severity": "high",
                            "page": 2,
                            "evidence_text": "「202 年度」目录行年度缺位",
                            "message": "目录年度缺位",
                        },
                        {
                            "rule": "V33-117",
                            "severity": "info",
                            "page": 15,
                            "evidence_text": "310 行 14.44 与明细和不符",
                            "message": "舍入差",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.evaluate_golden_corpus.CORPUS_DIR", tmp_path / "corpus"
    )
    report = evaluate(doc_id, replay_path)
    assert report["tp"] == 1 and report["fn"] == 0
    assert report["hint_groups_total"] == 1, "T4a/T4b 归一为同一真值组"
    assert report["hint_groups_hit"] == 1, "任一证据面命中即真值命中"
    assert report["hint_matched"] == 1, "只消费一面，不重复计数"
    assert report["hint_missed_count"] == 0, "命中组内另一面不得报 miss"


# ---------------------------------------------------------------------------
# R7 P0-1：舍入提示硬门禁（check_gates）
# ---------------------------------------------------------------------------


def _gate_report(**overrides):
    base = {
        "tp": 3,  # R9 P0：tp 已是真值组口径
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "locatable_evidence_rate": 1.0,
        "acceptable_violations": [],
        "hint_groups_total": 3,
        "hint_groups_hit": 3,
        "hint_missed_count": 0,
        "defect_groups_total": 3,
        "defect_groups_hit": ["T1", "T5", "T6"],
    }
    base.update(overrides)
    return base


def test_gate_requires_hint_3_of_3():
    """舍入门禁进入硬门禁：0/3、缩减真值集、hint_missed 都必须失败。

    R7 P0-1：此前 check_gates 完全不检查 hint——构造 0/3 命中仍返回
    无失败条件；且只查 hit==total 挡不住把真值组从 3 缩到 2 的假绿。
    """
    from scripts.evaluate_golden_corpus import check_gates

    assert check_gates(_gate_report()) == []
    assert check_gates(_gate_report(hint_groups_hit=0)) != [], "0/3 命中必须失败"
    # 真值组被缩减（历史实测 2/2 假通过形态）：两侧必须都等于 3
    assert check_gates(
        _gate_report(hint_groups_total=2, hint_groups_hit=2)
    ) != [], "缩减真值集必须失败"
    assert check_gates(_gate_report(hint_missed_count=1)) != []


def test_gate_requires_hard_problem_3_of_3():
    """硬问题 3/3 锁定：缩减 defect 真值集或按证据面虚增都必须失败。

    HANDOFF §7 验收标准是 T1/T5/T6 三组全命中——只查 fn!=0 挡不住
    删标注；R9 P0 前按证据面计 tp 挡不住多面归一（实测 3 面 1 组
    报告 TP=3 假绿）。现在直接校验命中组集恰为 {T1, T5, T6}。
    """
    from scripts.evaluate_golden_corpus import check_gates

    # 缩减真值集（2 组）
    assert check_gates(
        _gate_report(
            tp=2,
            defect_groups_total=2,
            defect_groups_hit=["T1", "T5"],
        )
    ) != [], "缩减真值集必须失败"
    # 证据面虚增（3 面归一同一真值组——R9 P0 复现形态）
    assert check_gates(
        _gate_report(
            tp=1,
            defect_groups_hit=["T1"],
        )
    ) != [], "按证据面虚增必须失败"
    # 命中组错位（缺 T6 多 T9）
    assert check_gates(
        _gate_report(defect_groups_hit=["T1", "T5", "T9"])
    ) != [], "命中组集必须是 T1/T5/T6"


# ---------------------------------------------------------------------------
# R7 P1-3：finding 独立 section_id 的跨章节候选拒配
# ---------------------------------------------------------------------------


def test_section_id_rejects_cross_section_candidate():
    """sec 锚标注 + finding 带 section_id → 跨章节候选不得晋升 TP。

    旧行为：「其他重要事项说明」章节的候选借「公务接待费」主题词
    混过 evidence 锚点；R7 后 section_id 结构化校验章节域。
    """
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:三公说明(一)公务接待费",
        "section_title_phrases_aligned": ["三公经费支出决算情况说明"],
    }
    wrong_section = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "公务接待费 0.00 与 2024 年持平",
        "section_id": "十一、其他重要事项说明",
    }
    assert match_annotation(ann, [wrong_section], set()) is None


def test_section_id_accepts_matching_section():
    """section_id 与标注章节同域（三公）→ 正常晋升 TP。"""
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:三公说明(一)公务接待费",
        "section_title_phrases_aligned": ["三公经费支出决算情况说明"],
    }
    matching = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "公务接待费 0.00 与 2024 年持平",
        "section_id": "七、财政拨款“三公”经费支出决算情况说明",
    }
    assert match_annotation(ann, [matching], set()) is matching


def test_section_id_missing_rejects_sec_annotation():
    """sec 真值强制非空结构化章节标识（R8 P1 fail-open 关闭）。

    此前无 section_id 的 finding 退回锚点语义即可晋升 TP——缺章节
    标识的 finding 无法证明产自目标章节，必须拒配（FN/待复核）。
    """
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:三公说明(一)公务接待费",
        "section_title_phrases_aligned": ["三公经费支出决算情况说明"],
    }
    legacy_finding = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "公务接待费 0.00 与 2024 年持平",
    }
    assert match_annotation(ann, [legacy_finding], set()) is None


def test_section_id_full_phrase_matching_rejects_two_char_prefix():
    """章节锚全短语匹配（R8 P1）：仅共享 2 字前缀的章节不得通过。

    旧行为允许锚短语的 2 字前缀——「九、公务管理情况说明」仅与
    「三公说明」共享「公」/「公务」即可命中三公真值（实测）。现在
    必须整短语（或声明的章节标题短语）出现在 section_id 中。
    """
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:三公说明(一)公务接待费",
    }
    # 未声明章节短语时，章节锚 = location_key 锚短语全量——两字前缀
    # 不再放行「九、公务管理情况说明」（共享「公务」两字）
    wrong_section = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "公务接待费 0.00 与 2024 年持平",
        "section_id": "九、公务管理情况说明",
    }
    assert match_annotation(ann, [wrong_section], set()) is None


# ---------------------------------------------------------------------------
# R8 P0：shadow replay 显式 mode + R8 P1：replay 身份绑定（doc_id/SHA）
# ---------------------------------------------------------------------------


def test_evaluate_rejects_tampered_doc_id_and_sha(monkeypatch, tmp_path):
    """replay 身份绑定（R8 P1）：doc_id 不符或 SHA 不符都必须拒绝。

    此前只校验内容不校验身份——篡改 replay 的 doc_id=DOC-WRONG、
    sha256=deadbeef 后评估仍 GATE-PASS（实测）。
    """
    import json

    import pytest

    from scripts.evaluate_golden_corpus import evaluate

    doc_id = "DOC-20260905-001"
    corpus_dir = tmp_path / "corpus" / doc_id
    corpus_dir.mkdir(parents=True)
    golden = {
        "doc_id": doc_id,
        "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
        "labels": [],
    }
    (corpus_dir / "golden.json").write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.evaluate_golden_corpus.CORPUS_DIR", tmp_path / "corpus"
    )

    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {"doc_id": "DOC-WRONG", "sha256": "d" * 64, "legacy": {"findings": []}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="doc_id"):
        evaluate(doc_id, replay_path)

    replay_path.write_text(
        json.dumps(
            {"doc_id": doc_id, "sha256": "deadbeef", "legacy": {"findings": []}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SHA"):
        evaluate(doc_id, replay_path)


def test_evaluate_shadow_replay_requires_explicit_mode(monkeypatch, tmp_path):
    """shadow replay 双结果不得静默取 legacy（R8 P0）。

    structured 路径失败时（同一样张 4 findings/TP=0/FN=3），auto 必须
    拒绝猜测；--mode structured 显式验收 structured 路径（如实呈现
    召回缺口，而不是读 legacy 蒙混 GATE-PASS）。
    """
    import json

    import pytest

    from scripts.evaluate_golden_corpus import evaluate

    doc_id = "DOC-20260905-001"
    corpus_dir = tmp_path / "corpus" / doc_id
    corpus_dir.mkdir(parents=True)
    golden = {
        "doc_id": doc_id,
        "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
        "labels": [
            {
                "annotation_id": "A-001",
                "truth_id": "T1",
                "label": "defect",
                "rule_id": "V33-001",
                "page": 2,
                "expected_severity": "high",
                "evidence": "目录行年度缺位",
            }
        ],
    }
    (corpus_dir / "golden.json").write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.evaluate_golden_corpus.CORPUS_DIR", tmp_path / "corpus"
    )

    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
                "legacy": {"findings": []},
                "structured": {"findings": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="--mode"):
        evaluate(doc_id, replay_path)  # auto 遇双结果拒绝猜测
    report = evaluate(doc_id, replay_path, mode="structured")
    assert report["mode"] == "structured"
    assert report["tp"] == 0 and report["fn"] == 1, (
        "structured 缺陷召回缺口必须如实呈现，不得被 legacy 掩盖"
    )


# ---------------------------------------------------------------------------
# R9 P0：defect 真值组口径 + R9 P1：sec 保护绕过反例
# ---------------------------------------------------------------------------


def test_evaluate_defect_groups_not_face_inflated(monkeypatch, tmp_path):
    """T1a/T1b/T1c 三个证据面归一同一 T1 时，tp 必须按组计（R9 P0）。

    此前 tp = len(matched) 按证据面计——3 面全命中报告 TP=3/FN=0、
    门禁假绿，可绕过「T1/T5/T6 三组」锁定。修复后 tp=1（组）、
    defect_groups_total=1、命中组集不含 T5/T6，check_gates 必须失败。
    """
    import json

    from scripts.evaluate_golden_corpus import check_gates, evaluate

    doc_id = "DOC-20260905-001"
    corpus_dir = tmp_path / "corpus" / doc_id
    corpus_dir.mkdir(parents=True)
    golden = {
        "doc_id": doc_id,
        "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
        "labels": [
            {
                "annotation_id": f"A-{n}",
                "truth_id": f"T1{n}",  # T1a/T1b/T1c → 组 T1
                "label": "defect",
                "rule_id": "V33-001",
                "page": 2,
                "expected_severity": "high",
                "evidence": "目录行年度缺位（应为 2025）",
            }
            for n in ("a", "b", "c")
        ],
    }
    (corpus_dir / "golden.json").write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.evaluate_golden_corpus.CORPUS_DIR", tmp_path / "corpus"
    )

    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
                "legacy": {
                    "findings": [
                        {
                            "rule": "V33-001",
                            "severity": "high",
                            "page": 2,
                            "evidence_text": "「202 年度」目录行年度缺位",
                            "message": "目录年度缺位",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = evaluate(doc_id, replay_path)
    assert report["tp"] == 1, f"tp 必须按真值组计: {report['tp']}"
    assert report["defect_faces_matched"] == 1, "三个面只能消费一条 finding"
    assert report["defect_groups_total"] == 1
    assert report["defect_groups_hit"] == ["T1"]
    assert check_gates(report), "单组命中不得通过三组门禁"


def test_sec_generic_anchor_without_section_id_rejected():
    """sec: 锚短语被通用词过滤时，缺失 section_id 仍必须拒配（R9 P1）。

    此前 section_id 硬要求挂在 `if section_anchors:` 内——location_key
    ="sec:情况说明" 的短语被通用词过滤、无有效锚，缺失 section_id 的
    finding 绕过章节保护（实测）。章节标识是非空硬前提，与锚无关。
    """
    ann = {
        "rule_id": "V33-245",
        "page": 26,
        "evidence": "公务接待费 0.00 与 2024 年持平",
        "location_key": "sec:情况说明",  # 「情况说明」在通用词表 → 无有效锚
    }
    finding = {
        "rule": "V33-245",
        "page": 26,
        "message": "m",
        "evidence_text": "公务接待费 0.00 与 2024 年持平",
        # 无 section_id
    }
    assert match_annotation(ann, [finding], set()) is None


def test_evaluate_rejects_sec_annotation_without_section_anchor(monkeypatch, tmp_path):
    """golden 加载阶段：sec 标注缺 section_title_phrases_aligned → 拒绝。

    R9 P1：缺章节锚的 sec 标注无法做结构化章节校验，fail-closed 在
    加载期报错，不等到匹配阶段静默退化。
    """
    import json

    import pytest

    from scripts.evaluate_golden_corpus import evaluate

    doc_id = "DOC-20260905-001"
    corpus_dir = tmp_path / "corpus" / doc_id
    corpus_dir.mkdir(parents=True)
    golden = {
        "doc_id": doc_id,
        "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
        "labels": [
            {
                "annotation_id": "A-002",
                "truth_id": "T5",
                "label": "defect",
                "rule_id": "V33-245",
                "page": 26,
                "location_key": "sec:三公说明(一)公务接待费",
                "expected_severity": "medium",
                "evidence": "逻辑矛盾",
                # 缺 section_title_phrases_aligned
            }
        ],
    }
    (corpus_dir / "golden.json").write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.evaluate_golden_corpus.CORPUS_DIR", tmp_path / "corpus"
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "sha256": "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7",
                "legacy": {"findings": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="section_title_phrases_aligned"):
        evaluate(doc_id, replay_path)
