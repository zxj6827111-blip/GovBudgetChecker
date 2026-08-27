"""src/services/pipeline_stages.py 的单测（Task 3）。

覆盖点（对照任务书要求）：
- 阶段推进顺序正确（既有中文 stage 文本按预期归入 5 个规范阶段之一，
  且规范阶段的枚举顺序确实是"上传 -> PDF 解析 -> 元数据识别 -> 规则与 AI 分析 -> 质量门禁"）；
- OCR 阶段不得出现在枚举中（反例）；
- null 语义反例：未知/空 stage 文本必须返回 percent=None，不得返回 0 或任何猜测值。
"""

from __future__ import annotations

import pytest

from src.services.pipeline_stages import (
    PIPELINE_STAGE_LABELS,
    PIPELINE_STAGE_ORDER,
    PipelineStage,
    resolve_stage_progress,
    stage_progress_to_dict,
)


# ---------------------------------------------------------------------------
# 阶段枚举结构性检查
# ---------------------------------------------------------------------------


def test_pipeline_stage_has_exactly_five_ordered_phases_without_ocr():
    """反例：OCR 阶段本轮不存在，枚举中不得出现（决策 1=b）。"""
    stage_values = [stage.value for stage in PipelineStage]
    assert len(stage_values) == 5, "阶段枚举必须恰好是 5 态，不多不少"
    assert "ocr" not in stage_values
    assert not any("ocr" in value.lower() for value in stage_values), (
        "REGRESSION: OCR 阶段本轮未实现，凭空造出这个阶段会让用户误以为系统在做 OCR 识别"
    )


def test_pipeline_stage_order_matches_business_sequence():
    """枚举定义顺序必须是"上传 -> PDF 解析 -> 元数据识别 -> 规则与 AI 分析 -> 质量门禁"。"""
    ordered_stages = list(PipelineStage)
    assert ordered_stages == [
        PipelineStage.UPLOAD,
        PipelineStage.PDF_PARSE,
        PipelineStage.METADATA_RECOGNITION,
        PipelineStage.RULE_AI_ANALYSIS,
        PipelineStage.QUALITY_GATE,
    ]
    # 顺序索引必须严格递增，供前端渲染阶段条时判断"是否已经过去"
    assert PIPELINE_STAGE_ORDER[PipelineStage.UPLOAD] < PIPELINE_STAGE_ORDER[PipelineStage.PDF_PARSE]
    assert (
        PIPELINE_STAGE_ORDER[PipelineStage.PDF_PARSE]
        < PIPELINE_STAGE_ORDER[PipelineStage.METADATA_RECOGNITION]
    )
    assert (
        PIPELINE_STAGE_ORDER[PipelineStage.METADATA_RECOGNITION]
        < PIPELINE_STAGE_ORDER[PipelineStage.RULE_AI_ANALYSIS]
    )
    assert (
        PIPELINE_STAGE_ORDER[PipelineStage.RULE_AI_ANALYSIS]
        < PIPELINE_STAGE_ORDER[PipelineStage.QUALITY_GATE]
    )


def test_every_stage_has_a_chinese_label():
    for stage in PipelineStage:
        assert PIPELINE_STAGE_LABELS[stage], f"{stage} 缺少中文展示名"


# ---------------------------------------------------------------------------
# resolve_stage_progress：既有自由文本 stage -> 规范阶段的映射正确性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage_text", "expected_phase"),
    [
        ("开始解析文档", PipelineStage.UPLOAD),
        ("解析PDF内容", PipelineStage.PDF_PARSE),
        ("构建文档对象", PipelineStage.PDF_PARSE),
        ("双模式分析", PipelineStage.RULE_AI_ANALYSIS),
        ("AI辅助状态", PipelineStage.METADATA_RECOGNITION),
        ("开始抽取", PipelineStage.METADATA_RECOGNITION),
        ("抽取完成", PipelineStage.METADATA_RECOGNITION),
        ("结果转换", PipelineStage.RULE_AI_ANALYSIS),
        ("执行规则检查", PipelineStage.RULE_AI_ANALYSIS),
        ("结构化入库", PipelineStage.QUALITY_GATE),
        ("完成", PipelineStage.QUALITY_GATE),
        ("完成（需人工复核）", PipelineStage.QUALITY_GATE),
        ("完成（部分能力降级）", PipelineStage.QUALITY_GATE),
    ],
)
def test_resolve_stage_progress_maps_known_stage_text_to_expected_phase(
    stage_text: str, expected_phase: PipelineStage
):
    result = resolve_stage_progress(stage_text)
    assert result.phase == expected_phase, f"{stage_text!r} 应归入 {expected_phase}"
    assert result.percent is not None, f"{stage_text!r} 是已知阶段，不应返回 percent=None"
    assert 0 <= result.percent <= 100


