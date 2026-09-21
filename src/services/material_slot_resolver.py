"""槽位归属判定：从任务元数据决定"这份材料属于哪条应收材料"。

为什么是纯函数，且被两条路径共用
--------------------------------
判定规则同时用在两处：线上流程（结构化入库时给文档挂槽位）和离线回填
（``scripts/backfill_material_slots.py`` 的历史数据盘点）。如果两处各写一份，
"dry-run 说能自动映射 80 份、真跑却只映射了 60 份"就会成为常态，
而回填报告的存在意义恰恰是"跑之前就知道会怎样"。

所以判定逻辑只在这里发生一次，两条路径都调它，差别只在"要不要写库"。

判定纪律
--------
1. **只按组织 id 精确匹配，绝不按名称兜底。** 名称相同无法区分
   "规划和自然资源局（部门）"与"规划和自然资源局（本级单位）"，
   按名称归并正是本轮要修掉的原始缺陷。名称只在完全无法定位时用来生成
   占位槽位的显示名，且该槽位必为 ``mapping_required``。
2. **识别不到就不猜。** 年份、文种、主体任一维度识别不到，结论都不是
   "用一个默认值继续"，而是"停在 mapping_required 等人工确认"。
3. **冲突是结论，不是噪声。** 元数据里两个来源给出不同文种/年份时，
   不按优先级挑一个，而是报冲突——挑一个等于把矛盾藏进数据里。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from src.schemas.material_slot import (
    SlotIdentity,
    SubjectOrg,
    build_slot_identity,
    mapping_key_for_document,
    resolve_subject_org,
)

# ---- 判定结论 ---------------------------------------------------------------

#: 身份完整，可以建/复用正常槽位
DECISION_RESOLVED = "resolved"
#: 材料真实存在但身份未确认，建占位槽位并停在 mapping_required
DECISION_MAPPING_REQUIRED = "mapping_required"
#: 连占位槽位都建不了（没有可用于区分的文档标识）
DECISION_UNALLOCATABLE = "unallocatable"

#: 原因码
REASON_OK = "ok"
REASON_ORGANIZATION_MISSING = "organization_missing"
REASON_ORGANIZATION_UNRESOLVED = "organization_unresolved"
REASON_ORGANIZATION_LEVEL_UNKNOWN = "organization_level_unknown"
REASON_KIND_CONFLICT = "kind_conflict"
REASON_YEAR_CONFLICT = "year_conflict"
REASON_KIND_UNKNOWN = "kind_unknown"
REASON_YEAR_UNKNOWN = "year_unknown"
REASON_NO_DOCUMENT_KEY = "no_document_key"

#: 原因码 -> 回填报告的分桶。分桶是给业务看的，原因码是给排查看的，
#: 两者分开，避免为了报告好看而合并掉真实差别。
REASON_BUCKETS: Dict[str, str] = {
    REASON_OK: "auto_mappable",
    REASON_ORGANIZATION_MISSING: "missing_subject",
    REASON_ORGANIZATION_UNRESOLVED: "missing_subject",
    REASON_ORGANIZATION_LEVEL_UNKNOWN: "missing_subject",
    REASON_KIND_CONFLICT: "conflict",
    REASON_YEAR_CONFLICT: "conflict",
    REASON_KIND_UNKNOWN: "missing_kind",
    REASON_YEAR_UNKNOWN: "missing_year",
    REASON_NO_DOCUMENT_KEY: "no_document",
}

REASON_LABELS: Dict[str, str] = {
    REASON_OK: "可自动映射",
    REASON_ORGANIZATION_MISSING: "任务未记录主体组织",
    REASON_ORGANIZATION_UNRESOLVED: "主体组织不在组织目录中",
    REASON_ORGANIZATION_LEVEL_UNKNOWN: "主体组织层级无法用于槽位",
    REASON_KIND_CONFLICT: "文种在不同来源间冲突",
    REASON_YEAR_CONFLICT: "财政年度在不同来源间冲突",
    REASON_KIND_UNKNOWN: "文种未识别",
    REASON_YEAR_UNKNOWN: "财政年度未识别",
    REASON_NO_DOCUMENT_KEY: "缺少可区分文档的校验和",
}

#: ``doc_type`` 取值（路由层口径）到槽位文种的映射。
#: 只做"取值规范化"，不做猜测：不在这张表里的值一律视为未识别。
_DOC_TYPE_TO_KIND: Dict[str, str] = {
    "dept_budget": "budget",
    "budget": "budget",
    "dept_final": "final",
    "final": "final",
    "settlement": "final",
    "accounts": "final",
    "final_accounts": "final",
}


@dataclass(frozen=True)
class SlotAllocationDecision:
    """一次归属判定的完整结论。

    成功与失败共用同一结构，调用方按 ``status`` 分支，不需要用异常控制流程。
    """

    status: str
    reason: str
    identity: Optional[SlotIdentity] = None
    subject_org: Optional[SubjectOrg] = None
    subject_org_name: str = ""
    subject_org_code: Optional[str] = None
    department_org_id: Optional[str] = None
    department_name: Optional[str] = None
    jurisdiction_org_id: Optional[str] = None
    jurisdiction_name: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def bucket(self) -> str:
        return REASON_BUCKETS.get(self.reason, "other")

    @property
    def ok(self) -> bool:
        return self.status == DECISION_RESOLVED


# ---- 组织目录读取 -----------------------------------------------------------


def to_org_record(record: Any) -> Dict[str, Any]:
    """把组织目录记录归一为字典。

    兼容 pydantic 模型与 dict 两种形态：组织目录目前是 JSON 文件，
    但 ``ps_schema_sync`` 等调用方也会直接传字典进来，归一放在这里
    避免每个消费方各写一份。
    """
    if isinstance(record, dict):
        payload = dict(record)
    elif hasattr(record, "model_dump"):
        payload = dict(record.model_dump())
    else:
        payload = {
            "id": getattr(record, "id", None),
            "name": getattr(record, "name", None),
            "level": getattr(record, "level", None),
            "parent_id": getattr(record, "parent_id", None),
            "code": getattr(record, "code", None),
        }
    for key in ("id", "name", "level", "parent_id", "code"):
        payload[key] = _text_or_none(payload.get(key))
    return payload


def load_org_records() -> List[Dict[str, Any]]:
    """从组织目录读取全部组织。

    读不到时返回空列表而不是抛错：调用方会据此把结论判为
    ``organization_unresolved``，材料照样被安置成占位槽位。
    组织目录故障不应该让材料凭空消失。
    """
    try:
        from src.services.org_storage import get_org_storage
    except Exception:  # pragma: no cover - 导入期异常极少见
        return []
    try:
        storage = get_org_storage()
        return [to_org_record(record) for record in storage.get_all()]
    except Exception:
        return []


# ---- 元数据解析 -------------------------------------------------------------


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def collect_report_kind_candidates(metadata: Dict[str, Any]) -> List[str]:
    """收集所有能表达文种的来源，返回去重后的候选值。

    只收集**明确的**候选：识别不出来的来源不产生候选，也就不会把冲突
    误报成"两个来源打架"。判断"两个来源是否冲突"的前提是两边都说得出话。
    """
    candidates: List[str] = []

    raw_kind = str(metadata.get("report_kind") or "").strip().lower()
    if raw_kind in ("budget", "final"):
        candidates.append(raw_kind)

    raw_doc_type = str(metadata.get("doc_type") or "").strip().lower()
    mapped = _DOC_TYPE_TO_KIND.get(raw_doc_type)
    if mapped:
        candidates.append(mapped)

    profile = metadata.get("document_profile")
    if isinstance(profile, dict):
        field = profile.get("report_kind")
        if isinstance(field, dict):
            value = str(field.get("value") or "").strip().lower()
            if value in ("budget", "final"):
                candidates.append(value)

    return sorted({value for value in candidates})


def collect_report_year_candidates(metadata: Dict[str, Any]) -> List[int]:
    """收集年份候选，逐个用权威解析器解析，不识别的不产生候选。"""
    from src.utils.report_year import parse_report_year

    candidates: List[int] = []
    for key in ("report_year", "fiscal_year"):
        if key not in metadata:
            continue
        parsed = parse_report_year(metadata.get(key))
        if parsed is not None and parsed not in candidates:
            candidates.append(parsed)

    profile = metadata.get("document_profile")
    if isinstance(profile, dict):
        field = profile.get("report_year")
        if isinstance(field, dict):
            parsed = parse_report_year(field.get("value"))
            if parsed is not None and parsed not in candidates:
                candidates.append(parsed)

    return sorted(candidates)


def profile_reports_kind_conflict(metadata: Dict[str, Any]) -> bool:
    """画像自己已经标记了文种冲突。

    画像的 ``conflicts`` 是解析层按来源优先级排序后保留的落选候选，
    它比"两个字段字面不同"更接近真相，因此单独认。
    """
    profile = metadata.get("document_profile")
    if not isinstance(profile, dict):
        return False
    for item in profile.get("conflicts") or []:
        if isinstance(item, dict) and str(item.get("field") or "") == "report_kind":
            return True
    return False


# ---- 主体解析 ---------------------------------------------------------------


def _resolve_ancestors(
    org: SubjectOrg, by_id: Dict[str, Dict[str, Any]]
) -> tuple[Optional[str], Optional[str]]:
    """沿 parent 链找出行政区域（区县/市）。

    政府级组织的材料主体就是区本身；部门/单位的材料则要看它挂在哪个区下面。
    找不到行政区划时返回 (None, None)，界面显示为空，
    不拿主体名冒充区县名。
    """
    seen: set = set()
    current_id = org.org_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        record = by_id.get(current_id)
        if record is None:
            return (None, None)
        level = str(record.get("level") or "").strip().lower()
        if level in ("district", "city"):
            return (str(record["id"]), _text_or_none(record.get("name")))
        current_id = str(record.get("parent_id") or "").strip()
    return (None, None)


def _name_matches(org_records: Iterable[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """按名称在组织目录中找候选，仅用于报告"同名歧义"的数量。

    注意：这个函数的结果**不参与归属判定**，只用于解释"为什么这条没有自动映射"。
    """
    target = str(name or "").strip()
    if not target:
        return []
    return [record for record in org_records if str(record.get("name") or "").strip() == target]


# ---- 主判定 -----------------------------------------------------------------


def decide_slot_allocation(
    *,
    metadata: Dict[str, Any],
    org_records: List[Dict[str, Any]],
    checksum: Optional[str] = None,
) -> SlotAllocationDecision:
    """判定一条任务元数据应落到哪个槽位。

    ``checksum`` 是文档 sha256。它只在身份未确认时用作占位槽位的区分键；
    身份确认时槽位按身份归并，同一文档重复上传不会多出槽位。
    """
    payload = metadata if isinstance(metadata, dict) else {}
    document_key = mapping_key_for_document(checksum or payload.get("checksum"))

    claimed_org_id = _text_or_none(payload.get("organization_id"))
    claimed_org_name = _text_or_none(payload.get("organization_name"))

    kind_candidates = collect_report_kind_candidates(payload)
    year_candidates = collect_report_year_candidates(payload)
    kind_conflict = len(kind_candidates) > 1 or profile_reports_kind_conflict(payload)
    year_conflict = len(year_candidates) > 1

    report_kind = kind_candidates[0] if len(kind_candidates) == 1 else "unknown"
    fiscal_year = year_candidates[0] if len(year_candidates) == 1 else None

    subject = resolve_subject_org(claimed_org_id, org_records) if claimed_org_id else None

    # --- 身份完整 -----------------------------------------------------------
    if (
        subject is not None
        and subject.subject_kind is not None
        and subject.material_scope is not None
        and not kind_conflict
        and not year_conflict
        and report_kind != "unknown"
        and fiscal_year is not None
    ):
        identity = build_slot_identity(
            subject_org_id=subject.org_id,
            subject_kind=subject.subject_kind,
            material_scope=subject.material_scope,
            report_kind=report_kind,
            fiscal_year=fiscal_year,
            mapping_key="",
        )
        by_id = {str(rec.get("id") or ""): rec for rec in org_records}
        jurisdiction_id, jurisdiction_name = _resolve_ancestors(subject, by_id)
        department_org_id, department_name = _department_of(subject)
        return SlotAllocationDecision(
            status=DECISION_RESOLVED,
            reason=REASON_OK,
            identity=identity,
            subject_org=subject,
            subject_org_name=subject.name,
            subject_org_code=subject.code,
            department_org_id=department_org_id,
            department_name=department_name,
            jurisdiction_org_id=jurisdiction_id,
            jurisdiction_name=jurisdiction_name,
            detail={
                "kind_candidates": kind_candidates,
                "year_candidates": year_candidates,
            },
        )

    # --- 身份未确认：尽量安置成占位槽位 -------------------------------------
    if not document_key:
        # 没有校验和就没有任何可用于区分文档的东西。若强行建槽，多条未识别
        # 材料会挤进同一个槽位互相覆盖——那比不建槽更糟。
        return SlotAllocationDecision(
            status=DECISION_UNALLOCATABLE,
            reason=REASON_NO_DOCUMENT_KEY,
            detail={"claimed_organization_id": claimed_org_id},
        )

    reason = _unresolved_reason(
        claimed_org_id=claimed_org_id,
        subject=subject,
        kind_conflict=kind_conflict,
        year_conflict=year_conflict,
        report_kind=report_kind,
        fiscal_year=fiscal_year,
    )

    # 占位槽位的主体标识：能用任务记录的组织 id 就用它，否则退化为
    # 名称哈希并加 ``unresolved:`` 前缀，保证永远不可能和真实组织 id 撞上。
    if claimed_org_id:
        placeholder_org_id = claimed_org_id
    else:
        seed = claimed_org_name or str(payload.get("filename") or "")
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16] if seed else "anonymous"
        placeholder_org_id = f"unresolved:{digest}"

    placeholder_kind = subject.subject_kind if subject is not None else "unknown"
    identity = build_slot_identity(
        subject_org_id=placeholder_org_id,
        subject_kind=placeholder_kind or "unknown",
        material_scope=(subject.material_scope if subject is not None else None),
        report_kind=report_kind,
        fiscal_year=fiscal_year,
        mapping_key=document_key,
    )

    by_id = {str(rec.get("id") or ""): rec for rec in org_records}
    jurisdiction_id, jurisdiction_name = (
        _resolve_ancestors(subject, by_id) if subject is not None else (None, None)
    )
    department_org_id, department_name = (
        _department_of(subject) if subject is not None else (None, None)
    )

    name_matches = _name_matches(org_records, claimed_org_name or "")
    detail: Dict[str, Any] = {
        "kind_candidates": kind_candidates,
        "year_candidates": year_candidates,
        "claimed_organization_id": claimed_org_id,
        "claimed_organization_name": claimed_org_name,
        "same_name_match_count": len(name_matches),
        "same_name_levels": sorted(
            {str(rec.get("level") or "").strip() for rec in name_matches if rec.get("level")}
        ),
    }
    # "同名歧义"独立计数：目录里同时存在同名部门与同名单位时，
    # 人工确认的难度和风险都更高，报告中必须能单独看到。
    detail["same_name_ambiguous"] = len(name_matches) > 1 and len(
        {str(rec.get("level") or "").strip() for rec in name_matches}
    ) > 1

    return SlotAllocationDecision(
        status=DECISION_MAPPING_REQUIRED,
        reason=reason,
        identity=identity,
        subject_org=subject,
        subject_org_name=subject.name if subject is not None else (claimed_org_name or ""),
        subject_org_code=subject.code if subject is not None else None,
        department_org_id=department_org_id,
        department_name=department_name,
        jurisdiction_org_id=jurisdiction_id,
        jurisdiction_name=jurisdiction_name,
        detail=detail,
    )


def _unresolved_reason(
    *,
    claimed_org_id: Optional[str],
    subject: Optional[SubjectOrg],
    kind_conflict: bool,
    year_conflict: bool,
    report_kind: str,
    fiscal_year: Optional[int],
) -> str:
    """挑一个最能解释"为什么没自动映射"的原因。

    顺序按"越靠前越根本"：主体不确认时，文种/年份即使认出来了也建不成
    正常槽位，所以先报主体。冲突优于缺失：冲突说明有两个来源打架，
    比单纯没认出来更需要人工介入。
    """
    if claimed_org_id is None:
        return REASON_ORGANIZATION_MISSING
    if subject is None:
        return REASON_ORGANIZATION_UNRESOLVED
    if subject.subject_kind is None:
        return REASON_ORGANIZATION_LEVEL_UNKNOWN
    if kind_conflict:
        return REASON_KIND_CONFLICT
    if year_conflict:
        return REASON_YEAR_CONFLICT
    if report_kind == "unknown":
        return REASON_KIND_UNKNOWN
    return REASON_YEAR_UNKNOWN


def _department_of(subject: SubjectOrg) -> tuple[Optional[str], Optional[str]]:
    """主体的主管部门。

    - 单位：主管部门是它的上级部门；
    - 部门：主管部门就是它自己（部门汇总材料的主体即部门）；
    - 政府：没有主管部门。
    """
    if subject.subject_kind == "unit":
        return (subject.parent_id, subject.parent_name)
    if subject.subject_kind == "department":
        return (subject.org_id, subject.name)
    return (None, None)
