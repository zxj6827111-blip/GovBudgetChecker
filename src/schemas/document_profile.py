"""报告画像（DocumentProfile）：材料识别结果的唯一权威载体。

为什么要单独抽一个画像模型
--------------------------
整改前，"这是预算还是决算"这件事在 8 个地方各判一次（上传 preflight、
status.json 兜底、两条规则引擎入口、通用规则内部、结果存储、结构化入库、
前端归一），而且兜底口径互相矛盾：``src/engine/common_rules.py`` 识别不到时
默认 ``"final"``，``src/engine/pipeline.py`` 默认 ``"unknown"``。同一份 PDF 在
不同入口会得到不同文种，于是"适用哪些检查"也随之漂移——这正是"漏查被当成
通过"最省事的成因：猜错了文种，专项规则整套都不会被调度，而结果里没有任何
字段能显示这一点。

本模块提供单一事实源。任何环节需要材料识别结果，都必须消费
``src/services/document_profile_resolver.resolve_document_profile`` 产出的
DocumentProfile，不得自行从文件名或首页文本重新猜文种。

设计取舍
--------
1. 每个维度记录**值 + 来源 + 置信度 + 被否决候选**。识别冲突时不静默取一个，
   而是保留全部原始依据（``rejected``），交人工确认——这与
   ``src/utils/report_year.py`` 里"识别不到就返回 None"是同一条纪律的延伸。
2. 识别不到就是 ``None`` / ``"unknown"``，**绝不做经验兜底**。兜底值会被下游
   当成事实消费，进而选出错误的检查配置。
3. ``profile_status`` 只描述"画像是否足以选择检查配置"，不描述材料质量。
   未解决时下游仍可执行通用算术与文字检查，但必须对用户明确显示
   "该报告类型尚未完整支持"，不能显示为通过。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 画像解析器版本。画像结构或候选优先级变更时必须递增，
#: 否则历史结果无法回答"这条结论是按哪版识别口径产生的"。
PROFILE_RESOLVER_VERSION = "document-profile-v1"

# ---- 维度取值域 -------------------------------------------------------------

#: 文种。``budget`` 含预算/调整预算，``final`` 含决算；无法识别为 ``unknown``。
REPORT_KINDS: Tuple[str, ...] = ("budget", "final", "unknown")

#: 主体层级。部门/单位/政府三级，识别不到为 ``unknown``。
SUBJECT_LEVELS: Tuple[str, ...] = ("department", "unit", "government", "unknown")

#: 口径。本级 vs 汇总（含下级单位）。识别不到为 ``unknown``。
#: 这个维度决定"能不能把分项直接相加"，猜错的代价是整表勾稽全错。
CALIBERS: Tuple[str, ...] = ("self", "summary", "unknown")

#: 资金范围。文档级识别到哪些预算口径在材料里出现。
FUND_SCOPE_GENERAL = "general_public"
FUND_SCOPE_GOVERNMENT_FUND = "government_fund"
FUND_SCOPE_STATE_CAPITAL = "state_capital"
FUND_SCOPES: Tuple[str, ...] = (
    FUND_SCOPE_GENERAL,
    FUND_SCOPE_GOVERNMENT_FUND,
    FUND_SCOPE_STATE_CAPITAL,
)

#: 提取质量。决定页面级检查（含扫描页）是否可信。
EXTRACTION_QUALITIES: Tuple[str, ...] = ("text", "mixed", "scanned", "unknown")

# ---- 维度来源 ---------------------------------------------------------------
# 来源同时是置信度依据：显式传入 > 封面结构化标签 > 封面标题 > 上传文种 >
# 文件名 > 正文首页。候选按此顺序求值，先命中者胜，其余进 ``rejected``。

SOURCE_EXPLICIT = "explicit"
SOURCE_COVER_LABEL = "cover_label"
SOURCE_COVER_TITLE = "cover_title"
SOURCE_DOC_TYPE = "doc_type"
SOURCE_FILENAME = "filename"
SOURCE_PAGE_TEXT = "page_text"
SOURCE_TABLE_TITLE = "table_title"
SOURCE_DERIVED = "derived"
SOURCE_UNRESOLVED = "unresolved"

SOURCE_CONFIDENCE: Dict[str, float] = {
    SOURCE_EXPLICIT: 1.0,
    SOURCE_COVER_LABEL: 0.9,
    SOURCE_COVER_TITLE: 0.85,
    SOURCE_DOC_TYPE: 0.8,
    SOURCE_FILENAME: 0.6,
    SOURCE_PAGE_TEXT: 0.5,
    SOURCE_TABLE_TITLE: 0.5,
    SOURCE_DERIVED: 0.4,
    SOURCE_UNRESOLVED: 0.0,
}

#: 画像状态
PROFILE_STATUS_RESOLVED = "resolved"
PROFILE_STATUS_PARTIAL = "partial"
PROFILE_STATUS_UNRESOLVED = "unresolved"


class ProfileField(BaseModel):
    """画像中的单个维度。

    值、来源、置信度必须同时存在。``rejected`` 保留被覆盖的候选依据——
    识别冲突不是异常，而是需要留痕的正常情况。
    """

    value: Any = Field(default=None, description="归一后的维度取值；未识别到为 None")
    source: str = Field(default=SOURCE_UNRESOLVED, description="取值来源")
    evidence: str = Field(default="", description="命中依据（标签名/文件名/片段标识，不含正文原文）")
    rejected: List[Dict[str, Any]] = Field(
        default_factory=list, description="被优先级覆盖的候选依据，保留供人工确认"
    )

    @property
    def resolved(self) -> bool:
        return self.value not in (None, "", "unknown", (), [], {})

    @property
    def confidence(self) -> float:
        return SOURCE_CONFIDENCE.get(self.source, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "rejected": self.rejected,
        }


class DocumentProfile(BaseModel):
    """一份材料的报告画像。

    字段分三类，用途不同：

    - **路由维度**（``report_kind`` / ``subject_level``）：决定选用哪套检查配置。
      未解决时不得声称"已完整检查"。
    - **口径维度**（``caliber`` / ``fund_scopes`` / ``report_year`` / ``organization_name``）：
      决定两个数字能不能比较。未解决时跨表/同比类检查只能标为未完成。
    - **质量维度**（``extraction_quality``）：决定页面级检查是否可信。
    """

    report_kind: ProfileField = Field(default_factory=ProfileField)
    report_year: ProfileField = Field(default_factory=ProfileField)
    organization_name: ProfileField = Field(default_factory=ProfileField)
    organization_label: ProfileField = Field(default_factory=ProfileField)
    subject_level: ProfileField = Field(default_factory=ProfileField)
    caliber: ProfileField = Field(default_factory=ProfileField)
    jurisdiction: ProfileField = Field(default_factory=ProfileField)
    fund_scopes: ProfileField = Field(default_factory=ProfileField)
    extraction_quality: ProfileField = Field(default_factory=ProfileField)
    cover_title: ProfileField = Field(default_factory=ProfileField)

    profile_status: str = Field(
        default=PROFILE_STATUS_UNRESOLVED,
        description="resolved / partial / unresolved，只描述画像可用性",
    )
    unsupported_reason: Optional[str] = Field(
        default=None,
        description="画像不足以选择检查配置时的原因码，供质量门与前端直接展示",
    )
    resolver_version: str = Field(default=PROFILE_RESOLVER_VERSION)
    conflicts: List[Dict[str, Any]] = Field(
        default_factory=list, description="维度级别的识别冲突（同维度出现互斥候选）"
    )

    # ---- 便捷读取（下游不应再自行解析 report_kind 字符串） ----

    @property
    def kind(self) -> str:
        value = self.report_kind.value
        return str(value) if value else "unknown"

    @property
    def year(self) -> Optional[int]:
        value = self.report_year.value
        return int(value) if isinstance(value, int) else None

    @property
    def level(self) -> str:
        value = self.subject_level.value
        return str(value) if value else "unknown"

    @property
    def is_fully_resolved(self) -> bool:
        return self.profile_status == PROFILE_STATUS_RESOLVED

    def fund_scope_values(self) -> List[str]:
        raw = self.fund_scopes.value
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_kind": self.report_kind.to_dict(),
            "report_year": self.report_year.to_dict(),
            "organization_name": self.organization_name.to_dict(),
            "organization_label": self.organization_label.to_dict(),
            "subject_level": self.subject_level.to_dict(),
            "caliber": self.caliber.to_dict(),
            "jurisdiction": self.jurisdiction.to_dict(),
            "fund_scopes": self.fund_scopes.to_dict(),
            "extraction_quality": self.extraction_quality.to_dict(),
            "cover_title": self.cover_title.to_dict(),
            "profile_status": self.profile_status,
            "unsupported_reason": self.unsupported_reason,
            "resolver_version": self.resolver_version,
            "conflicts": self.conflicts,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["DocumentProfile"]:
        """从持久化字典还原画像；缺失或非字典时返回 None。

        返回 None 而不是空画像，是为了让调用方明确区分"没有画像记录"与
        "画像是空的"。旧任务属于前者，展示时必须写"旧版未记录"，
        不能显示成已完成识别。
        """
        if not isinstance(data, dict):
            return None
        try:
            return cls.model_validate(data)
        except Exception:
            return None