def test_resolve_stage_progress_strips_retry_suffix_variants():
    """"执行规则检查（超时回退）"/"执行规则检查（异常回退）"必须归入同一阶段，
    不因为回退变体而被判定成未知 stage。"""
    base = resolve_stage_progress("执行规则检查")
    timeout_variant = resolve_stage_progress("执行规则检查（超时回退）")
    error_variant = resolve_stage_progress("执行规则检查（异常回退）")

    assert timeout_variant.phase == base.phase == PipelineStage.RULE_AI_ANALYSIS
    assert error_variant.phase == base.phase
    assert timeout_variant.percent == base.percent
    assert error_variant.percent == base.percent


def test_resolve_stage_progress_phase_progression_is_monotonic_for_the_happy_path():
    """阶段推进顺序正确性：沿正常流水线顺序解析各阶段文本，规范阶段的顺序索引
    必须单调不减（同一阶段内可以停留在同一个索引，但不能后退）。"""
    happy_path_stage_texts = [
        "开始解析文档",
        "解析PDF内容",
        "构建文档对象",
        "AI辅助状态",
        "开始抽取",
        "抽取完成",
        "结果转换",
        "执行规则检查",
        "结构化入库",
        "完成",
    ]
    previous_order = -1
    for stage_text in happy_path_stage_texts:
        result = resolve_stage_progress(stage_text)
        assert result.phase is not None
        current_order = PIPELINE_STAGE_ORDER[result.phase]
        assert current_order >= previous_order, (
            f"REGRESSION: {stage_text!r} 解析出的阶段顺序倒退了"
            f"（{current_order} < {previous_order}），流水线不应该往回走"
        )
        previous_order = current_order


# ---------------------------------------------------------------------------
# null 语义反例：未知/空 stage 文本绝不能返回 0 或猜测值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty_stage_text", [None, "", "   "])
def test_resolve_stage_progress_returns_none_for_empty_input(empty_stage_text):
    result = resolve_stage_progress(empty_stage_text)
    assert result.phase is None
    assert result.percent is None, (
        "REGRESSION: 空 stage 文本必须返回 percent=None，不得返回 0 或任何猜测值"
    )
    assert result.phase_label is None


def test_resolve_stage_progress_returns_none_for_unknown_stage_text():
    """反例：未在映射表内的陌生字符串（例如历史遗留的、或未来新增但没同步
    更新映射表的阶段名）必须返回 None，不得靠字符串相似度瞎猜一个阶段。"""
    result = resolve_stage_progress("某个从未见过的神秘阶段名称")
    assert result.phase is None
    assert result.percent is None
    assert result.raw_stage == "某个从未见过的神秘阶段名称", "原始文本仍应保留，供排障参考"


def test_resolve_stage_progress_unknown_percent_is_never_zero_or_falsy_confused_with_none():
    """再次强调 0 与 None 的区别：这里断言未知态返回的确实是 Python 的 None 对象，
    不是恰好等于 0 的整数（虽然两者在某些弱类型判断下都会被认为"假"）。"""
    result = resolve_stage_progress("未知阶段")
    assert result.percent is None
    assert result.percent != 0  # 显式区分：None 不是 0，即使 `not 0` 与 `not None` 都是 True


# ---------------------------------------------------------------------------
# stage_progress_to_dict：序列化正确性
# ---------------------------------------------------------------------------


def test_stage_progress_to_dict_serializes_known_stage():
    result = resolve_stage_progress("执行规则检查")
    payload = stage_progress_to_dict(result)
    assert payload == {
        "phase": "rule_ai_analysis",
        "phase_label": "规则与 AI 分析",
        "percent": 95,
        "raw_stage": "执行规则检查",
    }


def test_stage_progress_to_dict_serializes_unknown_stage_with_null_fields():
    """反例：未知阶段序列化后 phase/percent 必须是 JSON null，不是字符串 "None" 或 0。"""
    result = resolve_stage_progress(None)
    payload = stage_progress_to_dict(result)
    assert payload["phase"] is None
    assert payload["percent"] is None
    assert payload["phase_label"] is None
