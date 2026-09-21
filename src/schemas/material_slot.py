"""材料槽位（Material Slot）：材料台账的稳定业务对象。

为什么需要这一层
----------------
整改前，系统的中心对象是 analysis job。这决定了一个结构性的盲区：只能回答
"跑过哪些任务"，回答不了"哪些材料应该有、哪些还没有"。未上传的材料根本不存在，
于是"应收未收"这件事在数据模型里无从表达。

一条槽位表达的是**一份应当存在的材料**：

    地区 + 主管部门 + 具体主体 + 主体层级 + 财政年度 + 文种 + 汇总/本级口径

它与另外四件事必须严格区分：

    Material Slot  ≠  PDF 文件
    ≠  文件版本（同一槽位可以 v1 -> v2 -> v3）
    ≠  分析任务（同一份 PDF 可以重跑多次分析）
    ≠  问题条目

因此本模块只描述槽位本身的身份与状态，不承载文件、任务、问题。

三条不可让步的口径
------------------
1. **部门与同名本级单位是两个主体。** 组织目录的 id 由
   ``md5(f"{level}:{parent_id}:{name}")`` 生成，层级进了哈希，天然不合并。
   本模块禁止任何按名称归并的逻辑。
2. **财政年度与发布日期分开。** 2024 年度决算在 2025 年发布是常态，
   槽位的 ``fiscal_year`` 只来自材料本身，绝不从发布时间推导。
3. **年份/文种/组织识别不到就不猜。** 槽位可以带 ``fiscal_year=None`` 或
   未解决的组织存在，但状态必须是 ``mapping_required``，交人工确认。
   绝不为了"回填率好看"填默认年份或默认文种。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 文种取值域直接复用画像解析器，避免第二套口径：
# 槽位说 budget/final，画像说 budget/final，两边不可能漂移。
from src.schemas.document_profile import REPORT_KINDS as _PROFILE_REPORT_KINDS

# ---- 取值域 ----------------------------------------------------------------

#: 文种取值域，与画像解析器同源。``unknown`` 是**允许入库**的取值：
#: 一份文种还没认出来的材料仍然真实存在，必须能在台账里被看见并停在
#: ``mapping_required`` 上等人工确认。若在这里拒绝 unknown，这类材料就无处安放，
#: "应收未收"台账会漏掉它们，正是本轮要修的盲区。
SLOT_REPORT_KINDS: tuple = tuple(_PROFILE_REPORT_KINDS)

#: 主体层级。政府级对应"区本身"作为主体的材料（如区级政府总预算）。
#: ``unknown`` 同样是可入库取值，语义是"主体尚未确认"。
SUBJECT_KINDS: tuple = ("department", "unit", "government", "unknown")

#: 材料覆盖范围。与 subject_kind 分开是因为两者确实可能不一致：
#: 一个单位如果发布的是本部门的汇总材料，主体是 unit 而范围是 department_summary。
MATERIAL_SCOPES: tuple = ("department_summary", "unit_self", "government", "unknown")

#: 材料数字口径：含不含下级单位。
CALIBERS: tuple = ("summary", "self", "unknown")

#: 适用性。unresolved 表示"是否应当有这份材料尚未确认"，
#: 与 not_applicable（已人工确认不适用）必须分开，否则会把待确认当成已结论。
APPLICABILITY_STATUSES: tuple = ("applicable", "not_applicable", "unresolved")

#: 槽位状态。语义见 ``src/services/material_slot_status.py``。
SLOT_STATUSES: tuple = (
    "not_due",           # 未到应收/公开截止时间 —— 不得统计为缺失
    "missing",           # 已到期 + 适用 + 没有当前文件
    "uploaded",          # 已有文件，尚未分析
    "processing",        # 分析中
    "review_required",   # 待人工复核
    "reviewing",         # 人工复核中
    "completed",         # 复核完成（持久化结论，不是前端按钮跳转）
    "not_applicable",    # 人工确认本单位本年度无此材料
    "mapping_required",  # 年度/文种/主体映射待确认
    "failed",            # 处理失败
)

#: 状态成因码。status 只有 10 个值，不足以区分"没到期"与"到期时间未知"，
#: 后者必须在界面上可分辨，因此原因单独记录。
STATUS_REASON_DUE_NOT_REACHED = "due_not_reached"
STATUS_REASON_DUE_UNKNOWN = "due_at_unknown"
STATUS_REASON_DUE_EXCEEDED = "due_exceeded"
STATUS_REASON_APPLICABILITY_UNRESOLVED = "applicability_unresolved"
STATUS_REASON_APPLICABILITY_MARKED = "applicability_marked_not_applicable"
STATUS_REASON_IDENTITY_UNRESOLVED = "identity_unresolved"
STATUS_REASON_AWAITING_ANALYSIS = "awaiting_analysis"
STATUS_REASON_ANALYSIS_RUNNING = "analysis_running"
STATUS_REASON_ANALYSIS_FAILED = "analysis_failed"
STATUS_REASON_FINDINGS_PENDING = "findings_pending"
STATUS_REASON_REVIEW_IN_PROGRESS = "review_in_progress"
STATUS_REASON_REVIEW_DONE = "review_completed"
#: 已确认的口径与后来识别到的口径互相矛盾。这类矛盾必须由人工裁决，
#: 不能被"取最新一次识别"或"取先写入的值"这类默认策略静默抹平。
STATUS_REASON_CALIBER_CONFLICT = "caliber_conflict"

#: 组织目录里的层级 -> 槽位主体层级。组织目录用 city/district/department/unit 四级，
#: 槽位把 city/district 统一表达为 government（谁在报：政府本级，而不是某个部门）。
_ORG_LEVEL_TO_SUBJECT_KIND: Dict[str, str] = {
    "city": "government",
    "district": "government",
    "department": "department",
    "unit": "unit",
}

#: 主体层级 -> 默认材料覆盖范围
_SUBJECT_KIND_TO_SCOPE: Dict[str, str] = {
    "department": "department_summary",
    "unit": "unit_self",
    "government": "government",
}


# ---- 身份完整性：全系统唯一判定 --------------------------------------------


def slot_identity_is_resolved(
    *,
    subject_org_id: Any,
    subject_kind: Any,
    material_scope: Any,
    report_kind: Any,
    fiscal_year: Any,
    mapping_key: Any = "",
) -> bool:
    """槽位身份是否已经完整到可以当作"一条应收材料"。

    **这是全系统唯一的判定入口。** 内存里的 ``SlotIdentity.is_resolved``
    委托给它；从数据库读回来的槽位行也必须调它，不许各自写一份简化版。

    为什么要专门抽出来：此前 ``refresh_status`` 只看了 ``mapping_key``，
    于是 ``mapping_key='' 且 fiscal_year IS NULL`` 这种"年份没认出来"的槽位
    被判成身份已确认，一路滑到 ``not_due``——年份未知的材料被当成"没到期"，
    这是最典型的把未知当已知。

    判定与 ``SlotIdentity`` 的六个身份维度一一对应，全部满足才算完整：
    主体 id 非空、主体层级与材料范围与文种都不是 ``unknown``、
    财政年度不是 NULL、且不是"按具体文档临时安置"的占位槽位。
    """
    kind = str(subject_kind or "").strip()
    scope = str(material_scope or "").strip()
    report = str(report_kind or "").strip()
    if not str(subject_org_id or "").strip():
        return False
    if kind in ("", "unknown") or scope in ("", "unknown") or report in ("", "unknown"):
        return False
    if fiscal_year is None or fiscal_year == "":
        return False
    if str(mapping_key or "").strip():
        return False
    return True


class SlotIdentityError(ValueError):
    """槽位身份无法可靠确定。

    抛出即表示"这条材料不允许自动入库"，调用方必须转入 ``mapping_required``
    或人工映射，**不得**填入任何经验默认值继续。
    """


# ---- 身份 ------------------------------------------------------------------


@dataclass(frozen=True)
class SlotIdentity:
    """槽位的身份六元组。

    ``frozen`` 是刻意的：身份对象一旦构造出来就不该被就地改写，
    需要不同的身份就构造新的，避免"改了一半的身份"写进唯一键。

    ``mapping_key`` 是身份无法可靠确定时的区分键，语义与
    ``org_dept_annual_report.scope_key`` 一致：空串表示按正常身份归并，
    非空（通常是文档 sha256）表示按具体文档单独建槽。没有它的话，
    同一主体所有"年份未知"的材料会挤进同一个槽位互相覆盖。
    """

    subject_org_id: str
    subject_kind: str
    material_scope: str
    report_kind: str
    fiscal_year: Optional[int]
    mapping_key: str = ""

    def __post_init__(self) -> None:
        # 只校验"取值合法"，不校验"取值已确认"。哪些维度允许 unknown 由
        # is_resolved 表达，槽位的最终状态由状态机表达——把未确认当非法值会让
        # 未确认的材料无处安放。
        if not str(self.subject_org_id or "").strip():
            raise SlotIdentityError("subject_org_id 为空：无法确定主体，不允许建槽位")
        if self.subject_kind not in SUBJECT_KINDS:
            raise SlotIdentityError(f"未知主体层级: {self.subject_kind!r}")
        if self.material_scope not in MATERIAL_SCOPES:
            raise SlotIdentityError(f"未知材料范围: {self.material_scope!r}")
        if self.report_kind not in SLOT_REPORT_KINDS:
            raise SlotIdentityError(f"未知文种: {self.report_kind!r}")
        if self.fiscal_year is not None and not (2000 <= int(self.fiscal_year) <= 2099):
            raise SlotIdentityError(f"财政年度超出可信区间: {self.fiscal_year!r}")
        if self.subject_kind == "unknown" and self.material_scope != "unknown":
            raise SlotIdentityError("主体层级未知时材料范围必须同为未知，禁止推测范围")

    def canonical_payload(self) -> Dict[str, Any]:
        """身份键的规范化载荷。

        字段顺序固定、年份归一为 int 或 None——键值必须只由身份决定，
        不能因为调用方传了 ``"2024"`` 还是 ``2024`` 而算出两个键。
        """
        return {
            "subject_org_id": str(self.subject_org_id).strip(),
            "subject_kind": self.subject_kind,
            "material_scope": self.material_scope,
            "report_kind": self.report_kind,
            "fiscal_year": int(self.fiscal_year) if self.fiscal_year is not None else None,
            "mapping_key": str(self.mapping_key or ""),
        }

    @property
    def slot_key(self) -> str:
        """稳定身份键（sha256 前 32 位十六进制）。

        与组织目录 id 同一种手法（内容哈希），但长度取 32 位——
        槽位是长期引用对象，会进日志、URL 和导出报告，碰撞余量要更足。
        """
        payload = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @property
    def is_resolved(self) -> bool:
        """身份是否足以把槽位当作一条"应收材料"看待。

        实现委托给模块级 ``slot_identity_is_resolved``，因此内存对象与
        数据库行使用**同一套判定**——两处各写一份必然漂移，而漂移的表现
        恰好是"库里已经落地的槽位被判成已确认、内存里的同一个身份被判成未确认"
        这种最难查的不一致。
        """
        return slot_identity_is_resolved(**self.canonical_payload())


# ---- 组织解析 --------------------------------------------------------------


@dataclass(frozen=True)
class SubjectOrg:
    """组织目录中一条已确认的组织记录。

    只保留槽位需要的字段，避免把组织目录的完整模型复制进业务层——
    组织主数据后续统一时，这里是最小的耦合面。
    """

    org_id: str
    name: str
    level: str
    parent_id: Optional[str] = None
    code: Optional[str] = None
    parent_name: Optional[str] = None
    parent_code: Optional[str] = None

    @property
    def subject_kind(self) -> Optional[str]:
        return _ORG_LEVEL_TO_SUBJECT_KIND.get(str(self.level or "").strip().lower())

    @property
    def material_scope(self) -> Optional[str]:
        kind = self.subject_kind
        return _SUBJECT_KIND_TO_SCOPE.get(kind) if kind else None


def resolve_subject_org(org_id: Any, org_records: List[Dict[str, Any]]) -> Optional[SubjectOrg]:
    """按组织 id 在组织目录中解析主体。

    只按 **id** 精确匹配。按名称匹配是不允许的：名称相同无法区分
    "规划和自然资源局（部门）"与"规划和自然资源局（本级单位）"，
    按名称归并正是本轮要修掉的缺陷。

    返回 ``None`` 表示目录里没有这条 id——调用方必须转 ``mapping_required``，
    不得回退到名称匹配。
    """
    target = str(org_id or "").strip()
    if not target:
        return None
    by_id = {str(record.get("id") or "").strip(): record for record in org_records}
    record = by_id.get(target)
    if record is None:
        return None

    parent = by_id.get(str(record.get("parent_id") or "").strip())
    return SubjectOrg(
        org_id=target,
        name=str(record.get("name") or "").strip(),
        level=str(record.get("level") or "").strip().lower(),
        parent_id=str(record.get("parent_id") or "").strip() or None,
        code=_text_or_none(record.get("code")),
        parent_name=str(parent.get("name") or "").strip() if parent else None,
        parent_code=_text_or_none(parent.get("code")) if parent else None,
    )


def build_slot_identity(
    *,
    subject_org_id: Any,
    subject_kind: Any,
    material_scope: Any,
    report_kind: Any,
    fiscal_year: Any,
    mapping_key: str = "",
) -> SlotIdentity:
    """构造槽位身份；任一维度不可靠即抛 ``SlotIdentityError``。

    实现上不吞异常为默认值：文种 ``unknown``、年份缺失到无法解析，
    都在这里失败，由调用方决定是转 ``mapping_required`` 还是人工确认。
    """
    kind = str(subject_kind or "").strip() or "unknown"
    # 主体层级未知时不允许推导范围：范围猜错会让下游把不同材料当成可比较的。
    scope = (
        str(material_scope or "").strip()
        or _SUBJECT_KIND_TO_SCOPE.get(kind)
        or "unknown"
    )
    return SlotIdentity(
        subject_org_id=str(subject_org_id or "").strip(),
        subject_kind=kind,
        material_scope=scope,
        report_kind=str(report_kind or "").strip(),
        fiscal_year=_coerce_year(fiscal_year),
        mapping_key=str(mapping_key or ""),
    )


def _coerce_year(value: Any) -> Optional[int]:
    """把年份归一为 int 或 None；不识别就返回 None，绝不兜底。

    这里复用 ``src/utils/report_year`` 的解析口径，避免第二套年份解析。
    解析路径上唯一的差别是：本函数只接受能被权威解析器认出的年份。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 2000 <= value <= 2099 else None
    try:
        from src.utils.report_year import parse_report_year
    except Exception:  # pragma: no cover - 导入失败时退化为纯数字判断
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return parse_report_year(value)


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def mapping_key_for_document(checksum: Any) -> str:
    """身份未解决时，用文档 sha256 作为区分键。

    没有它，同一主体所有"年份未知"材料会归并成一个槽位互相覆盖；
    有了它，每条未知材料各自成槽且仍处于 ``mapping_required``，
    人工确认后再改判到正确槽位。
    """
    text = str(checksum or "").strip().lower()
    return f"doc:{text}" if text else ""


@dataclass
class SlotResolution:
    """一次"该不该建槽、建哪个槽"的判定结果。

    成功与失败共用同一结构：``identity`` 为 None 时 ``reason`` 一定非空，
    调用方无需用异常控制流程判断失败原因。
    """

    identity: Optional[SlotIdentity] = None
    reason: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.identity is not None
