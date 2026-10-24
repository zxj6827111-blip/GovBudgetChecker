"""检查主体：规则把哪个单位当作"本单位"。

为什么要与任务关联的组织分开
----------------------------
任务关联的组织（``organization_id`` / ``organization_name``）决定材料归档到
组织树的哪个节点；规则的"本单位"决定说明原文里出现的单位名是本单位还是
他单位。两者通常是同一个名字，但手动关联到市/区级上级节点时会分叉：封面写
"上海市退役军人事务局"，关联节点是"上海市"。规则若以"上海市"为本单位，
去掉"上海市"后剩下的"退役军人事务局"被判成其他主体，说明四总计、收入结构
等表文比对整类停在 ``insufficient_data``，转成人工补核。

只在可证明的情形下改用封面单位名：

1. 关联节点是市/区级（``city`` / ``district``）；
2. 封面单位名完整包含关联节点名、比它长，且以部门/单位后缀结尾——即该行政
   区划下的部门或单位，而不是另一个区划（"上海市普陀区"不改）；
3. 画像没有把单位名标为冲突，材料也不是政府级。

归档关联保持用户的选择不变；改用的事实写进 ``conflict``，供元数据页提示。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

PARENT_LEVELS = {"city": "市", "district": "区"}

_DEPARTMENT_SUFFIX = re.compile(
    r"(?:局|委员会|委|办公室|办事处|厅|中心|院|所|站|队|处|馆|学校|医院|协会|联合会|"
    r"学会|社|团|部|署|会|集团)(?:本级|本部)?$"
)


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", str(value or ""))


def resolve_check_subject(
    *,
    organization_id: Any,
    organization_name: Optional[str],
    organization_level: Optional[str],
    cover_name: Optional[str],
    cover_conflicted: bool = False,
    subject_level: Optional[str] = None,
) -> Dict[str, Any]:
    """返回检查主体；``name`` 是规则应当视为本单位的名称。"""
    linked = str(organization_name or "").strip() or None
    cover = str(cover_name or "").strip() or None
    level = str(organization_level or "").strip().lower() or None
    subject = {
        "name": linked,
        "source": "organization" if organization_id else "document_profile",
        "organization_id": organization_id,
        "organization_name": linked,
        "organization_level": level,
        "cover_organization_name": cover,
        "conflict": None,
    }
    if (not organization_id or level not in PARENT_LEVELS or not linked or not cover
            or cover_conflicted or subject_level == "government"):
        return subject
    linked_key, cover_key = _compact(linked), _compact(cover)
    if (not linked_key or linked_key not in cover_key or len(cover_key) <= len(linked_key)
            or not _DEPARTMENT_SUFFIX.search(cover_key)):
        return subject
    message = (
        f"任务关联的是{PARENT_LEVELS[level]}级节点“{linked}”，封面单位是其下属的“{cover}”；"
        f"规则以封面单位作为本单位检查，归档关联保持“{linked}”不变。"
        f"如需按部门归档，请在组织管理中关联到“{cover}”。"
    )
    return {
        **subject,
        "name": cover,
        "source": "cover",
        "conflict": {"code": "linked_parent_organization", "message": message},
    }
