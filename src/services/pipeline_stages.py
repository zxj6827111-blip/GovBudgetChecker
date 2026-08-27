"""Per-job 流水线阶段枚举与进度派生（Task 3）。

背景
----
`api/main.py` 的 `_run_pipeline_body` 一直会在每次 `_safe_write` 时带上一个中文自由文本
`stage` 字段（如"解析PDF内容""执行规则检查"）与一个 0-100 的整数 `progress`，
但这两个字段只是**流水线内部细粒度阶段名**，从未被归约成一个稳定的、供前端展示的
有序阶段模型——前端要展示"当前处于 5 个阶段中的哪一个 + 该阶段完成度"，
不能直接把这些细粒度中文字符串甩给用户（原型图画的是"元数据识别 · 54%"这种
粗粒度阶段，不是"开始抽取""结果转换"这种实现细节）。

本模块只做一件事：把已有的自由文本 stage 映射到一个稳定的 5 态有序枚举，
**不改变** `_run_pipeline_body` 里任何一处 `_safe_write` 调用的现有 stage 字符串取值
（那些字符串已经被 `tests/test_structured_logging.py`、`app/app/components/PipelineStatus.tsx`
等既有调用方通过日志/展示逻辑间接依赖，改字符串本身有回归既有功能的风险，
本轮任务范围是"补齐缺失的阶段进度模型"，不是"重新设计现有阶段日志"）。

阶段枚举（对照任务书，5 态有序，不含 OCR）
------------------------------------------
上传 -> PDF 解析 -> 元数据识别 -> 规则与 AI 分析 -> 质量门禁

OCR 阶段本轮不存在，枚举中不得出现：原型图画了 OCR 阶段，但 OCR 只做检测、
未做自动识别（`docs/RELEASE_ACCEPTANCE_2026-08-27.md` 第 6 节），凭空造一个
"OCR 阶段"会让用户误以为系统真的在做 OCR 识别，属于制造虚假信息。

严禁伪造进度
------------
`resolve_stage_progress()` 遇到无法识别的 stage 文本时返回 `percent=None`，
前端据此显示"—"而不是猜一个数字。这不是"偶尔失手漏了几个 case"的兜底，
是本模块的设计前提：宁可"不知道"，不可"编一个看起来合理的数字"。
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple, Optional


class PipelineStage(str, Enum):
    """有序 5 态流水线阶段（枚举定义顺序即业务顺序，供前端渲染阶段条时排序用）。"""

    UPLOAD = "upload"
    PDF_PARSE = "pdf_parse"
    METADATA_RECOGNITION = "metadata_recognition"
    RULE_AI_ANALYSIS = "rule_ai_analysis"
    QUALITY_GATE = "quality_gate"


#: 阶段的中文展示名，与阶段枚举顺序一一对应。
PIPELINE_STAGE_LABELS: dict[PipelineStage, str] = {
    PipelineStage.UPLOAD: "上传",
    PipelineStage.PDF_PARSE: "PDF 解析",
    PipelineStage.METADATA_RECOGNITION: "元数据识别",
    PipelineStage.RULE_AI_ANALYSIS: "规则与 AI 分析",
    PipelineStage.QUALITY_GATE: "质量门禁",
}

#: 阶段顺序索引（0-based），供"某阶段是否已经过去"之类的比较使用。
PIPELINE_STAGE_ORDER: dict[PipelineStage, int] = {
    stage: index for index, stage in enumerate(PipelineStage)
}


class _StageRange(NamedTuple):
    """一条既有自由文本 stage 归属的规范阶段，以及它在该阶段内的进度区间。

    `progress_at_start` / `progress_at_end` 取自 `_safe_write` 调用点里实际写入的
    pipeline 全局 progress 整数（例如"解析PDF内容"对应 progress=15，
    紧接着的"构建文档对象"对应 progress=25，因此"解析PDF内容"在 PDF_PARSE 阶段内
    的进度区间是 [15, 25)）。这里不是重新发明一套百分比，只是把已有的
    pipeline 全局进度重新表达成"阶段内进度"，两者可以互相换算，不产生新事实。
    """

    stage: PipelineStage
    progress_at_start: int
    progress_at_end: int


#: 既有自由文本 stage -> 规范阶段的映射表。
#:
#: 数据来源：`api/main.py` 的 `_run_pipeline_body` 里全部 `_safe_write` 调用点
#: 实际写入的 `stage` 字符串与对应 `progress` 整数（逐条核对，非猜测）。
#: 键统一去除"（xxx回退）"括注后再比较，见 `_strip_retry_suffix()`。
_STAGE_TEXT_TO_RANGE: dict[str, _StageRange] = {
    "开始解析文档": _StageRange(PipelineStage.UPLOAD, 0, 15),
    "解析PDF内容": _StageRange(PipelineStage.PDF_PARSE, 15, 25),
    "构建文档对象": _StageRange(PipelineStage.PDF_PARSE, 25, 35),
    "双模式分析": _StageRange(PipelineStage.RULE_AI_ANALYSIS, 35, 95),
    "AI辅助状态": _StageRange(PipelineStage.METADATA_RECOGNITION, 35, 50),
    "开始抽取": _StageRange(PipelineStage.METADATA_RECOGNITION, 50, 80),
    "抽取完成": _StageRange(PipelineStage.METADATA_RECOGNITION, 80, 90),
    "结果转换": _StageRange(PipelineStage.RULE_AI_ANALYSIS, 90, 95),
    "执行规则检查": _StageRange(PipelineStage.RULE_AI_ANALYSIS, 95, 98),
    "结构化入库": _StageRange(PipelineStage.QUALITY_GATE, 98, 100),
    "完成": _StageRange(PipelineStage.QUALITY_GATE, 100, 100),
    "完成（需人工复核）": _StageRange(PipelineStage.QUALITY_GATE, 100, 100),
    "完成（部分能力降级）": _StageRange(PipelineStage.QUALITY_GATE, 100, 100),
}


def _strip_retry_suffix(stage_text: str) -> str:
    """去掉"执行规则检查（超时回退）"/"执行规则检查（异常回退）"之类的括注后缀。

    这两个变体在语义上仍属于"规则与 AI 分析"阶段（只是走了失败回退路径），
    不需要单独的映射条目，去掉后缀即可复用主条目的区间。
    """
    if "（" in stage_text and stage_text.endswith("）"):
        return stage_text.split("（", 1)[0]
    return stage_text


class StageProgress(NamedTuple):
    """解析结果：规范阶段 + 阶段内完成度百分比 + 原始自由文本 stage。"""

    phase: Optional[PipelineStage]
    phase_label: Optional[str]
    percent: Optional[int]
    raw_stage: Optional[str]


def resolve_stage_progress(stage_text: Optional[str]) -> StageProgress:
    """把既有自由文本 stage 解析成"规范阶段 + 阶段内完成度"。

    反例（严禁伪造进度）：`stage_text` 为空、为 None，或不在映射表内（例如历史
    任务留下的未知字符串、未来新增但没同步更新本映射表的阶段）时，
    `percent` 与 `phase` 均返回 None——前端必须显示"—"，不能用 0 或任何猜测值填补。
    """
    if not stage_text:
        return StageProgress(phase=None, phase_label=None, percent=None, raw_stage=stage_text)

    normalized = _strip_retry_suffix(stage_text.strip())
    stage_range = _STAGE_TEXT_TO_RANGE.get(normalized)
    if stage_range is None:
        return StageProgress(phase=None, phase_label=None, percent=None, raw_stage=stage_text)

    # 阶段内进度：如果该阶段起止进度相同（如终态"完成"区间是 [100,100)），
    # 直接给 100，避免除零；否则取阶段起点作为"刚进入该阶段"的合理估计
    # ——这是"阶段内进度"而非"距离阶段结束还有多远"，进入阶段即视为该阶段
    # 已经开始推进，用起点值不会比区间终点更"虚张声势"。
    if stage_range.progress_at_end <= stage_range.progress_at_start:
        percent = 100
    else:
        percent = stage_range.progress_at_start

    return StageProgress(
        phase=stage_range.stage,
        phase_label=PIPELINE_STAGE_LABELS[stage_range.stage],
        percent=percent,
        raw_stage=stage_text,
    )


def stage_progress_to_dict(result: StageProgress) -> dict[str, Optional[object]]:
    """把 `StageProgress` 转成可直接塞进 `status.json` / API 响应的字典。"""
    return {
        "phase": result.phase.value if result.phase is not None else None,
        "phase_label": result.phase_label,
        "percent": result.percent,
        "raw_stage": result.raw_stage,
    }
