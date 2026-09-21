"""槽位状态机：材料"到哪一步了"的唯一权威推导。

为什么必须是纯函数
------------------
状态出现在三处用途里：数据库里的缓存列、台账列表的聚合、材料详情页的展示。
如果三处各自推导，就一定会出现"列表说缺材料、详情说已上传"这类互相矛盾的说法，
而矛盾一旦出现，用户就再也不能相信任何一个。

因此**推导只在本模块发生**，输入是事实，输出是 (状态, 成因码) 二元组；
数据库里的 ``status`` 只是这个函数结果的缓存。

两条最容易搞错的口径
--------------------
1. **"没有 PDF" ≠ "缺失"。** 缺失的完整条件是
   ``已到期 AND 适用 AND 没有当前文件``。截止时间未知时，无法证明逾期，
   只能停在"未到期"并注明成因是"截止时间未知"——把未知当已知是最典型的
   虚假结论。
2. **"年份未识别"必须停在 mapping_required。** 一份年份没认出来的材料，
   其身份（哪一年、属于哪条应收）尚未确认，任何下游结论都不可信。
   本项目历史上正是用"兜底成 2000 年"把这类问题藏了起来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.schemas.material_slot import (
    STATUS_REASON_ANALYSIS_FAILED,
    STATUS_REASON_ANALYSIS_RUNNING,
    STATUS_REASON_APPLICABILITY_MARKED,
    STATUS_REASON_APPLICABILITY_UNRESOLVED,
    STATUS_REASON_AWAITING_ANALYSIS,
    STATUS_REASON_CALIBER_CONFLICT,
    STATUS_REASON_DUE_EXCEEDED,
    STATUS_REASON_DUE_NOT_REACHED,
    STATUS_REASON_DUE_UNKNOWN,
    STATUS_REASON_FINDINGS_PENDING,
    STATUS_REASON_IDENTITY_UNRESOLVED,
    STATUS_REASON_REVIEW_DONE,
    STATUS_REASON_REVIEW_IN_PROGRESS,
)

#: 分析运行状态（由处理队列侧提供）。WP1 只定义取值域，接线在 WP2/WP3。
ANALYSIS_NOT_STARTED = "not_started"
ANALYSIS_PROCESSING = "processing"
ANALYSIS_DONE = "done"
ANALYSIS_FAILED = "failed"

#: 人工复核状态（由审核生命周期提供）。WP3 之前一律为 none。
REVIEW_NONE = "none"
REVIEW_REQUIRED = "required"
REVIEW_IN_PROGRESS = "in_progress"
REVIEW_COMPLETED = "completed"


@dataclass(frozen=True)
class SlotStatusResult:
    """推导结果。状态与成因必须成对出现。"""

    status: str
    reason: str

    def as_tuple(self) -> tuple:
        return (self.status, self.reason)


def derive_slot_status(
    *,
    applicability_status: str = "applicable",
    due_at: Optional[datetime] = None,
    identity_resolved: bool = True,
    caliber_conflict: bool = False,
    has_current_document: bool = False,
    analysis_state: str = ANALYSIS_NOT_STARTED,
    review_state: str = REVIEW_NONE,
    now: Optional[datetime] = None,
) -> SlotStatusResult:
    """推导槽位状态。

    判定顺序是有意固定的，每一步都对应一类"不能提前下结论"的情形：

    1. 身份未解决 -> ``mapping_required``。身份都没确认，谈上传与否没有意义。
    2. 适用性未确认 -> ``mapping_required``。是否应收都没定，不能计入缺失。
    3. 人工确认不适用 -> ``not_applicable``。
    4. 口径存在未裁决的矛盾 -> ``mapping_required``。已确认的口径与后来识别到的
       口径不一致时，只有人工能裁决；静默取一个会让"两笔数字能不能相加"失去依据。
    5. 已确认适用但没有当前文件 -> 再分到期与否（``missing`` / ``not_due``）。
    6. 有文件 -> 按分析/复核进度推进，终态才算 ``completed``。

    第 4 步排在第 3 步之后是有意的：一份已被人工确认"本年度无此材料"的槽位，
    口径矛盾已经没有意义，不该再被拉回待确认。
    """
    moment = now or datetime.now(tz=due_at.tzinfo if due_at is not None else None)

    # 1) 身份未确认：年份/文种/主体任一未解决，或人工映射未完成
    if not identity_resolved:
        return SlotStatusResult("mapping_required", STATUS_REASON_IDENTITY_UNRESOLVED)

    # 2) 是否应收尚未确认
    if applicability_status == "unresolved":
        return SlotStatusResult("mapping_required", STATUS_REASON_APPLICABILITY_UNRESOLVED)

    # 3) 人工确认不适用
    if applicability_status == "not_applicable":
        return SlotStatusResult("not_applicable", STATUS_REASON_APPLICABILITY_MARKED)

    # 4) 口径矛盾未裁决
    if caliber_conflict:
        return SlotStatusResult("mapping_required", STATUS_REASON_CALIBER_CONFLICT)

    # 5) 适用但尚无当前文件
    if not has_current_document:
        if due_at is None:
            # 截止时间未知：无法证明逾期。计入"未到期"并注明成因，
            # 避免把未知当成"未上传"。
            return SlotStatusResult("not_due", STATUS_REASON_DUE_UNKNOWN)
        if _is_due(moment, due_at):
            return SlotStatusResult("missing", STATUS_REASON_DUE_EXCEEDED)
        return SlotStatusResult("not_due", STATUS_REASON_DUE_NOT_REACHED)

    # 6) 已有当前文件：按处理进度推进
    if analysis_state == ANALYSIS_FAILED:
        return SlotStatusResult("failed", STATUS_REASON_ANALYSIS_FAILED)
    if analysis_state == ANALYSIS_PROCESSING:
        return SlotStatusResult("processing", STATUS_REASON_ANALYSIS_RUNNING)
    if analysis_state == ANALYSIS_NOT_STARTED:
        return SlotStatusResult("uploaded", STATUS_REASON_AWAITING_ANALYSIS)

    # 分析已完成：进入人工复核链路
    if review_state == REVIEW_COMPLETED:
        return SlotStatusResult("completed", STATUS_REASON_REVIEW_DONE)
    if review_state == REVIEW_IN_PROGRESS:
        return SlotStatusResult("reviewing", STATUS_REASON_REVIEW_IN_PROGRESS)
    if review_state == REVIEW_REQUIRED:
        return SlotStatusResult("review_required", STATUS_REASON_FINDINGS_PENDING)
    # 分析完成且无需人工复核：材料本身已可结案，但仍不等同复核完成。
    # 这里停在 wait 状态而不是直接 completed——"没有待办"与"人已确认"
    # 是两件事，后者必须由 WP3 的持久化复核结论产生。
    return SlotStatusResult("review_required", STATUS_REASON_FINDINGS_PENDING)


def _is_due(now: datetime, due_at: datetime) -> bool:
    """判断是否已过截止时间。

    朴素时间与带时区时间相减会抛 TypeError，而 due_at 来自数据库
    (TIMESTAMPTZ) 通常带时区、now 可能是朴素本地时间。这里统一退化为
    "按本地时间比较"，而不是让整个状态推导崩掉。
    """
    try:
        return now > due_at
    except TypeError:
        return now.replace(tzinfo=None) > due_at.replace(tzinfo=None)


#: 从既有状态反推"分析与复核走到哪一步"。
#: 用途：调用方只关心槽位事实（是否已绑定版本、口径是否冲突）而不知道
#: 分析/复核进度时，刷新不能把进度信息丢掉——否则一次无关刷新会把
#: "待人工复核"打回"已上传"，用户看到的是待办凭空消失。
#: 反推结果必须能"往返一致"：用反推出的状态对同一批事实再推一次，
#: 仍得到原状态（相关用例逐条验证了这一点）。
_STATUS_TO_PROGRESS: dict = {
    "uploaded": (ANALYSIS_NOT_STARTED, REVIEW_NONE),
    "processing": (ANALYSIS_PROCESSING, REVIEW_NONE),
    "failed": (ANALYSIS_FAILED, REVIEW_NONE),
    "review_required": (ANALYSIS_DONE, REVIEW_REQUIRED),
    "reviewing": (ANALYSIS_DONE, REVIEW_IN_PROGRESS),
    "completed": (ANALYSIS_DONE, REVIEW_COMPLETED),
}


def infer_progress_state(status: Any) -> tuple:
    """从既有状态反推 (analysis_state, review_state)。

    无法反推（``not_due`` / ``missing`` / ``not_applicable`` / ``mapping_required``）
    时返回"分析未开始"——这些状态描述的是"文件到没到、身份定没定"，
    本来就不携带分析进度，重算不会丢信息。
    """
    return _STATUS_TO_PROGRESS.get(str(status or ""), (ANALYSIS_NOT_STARTED, REVIEW_NONE))


def status_is_blocking(status: str) -> bool:
    """该状态是否需要在工作台上被"催办"。

    ``not_due`` / ``not_applicable`` / ``completed`` 之外都需要关注：
    未到期不是问题，不适用不是问题，已完成是终点。
    """
    return status not in ("not_due", "not_applicable", "completed")


def counts_as_missing(status: str, reason: Optional[str] = None) -> bool:
    """是否计入"应收未收"。

    只有确实判定了逾期未上传才算；``not_due`` 的两种成因（真的没到期、
    截止时间未知）都不计入。这个函数存在的意义是：让"缺失数"这个对外数字
    只有一个出口，避免各页面各自 ``status == "missing"`` 时口径走偏。
    """
    return status == "missing" and reason in (None, STATUS_REASON_DUE_EXCEEDED)


def status_label(status: str) -> str:
    """面向业务用户的中文状态名。

    技术状态码不进业务界面主语言（PLAN §9）。
    """
    return {
        "not_due": "未到期",
        "missing": "逾期未上传",
        "uploaded": "已上传待分析",
        "processing": "分析中",
        "review_required": "待人工复核",
        "reviewing": "复核中",
        "completed": "已复核完成",
        "not_applicable": "不适用",
        "mapping_required": "待人工确认",
        "failed": "处理失败",
    }.get(str(status or ""), str(status or "未知"))


def reason_label(reason: Any) -> str:
    """成因码的中文说明。未登记的成因码原样返回，便于发现遗漏。"""
    return {
        STATUS_REASON_DUE_NOT_REACHED: "尚未到公开截止时间",
        STATUS_REASON_DUE_UNKNOWN: "截止时间未知，无法判定逾期",
        STATUS_REASON_DUE_EXCEEDED: "已过截止时间且未上传",
        STATUS_REASON_APPLICABILITY_UNRESOLVED: "是否应当有这份材料尚未确认",
        STATUS_REASON_APPLICABILITY_MARKED: "已人工确认本年度无此材料",
        STATUS_REASON_IDENTITY_UNRESOLVED: "年度/文种/主体映射待确认",
        STATUS_REASON_AWAITING_ANALYSIS: "已上传，等待分析",
        STATUS_REASON_ANALYSIS_RUNNING: "分析进行中",
        STATUS_REASON_ANALYSIS_FAILED: "分析失败，需重试或人工处理",
        STATUS_REASON_FINDINGS_PENDING: "存在待人工处理的问题",
        STATUS_REASON_REVIEW_IN_PROGRESS: "人工复核进行中",
        STATUS_REASON_REVIEW_DONE: "人工复核已完成",
        STATUS_REASON_CALIBER_CONFLICT: "材料口径识别结果互相矛盾，待人工裁决",
    }.get(str(reason or ""), str(reason or ""))
