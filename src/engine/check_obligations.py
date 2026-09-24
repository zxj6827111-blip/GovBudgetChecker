"""检查义务清单与逐实例执行账本：把"检查了多少条"升级为"应检查的事情完成了多少"。

要解决的问题
------------
整改前，系统对外的完成度信号只有三种：页面文本覆盖率、核心表识别、规则执行摘要
（``total_rules`` / ``executed``）。三者都不是"应检查的事项台账"：

- 规则总数是**实现方视角**。删掉一条规则、把它标成不适用，总数就跟着变小，
  完成率反而更漂亮；
- 某条规则"存在但根本没被调度"与"跑了但没查到这个点"在摘要里长得一样；
- 规则还没实现的能力（本轮真实漏报里就有：表间资金来源一致性、表文逐项比对、
  文内指标重复披露、预算完成率）**根本不会出现在任何计数里**——它们不是
  "未完成"，而是"不存在"，于是漏查被静默当成通过。

本模块建立版本化的**检查义务清单**（obligation catalog）与**逐实例执行账本**
（ledger）：对一份材料，先把"应检查的事项"按画像展开成实例，再为每个实例
给出明确状态与依据。核心纪律：

1. **应执行而未完成，一律不计为通过。** 台账里的未完成实例会让质量门保留
   ``review_required`` / ``incomplete``。
2. **非适用项必须有依据。** 排除一个实例必须写明理由（文种不符、层级不符、
   规则自身判定不适用），不能为了提高完成率随意排除。
3. **完成率不可被绿化。** 台账分母只由"应检查事项"决定，与规则注册数无关；
   分母为 0 时完成率是 ``None``，不是 100%。
4. **规则未实现要显式记账。** ``pending_checkers`` 声明的是"计划中但尚无实现"
   的规则编号，台账据此报 ``not_implemented``，而不是假装没这条要求。

checker 必须按文种声明
----------------------
``checkers_by_kind`` 而不是一个扁平列表：同一个要求（比如"封面要素齐全"）在预算
与决算下由完全不同的规则实现（``BUD-003`` / ``V33-001``）。若只声明一个并集，
决算材料会因为"预算规则不在决算注册表里"被误报成"尚未实现"——那是假缺口，
会掩盖真缺口。声明成 mapping 后，"缺实现"的判定天然按文种进行。

与规则六态的关系
----------------
实例状态从 ``rule_execution_summary`` 的逐规则回执推导（见
``src/engine/rule_outcome.py`` 的 ``rule_statuses``），不复用也不改写六态：
``insufficient_data`` / ``parse_error`` / ``execution_error`` 如实映射为
台账的未完成原因，``fail`` 与 ``pass`` 都算已完成（前者附问题、后者无问题）。

AI 语义义务与 AI 必需开关
------------------------
``requires_ai`` 的实例在 AI 未成功执行时一律记为未完成（``ai_not_run`` /
``ai_failed``）。但它们是否**阻塞**质量门，沿用既有的 ``AI_ASSIST_REQUIRED``
策略（``api/main.py`` 的 ``_ai_assist_required``，默认 false）：未配置为必需
能力时，AI 义务的未完成只进覆盖摘要供人工查看，不改变终态。这是为了不让
本模块在"用户显式选择的纯规则模式"下凭空制造人工复核量。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from src.engine.rule_outcome import (
    STATUS_EXECUTION_ERROR,
    STATUS_FAIL,
    STATUS_INSUFFICIENT_DATA,
    STATUS_NOT_APPLICABLE,
    STATUS_PARSE_ERROR,
    STATUS_PASS,
)
from src.schemas.document_profile import DocumentProfile

#: 义务清单版本。清单增删条目、调整 checkers 或适用条件时必须递增，
#: 否则历史结论无法回答"当时是按哪版要求判定已检查完整的"。
#: v2（2026-09-17 独立验收整改）：拆出零基数同比复算缺口（OBL-TREND-ZERO-BASE），
#: 收窄 OBL-TREND-COMPARATIVE-LOGIC 的 basis 使其与 CMM-005 实际能力一致，
#: 文种冲突单独登记为阻塞实例（OBL-PROFILE-KIND-CONFLICT）。
#: v3（2026-09-24 WP4-A）：OBL-CROSS-SAN-GONG-ECON 的实现缺口补齐——
#: checker 从 pending_checkers 转为真实 checker（V33-CROSS-SAN-GONG-ECON），
#: 依赖表从 FIN_07 修正为 FIN_06+FIN_07（该义务本就是跨这两张表）。
OBLIGATION_CATALOG_VERSION = "obligations-v3"

# ---- 实例状态 ---------------------------------------------------------------

OBLIGATION_COMPLETED = "completed"
OBLIGATION_NOT_APPLICABLE = "not_applicable"
OBLIGATION_NOT_IMPLEMENTED = "not_implemented"
OBLIGATION_NOT_EXECUTED = "not_executed"
OBLIGATION_INSUFFICIENT_DATA = "insufficient_data"
OBLIGATION_PARSE_AMBIGUITY = "parse_ambiguity"
OBLIGATION_EXECUTION_ERROR = "execution_error"
OBLIGATION_PROFILE_UNRESOLVED = "profile_unresolved"
OBLIGATION_KIND_CONFLICT = "kind_conflict"
OBLIGATION_AI_NOT_RUN = "ai_not_run"
OBLIGATION_AI_FAILED = "ai_failed"

#: 未完成状态集合。落在其中的实例不计为通过。
UNRESOLVED_OBLIGATION_STATUSES: FrozenSet[str] = frozenset(
    {
        OBLIGATION_NOT_IMPLEMENTED,
        OBLIGATION_NOT_EXECUTED,
        OBLIGATION_INSUFFICIENT_DATA,
        OBLIGATION_PARSE_AMBIGUITY,
        OBLIGATION_EXECUTION_ERROR,
        OBLIGATION_PROFILE_UNRESOLVED,
        OBLIGATION_KIND_CONFLICT,
        OBLIGATION_AI_NOT_RUN,
        OBLIGATION_AI_FAILED,
    }
)

#: 未完成原因的中文标签。展示与导出统一消费这里，避免各处各写一套措辞。
OBLIGATION_REASON_LABELS: Dict[str, str] = {
    OBLIGATION_NOT_IMPLEMENTED: "尚未实现",
    OBLIGATION_NOT_EXECUTED: "未执行",
    OBLIGATION_INSUFFICIENT_DATA: "取数不足",
    OBLIGATION_PARSE_AMBIGUITY: "解析歧义",
    OBLIGATION_EXECUTION_ERROR: "执行异常",
    OBLIGATION_PROFILE_UNRESOLVED: "画像未确认",
    OBLIGATION_KIND_CONFLICT: "文种冲突待确认",
    OBLIGATION_AI_NOT_RUN: "语义模型未执行",
    OBLIGATION_AI_FAILED: "语义模型失败",
}

# 规则六态 -> 台账未完成原因
_RULE_STATUS_TO_OBLIGATION_REASON: Dict[str, str] = {
    "insufficient_data": OBLIGATION_INSUFFICIENT_DATA,
    "parse_error": OBLIGATION_PARSE_AMBIGUITY,
    "execution_error": OBLIGATION_EXECUTION_ERROR,
}

#: 规则回执的合法终态（六态，取值来自 ``rule_outcome`` 的权威常量）。
#: 台账只认这些：缺回执或状态不在六态内都算"未执行"，不能算完成——
#: "某义务需要两条规则、只来了一张 pass 回执"不允许被记成整单完成。
KNOWN_RULE_STATUSES: FrozenSet[str] = frozenset(
    {
        STATUS_PASS,
        STATUS_FAIL,
        STATUS_NOT_APPLICABLE,
        STATUS_INSUFFICIENT_DATA,
        STATUS_PARSE_ERROR,
        STATUS_EXECUTION_ERROR,
    }
)

# ---- 义务分组 ---------------------------------------------------------------

GROUP_STRUCTURE = "DOC_STRUCTURE"
GROUP_CORE_TABLES = "CORE_TABLES"
GROUP_INTERNAL = "TABLE_INTERNAL"
GROUP_CROSS = "TABLE_CROSS"
GROUP_TABLE_TEXT = "TABLE_TEXT"
GROUP_NARRATIVE = "NARRATIVE"
GROUP_TREND = "RATIO_TREND"
GROUP_DISCLOSURE = "DISCLOSURE"
GROUP_SAN_GONG = "SAN_GONG"
GROUP_PERFORMANCE = "PERFORMANCE"

OBLIGATION_GROUP_TITLES: Dict[str, str] = {
    GROUP_STRUCTURE: "文档结构与必备要素",
    GROUP_CORE_TABLES: "核心表完整性",
    GROUP_INTERNAL: "表内关系",
    GROUP_CROSS: "表间关系",
    GROUP_TABLE_TEXT: "表文关系",
    GROUP_NARRATIVE: "文内关系",
    GROUP_TREND: "比例与变动",
    GROUP_DISCLOSURE: "披露与表达",
    GROUP_SAN_GONG: "三公经费",
    GROUP_PERFORMANCE: "绩效披露",
}

#: 证据要求。``locatable`` 的问题必须能回到原文某一处；
#: ``document_level`` 是结构性"有没有"判定，天然不指向单页。
EVIDENCE_LOCATABLE = "locatable"
EVIDENCE_DOCUMENT_LEVEL = "document_level"


def _final(*codes: str) -> Dict[str, Tuple[str, ...]]:
    """仅决算可用的 checker 集合。"""
    return {"final": tuple(codes)}


def _budget(*codes: str) -> Dict[str, Tuple[str, ...]]:
    """仅预算可用的 checker 集合。"""
    return {"budget": tuple(codes)}


def _both(final_codes: Sequence[str], budget_codes: Sequence[str]) -> Dict[str, Tuple[str, ...]]:
    """同一要求在预算与决算下由不同规则实现时的 checker 集合。"""
    return {"final": tuple(final_codes), "budget": tuple(budget_codes)}


@dataclass(frozen=True)
class Obligation:
    """一条检查义务。

    ``checkers_by_kind`` 与 ``pending_checkers`` 的区别是本模块的关键：
    前者是已实现的规则编号（按文种分组），台账会去执行回执里逐条核对；
    后者是"这项要求应该有规则但还没有"，台账直接记 ``not_implemented``。
    """

    obligation_id: str
    group_id: str
    title: str
    report_kinds: Tuple[str, ...]
    subject_levels: Tuple[str, ...] = ()
    checkers_by_kind: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    pending_checkers: Tuple[str, ...] = ()
    requires_ai: bool = False
    evidence_kind: str = EVIDENCE_LOCATABLE
    depends_on: Tuple[str, ...] = ()
    basis: str = ""
    gap_note: str = ""

    def checkers_for(self, kind: str) -> Tuple[str, ...]:
        return tuple(self.checkers_by_kind.get(kind, ()))

    @property
    def all_checkers(self) -> Tuple[str, ...]:
        """跨文种的 checker 并集，供 finding 反向挂义务编号时使用。"""
        seen: List[str] = []
        for codes in self.checkers_by_kind.values():
            for code in codes:
                if code not in seen:
                    seen.append(code)
        return tuple(seen)


# ---- 清单本体 ---------------------------------------------------------------
# 依据来源：AGENTS.md 内置 ``v3_3_portable`` 规则集的预算/决算必查项，
# 以及本轮样张复核确认的漏报类别。``pending_checkers`` 的缺口说明直接写在
# gap_note 里，避免"没有实现"这件事只在文档里可见、代码里看不见。

_FINAL_ONLY: Tuple[str, ...] = ("final",)
_BUDGET_ONLY: Tuple[str, ...] = ("budget",)
_BOTH: Tuple[str, ...] = ("budget", "final")

OBLIGATION_CATALOG: Tuple[Obligation, ...] = (
    # ---- 文档结构与核心表 ----
    Obligation(
        obligation_id="OBL-STRUCT-COVER",
        group_id=GROUP_STRUCTURE,
        title="封面要素：年度、单位、文种、金额单位",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-001",), ("BUD-003",)),
        evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
        basis="预决算公开材料须在封面标明年度、编制单位与文种，金额单位须与表格一致",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-NINE-TABLES",
        group_id=GROUP_CORE_TABLES,
        title="核心表集合完整性（可适用表齐全、顺序正确）",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-002", "V33-003"),
        evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
        depends_on=("table:FIN_01", "table:FIN_02", "table:FIN_03", "table:FIN_04",
                    "table:FIN_05", "table:FIN_06", "table:FIN_07"),
        basis="部门决算公开须包含收入支出决算总表等核心表",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-BUDGET-TABLES",
        group_id=GROUP_CORE_TABLES,
        title="预算核心表集合完整性（含条件性表）",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-001"),
        evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
        depends_on=("table:BUD_T1", "table:BUD_T3", "table:BUD_T5", "table:BUD_T8",
                    "table:BUD_T9"),
        basis="部门预算公开须包含收支总表等核心表，条件性表按资金范围判定",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-EMPTY-STATEMENT",
        group_id=GROUP_DISCLOSURE,
        title="空表/无此项的规范说明",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-114", "V33-109", "V33-122"), ("BUD-106",)),
        evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
        basis="无此项或空表必须给出说明，且说明与所涉表名一致（不得套用模板）",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-TERMINOLOGY",
        group_id=GROUP_DISCLOSURE,
        title="文种口径用语一致性（部门/单位不得混用）",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-236",), ("BUD-113",)),
        basis="单位预算材料中出现'部门预算安排'等表述属口径混用",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-PLACEHOLDER",
        group_id=GROUP_DISCLOSURE,
        title="占位符与模板残留",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-112",), ("BUD-002",)),
        basis="XX、××、某某等占位符不得出现在正式公开材料中",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-PUNCTUATION",
        group_id=GROUP_DISCLOSURE,
        title="标点与文字表达异常（重复标点、重复词、中英标点混用）",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-113", "CMM-002"), ("CMM-002",)),
        basis="重复词、连续句号、英文逗号等属表达类缺陷",
    ),
    Obligation(
        obligation_id="OBL-STRUCT-NUMBER-VALIDITY",
        group_id=GROUP_INTERNAL,
        title="数值单元格可解析性",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-004"),
        basis="表内金额须可解析为数值，否则后续勾稽全部失去基础",
    ),
    # ---- 表内关系 ----
    Obligation(
        obligation_id="OBL-INTERNAL-T1-BALANCE",
        group_id=GROUP_INTERNAL,
        title="收入支出决算总表：支出列总计 = 结余分配 + 年末结转和结余 + 本年支出合计",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-214"),
        depends_on=("table:FIN_01",),
        basis="AGENTS.md 决算必查勾稽 D-001",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-T1-BUDGET",
        group_id=GROUP_INTERNAL,
        title="收支总表：收入总计 = 支出总计",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-101"),
        depends_on=("table:BUD_T1",),
        basis="收支总表横向/纵向合计须自洽",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-T2-TOTAL",
        group_id=GROUP_INTERNAL,
        title="收入决算表：同一行横向合计与纵向合计一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-240"),
        depends_on=("table:FIN_02",),
        basis="表内行合计与列合计须一致",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-T3-TOTAL",
        group_id=GROUP_INTERNAL,
        title="支出决算表：本年支出合计 = 基本支出 + 项目支出",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-241"),
        depends_on=("table:FIN_03",),
        basis="AGENTS.md 决算必查勾稽 D-003",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-T3-BUDGET-TOTAL",
        group_id=GROUP_INTERNAL,
        title="支出预算总表：合计 = 基本支出 + 项目支出",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-102"),
        depends_on=("table:BUD_T3",),
        basis="支出总表分项之和须等于合计",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-BASIC-EXPENSE",
        group_id=GROUP_INTERNAL,
        title="基本支出 = 人员经费 + 公用经费",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-243", "V33-117"),
        depends_on=("table:FIN_06",),
        basis="AGENTS.md 决算必查勾稽 D-006",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-DETAIL-ROW",
        group_id=GROUP_INTERNAL,
        title="明细行公式自洽（合计 = 基本 + 项目等）",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("V33-233",), ("BUD-110",)),
        basis="明细行内的列关系必须成立，否则明细不可用",
    ),
    Obligation(
        obligation_id="OBL-INTERNAL-TABLE-TOTAL",
        group_id=GROUP_INTERNAL,
        title="表格合计行与分项之和一致（含续表合并范围）",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-005"),
        basis="合计行须与全部分项（含跨页续表）之和一致",
    ),
    # ---- 表间关系 ----
    Obligation(
        obligation_id="OBL-CROSS-T1-T2",
        group_id=GROUP_CROSS,
        title="收入支出决算总表 与 收入决算表 同口径总额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-200"),
        depends_on=("table:FIN_01", "table:FIN_02"),
        basis="AGENTS.md 决算必查勾稽 D-002",
    ),
    Obligation(
        obligation_id="OBL-CROSS-T1-T3",
        group_id=GROUP_CROSS,
        title="收入支出决算总表 与 支出决算表 同口径总额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-201"),
        depends_on=("table:FIN_01", "table:FIN_03"),
        basis="AGENTS.md 决算必查勾稽 D-003",
    ),
    Obligation(
        obligation_id="OBL-CROSS-T4-T5",
        group_id=GROUP_CROSS,
        title="财政拨款收入支出决算总表 与 一般公共预算财政拨款支出决算表 一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-202"),
        depends_on=("table:FIN_04", "table:FIN_05"),
        basis="AGENTS.md 决算必查勾稽 D-005",
    ),
    Obligation(
        obligation_id="OBL-CROSS-T5-T6",
        group_id=GROUP_CROSS,
        title="一般公共预算支出 与 基本支出明细 一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-203"),
        depends_on=("table:FIN_05", "table:FIN_06"),
        basis="AGENTS.md 决算必查勾稽 D-005/D-006",
    ),
    Obligation(
        obligation_id="OBL-CROSS-T2-T4",
        group_id=GROUP_CROSS,
        title="财政拨款收入 与 财政拨款支出总表 一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-204"),
        depends_on=("table:FIN_02", "table:FIN_04"),
        basis="财政拨款口径的收支总额须互相印证",
    ),
    Obligation(
        obligation_id="OBL-CROSS-FISCAL-TOTAL",
        group_id=GROUP_CROSS,
        title="财政拨款收支总表合计一致性",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-119"),
        depends_on=("table:FIN_04",),
        basis="AGENTS.md 决算必查勾稽 D-004",
    ),
    Obligation(
        obligation_id="OBL-CROSS-T1-IDENTITY",
        group_id=GROUP_CROSS,
        title="收支总表主体识别与口径一致性",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-101"),
        basis="总表须能确认所属主体与口径，否则不可与其他表比对",
    ),
    Obligation(
        obligation_id="OBL-CROSS-BUDGET-TABLES",
        group_id=GROUP_CROSS,
        title="预算表间同口径总额一致（T1/T3/T5/T8 交叉）",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-105"),
        depends_on=("table:BUD_T1", "table:BUD_T3", "table:BUD_T5", "table:BUD_T8"),
        basis="预算各表同口径总额须一致",
    ),
    Obligation(
        obligation_id="OBL-CROSS-SAN-GONG-ECON",
        group_id=GROUP_CROSS,
        title="三公经费表 与 财政拨款经济分类表 资金来源一致性",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-CROSS-SAN-GONG-ECON"),
        depends_on=("table:FIN_06", "table:FIN_07"),
        basis=(
            "基本支出（FIN_06《一般公共预算财政拨款基本支出决算表》经济分类）是财政拨款"
            "“三公”经费（FIN_07《财政拨款“三公”经费支出决算表》，含项目支出）的组成部分，"
            "逐业务项须满足「基本支出分项 ≤ 三公经费决算数」——反向即两表口径不能同时成立。"
            "真值：2026-09-16 人工判定 Y02（宜川路街道 2025 年度决算 P22/P24，"
            "出国 13.24>0、接待 8.31>0.30、车辆运行 81.11>19.56 万元）曾漏报"
        ),
    ),
    # ---- 表文关系 ----
    Obligation(
        obligation_id="OBL-TXT-T1",
        group_id=GROUP_TABLE_TEXT,
        title="收支总表 与 收支总体情况说明 金额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-102", "V33-225"),
        depends_on=("table:FIN_01",),
        basis="AGENTS.md 决算公开核心表与说明配对",
    ),
    Obligation(
        obligation_id="OBL-TXT-T2",
        group_id=GROUP_TABLE_TEXT,
        title="收入决算表 与 收入决算情况说明 金额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-103", "V33-226"),
        depends_on=("table:FIN_02",),
        basis="AGENTS.md 决算必查勾稽 D-002",
    ),
    Obligation(
        obligation_id="OBL-TXT-T3",
        group_id=GROUP_TABLE_TEXT,
        title="支出决算表 与 支出决算情况说明 金额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-104", "V33-220"),
        depends_on=("table:FIN_03",),
        basis="AGENTS.md 决算必查勾稽 D-003",
    ),
    Obligation(
        obligation_id="OBL-TXT-T4",
        group_id=GROUP_TABLE_TEXT,
        title="财政拨款收支总表 与 对应说明一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-105", "V33-221", "V33-242"),
        depends_on=("table:FIN_04",),
        basis="AGENTS.md 决算必查勾稽 D-004",
    ),
    Obligation(
        obligation_id="OBL-TXT-T5",
        group_id=GROUP_TABLE_TEXT,
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-106", "V33-222", "V33-227"),
        depends_on=("table:FIN_05",),
        basis="AGENTS.md 决算必查勾稽 D-005",
    ),
    Obligation(
        obligation_id="OBL-TXT-T6",
        group_id=GROUP_TABLE_TEXT,
        title="基本支出决算表 与 基本支出情况说明一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-107", "V33-223"),
        depends_on=("table:FIN_06",),
        basis="AGENTS.md 决算必查勾稽 D-006",
    ),
    Obligation(
        obligation_id="OBL-TXT-T7",
        group_id=GROUP_TABLE_TEXT,
        title="三公经费支出决算表 与 三公说明一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-108", "V33-224"),
        depends_on=("table:FIN_07",),
        basis="AGENTS.md 决算必查勾稽 D-007",
    ),
    Obligation(
        obligation_id="OBL-TXT-BUDGET",
        group_id=GROUP_TABLE_TEXT,
        title="预算表与预算编制说明文字数字一致",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-107"),
        basis="预算编制说明中的金额须与对应表格一致",
    ),
    Obligation(
        obligation_id="OBL-TXT-BUDGET-NAME",
        group_id=GROUP_TABLE_TEXT,
        title="预算编制说明类款项名称与功能分类表一致",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-109"),
        depends_on=("table:BUD_T5",),
        basis="说明中的类款项名称须与功能分类表一致",
    ),
    Obligation(
        obligation_id="OBL-TXT-FUND-DETAIL",
        group_id=GROUP_TABLE_TEXT,
        title="政府性基金/国有资本经营表 与 对应说明逐项一致",
        report_kinds=_FINAL_ONLY,
        # 本轮样张真实漏报：基金表金额（105）与说明金额（135）不一致未被报告。
        # 现有实现只做单点总额比对，未做逐项比对。
        pending_checkers=("V33-TXT-FUND-DETAIL",),
        depends_on=("table:FIN_08", "table:FIN_09"),
        basis="AGENTS.md 决算必查勾稽 D-008/D-009：基金与国资表须与说明一致",
        gap_note="缺少基金表/国资表与对应说明之间的逐项金额比对",
    ),
    # ---- 文内关系 ----
    Obligation(
        obligation_id="OBL-NARRATIVE-AMOUNT",
        group_id=GROUP_NARRATIVE,
        title="文字说明中的金额与占比可复算",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-235", "V33-234", "V33-232"),
        basis="说明中的金额、占比须能由表格数据复算得出",
    ),
    Obligation(
        obligation_id="OBL-NARRATIVE-BUDGET-AMOUNT",
        group_id=GROUP_NARRATIVE,
        title="预算说明中的金额与占比可复算",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-112", "BUD-111"),
        basis="预算编制说明中的金额与同比须能与表格复算一致",
    ),
    Obligation(
        obligation_id="OBL-NARRATIVE-CODE-NAME",
        group_id=GROUP_NARRATIVE,
        title="同一科目编码的名称在文内各处一致",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("CMM-004",), ("CMM-004",)),
        basis="同一编码在文内多处出现时名称与金额须一致",
    ),
    Obligation(
        obligation_id="OBL-NARRATIVE-INDICATOR-REPEAT",
        group_id=GROUP_NARRATIVE,
        title="同段/跨段同一指标重复披露的一致性",
        report_kinds=_FINAL_ONLY,
        # 本轮样张真实漏报：职业年金 257.14 与 245.53 在同一材料内两处披露不一致。
        # 现有实现只比较"说明 vs 表格"，不比较"文内两处披露"。
        pending_checkers=("V33-NARRATIVE-INDICATOR-REPEAT",),
        basis="同一指标在同一材料内多处披露时必须一致",
        gap_note="缺少文内（同段/跨段）同一指标重复披露的一致性检查",
    ),
    Obligation(
        obligation_id="OBL-NARRATIVE-TOC",
        group_id=GROUP_STRUCTURE,
        title="目录与正文表数一致",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("CMM-003"),
        evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
        basis="目录所列表数须与正文实际表数一致",
    ),
    # ---- 比例与变动 ----
    Obligation(
        obligation_id="OBL-TREND-DIRECTION",
        group_id=GROUP_TREND,
        title="同比方向与增减表述一致性",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("CMM-006", "V33-245"), ("CMM-006",)),
        # basis 与 checker 实际能力对齐（独立验收 2026-09-17：不得用规则
        # 名或规则存在证明语义覆盖）：CMM-006 查同页收入/支出方向矛盾，
        # V33-245 查三公经费说明内增减方向与"持平"表述的矛盾。
        basis="同页收入/支出同比方向不得互相矛盾；三公经费说明内增减方向与持平表述不得矛盾",
    ),
    Obligation(
        obligation_id="OBL-TREND-COMPARATIVE-LOGIC",
        group_id=GROUP_TREND,
        title="同比表述异常（模板残留、增减原因缺失、零值口径矛盾、预决算方向矛盾）",
        report_kinds=_BOTH,
        checkers_by_kind=_both(("CMM-005",), ("CMM-005",)),
        # basis 收窄到 CMM-005 已实现的三类子检查 + 预决算方向比对。
        # 原版 basis 声称的"零基数不得表述为增长百分比"需要基期/本期
        # 金额复算，CMM-005 并未实现——该子能力拆到 OBL-TREND-ZERO-BASE。
        basis=(
            "“增加（减少）”等模板残留须报告；“主要原因是”后不得缺失说明；"
            "当前金额为0却写“比上年增加”属口径矛盾；预算数与决算数的增减方向须与金额一致"
        ),
    ),
    Obligation(
        obligation_id="OBL-TREND-ZERO-BASE",
        group_id=GROUP_TREND,
        title="同比基期/本期/增减额与增长百分比的确定性复算（零基数）",
        report_kinds=_BOTH,
        # 样张真实漏报（独立验收 2026-09-17 反例）：宜川接待费 0.30 万元、
        # 增加 0.30 万元，文旅接待费 0.40 万元、增加 0.40 万元，两份材料
        # 都写“增长100%”——本期等于增加额说明基期为 0，此时不存在可比
        # 增长率。CMM-005 只查“当前为0却写增加”，覆盖不了这一类。
        pending_checkers=("CMM-007",),
        basis=(
            "本期金额等于增加金额时基期为0，不得表述为“增长X%”；"
            "增减额与增长百分比须可由基期/本期金额复算"
        ),
        gap_note=(
            "缺少“基期=本期-增减额”的复算检查：本期=增加额（基期为0）"
            "却写“增长100%”的表述未报告（样张：宜川 0.30/0.30、文旅 0.40/0.40）"
        ),
    ),
    Obligation(
        obligation_id="OBL-TREND-COMPLETION-RATE",
        group_id=GROUP_TREND,
        title="预算完成率分母口径与复算",
        report_kinds=_FINAL_ONLY,
        # AGENTS.md R004：年初预算为 0 却写"完成年初预算的 X%"属明显口径错误。
        pending_checkers=("V33-TREND-COMPLETION-RATE",),
        basis="预算完成率须明确分母口径，且分母为 0 时不得给出完成率",
        gap_note="缺少预算完成率的分母口径校验与复算（R004 类缺陷）",
    ),
    # ---- 披露与表达 ----
    Obligation(
        obligation_id="OBL-DISCLOSURE-PERCENT-UNIT",
        group_id=GROUP_DISCLOSURE,
        title="百分比写法完整（不得漏百分号）",
        report_kinds=_BOTH,
        # 本轮样张真实漏报："占76.23"缺少百分号未被报告。
        pending_checkers=("V33-DISCLOSURE-PERCENT-UNIT",),
        basis="占比表述必须带百分号，否则口径不明",
        gap_note="缺少'占 XX.XX'漏百分号等百分比写法完整性检查",
    ),
    Obligation(
        obligation_id="OBL-SG-DISCLOSURE",
        group_id=GROUP_SAN_GONG,
        title="三公经费范围披露完整（含国内接待费等子项）",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-246"),
        basis="三公经费披露须覆盖因公出国（境）费、公务用车、公务接待费等子项",
    ),
    # ---- 三公经费（拆成六个实例，避免"一条完成"掩盖其余） ----
    Obligation(
        obligation_id="OBL-SG-TOTAL",
        group_id=GROUP_SAN_GONG,
        title="三公经费合计 = 三项分项之和",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-121", "V33-244"),
        depends_on=("table:FIN_07",),
        basis="AGENTS.md 决算必查勾稽 D-007 / 规则 C-001",
    ),
    Obligation(
        obligation_id="OBL-SG-ITEMS",
        group_id=GROUP_SAN_GONG,
        title="三公经费三项分项齐备（出国费、公车购置及运行费、接待费）",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-244"),
        depends_on=("table:FIN_07",),
        basis="三公经费须逐项列出，不得合并披露",
    ),
    Obligation(
        obligation_id="OBL-SG-COMPLETION",
        group_id=GROUP_SAN_GONG,
        title="三公经费预算数与决算数对比及说明",
        report_kinds=_FINAL_ONLY,
        pending_checkers=("V33-SG-COMPLETION",),
        depends_on=("table:FIN_07",),
        basis="AGENTS.md 决算必查勾稽 D-007：预算数与决算数对比须与说明一致",
        gap_note="缺少三公经费预算数/决算数对比与说明一致性检查",
    ),
    Obligation(
        obligation_id="OBL-SG-YOY",
        group_id=GROUP_SAN_GONG,
        title="三公经费同比变化方向与原因",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-245"),
        basis="AGENTS.md 决算公开变化原因检查：出现增减须写明原因",
    ),
    Obligation(
        obligation_id="OBL-SG-TABLE-TEXT",
        group_id=GROUP_SAN_GONG,
        title="三公经费表 与 三公说明 金额一致",
        report_kinds=_FINAL_ONLY,
        checkers_by_kind=_final("V33-108", "V33-224"),
        depends_on=("table:FIN_07",),
        basis="AGENTS.md 表-说明配对：三公经费支出决算表对应七、三公经费说明",
    ),
    Obligation(
        obligation_id="OBL-SG-BUDGET-TABLE",
        group_id=GROUP_SAN_GONG,
        title="预算三公经费表公式与说明一致",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-104", "CMM-001"),
        depends_on=("table:BUD_T9",),
        basis="AGENTS.md 预算必查勾稽 C-001；九表口径 T9 为三公经费表",
    ),
    # ---- 绩效 ----
    Obligation(
        obligation_id="OBL-PERF-TARGET",
        group_id=GROUP_PERFORMANCE,
        title="绩效目标披露与项目支出说明衔接",
        report_kinds=_BUDGET_ONLY,
        checkers_by_kind=_budget("BUD-108"),
        basis="AGENTS.md 预算公开规则：绩效目标说明或部门整体/重点项目绩效目标须存在",
    ),
    Obligation(
        obligation_id="OBL-PERF-PHASE-AMOUNT",
        group_id=GROUP_PERFORMANCE,
        title="绩效阶段金额与项目集合口径说明",
        report_kinds=_BUDGET_ONLY,
        # plan §4：项目数相同不代表项目集合、预算版本相同，缺依据时只能要求解释。
        pending_checkers=("BUD-PERF-PHASE-AMOUNT",),
        basis="绩效阶段金额与项目集合口径不一致时须给出解释，不得直接判等",
        gap_note="缺少绩效阶段金额与项目集合/预算版本口径的说明性校验",
    ),
    # ---- AI 语义义务（候选提出，不作确定性结论） ----
    Obligation(
        obligation_id="OBL-AI-SEMANTIC-REVIEW",
        group_id=GROUP_DISCLOSURE,
        title="AI 语义复核：隐含披露缺失、异常段落与口径差异线索",
        report_kinds=_BOTH,
        requires_ai=True,
        evidence_kind=EVIDENCE_LOCATABLE,
        basis="plan §5：AI 补充语义与视觉能力，候选须经确定性复算或原文回查",
    ),
)

#: 三公经费实例组。plan §3 点名要求"核验三公经费"不能只记一条完成，
#: 必须拆成 合计/分项/预算完成率/同比/表文一致性/披露要素 六个实例。
#: 下面显式列出这六条，而不是"取分组内全部"——分组里还有预算专属的三公表
#: 义务，用分组代替"六个实例"会让断言随清单增删而悄悄失效。
SAN_GONG_CHECK_OBLIGATION_IDS: Tuple[str, ...] = (
    "OBL-SG-TOTAL",
    "OBL-SG-ITEMS",
    "OBL-SG-COMPLETION",
    "OBL-SG-YOY",
    "OBL-SG-TABLE-TEXT",
    "OBL-SG-DISCLOSURE",
)

#: 三公经费分组下的全部义务（含预算口径的三公表义务）。
SAN_GONG_OBLIGATION_IDS: Tuple[str, ...] = tuple(
    item.obligation_id for item in OBLIGATION_CATALOG if item.group_id == GROUP_SAN_GONG
)


def catalog_fingerprint() -> str:
    """清单内容指纹（sha256，前 16 位）。

    只覆盖会影响判定的字段：编号、分组、适用文种、checker、缺口、
    AI 依赖、证据要求、依赖输入与依据文本。这样指纹能在"改了什么"层面
    回答问题——两条结论若指纹不同，就一定不是同一版检查要求；
    改注释、改措辞顺序不会让指纹漂移。
    """
    payload = [
        {
            "id": item.obligation_id,
            "group": item.group_id,
            "kinds": list(item.report_kinds),
            "levels": list(item.subject_levels),
            "checkers": {key: list(value) for key, value in sorted(item.checkers_by_kind.items())},
            "pending": list(item.pending_checkers),
            "ai": item.requires_ai,
            "evidence": item.evidence_kind,
            "depends_on": list(item.depends_on),
            "basis": item.basis,
            "gap_note": item.gap_note,
        }
        for item in sorted(OBLIGATION_CATALOG, key=lambda entry: entry.obligation_id)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def catalog_gaps() -> List[Dict[str, str]]:
    """清单中声明为"尚无实现"的检查要求（发布门槛里必须逐条交代的清单）。"""
    return [
        {
            "obligation_id": item.obligation_id,
            "group_id": item.group_id,
            "group_title": OBLIGATION_GROUP_TITLES.get(item.group_id, item.group_id),
            "title": item.title,
            "pending_checkers": "/".join(item.pending_checkers),
            "report_kinds": ",".join(item.report_kinds),
            "basis": item.basis,
            "gap_note": item.gap_note,
        }
        for item in OBLIGATION_CATALOG
        if item.pending_checkers
    ]


def validate_catalog() -> List[str]:
    """自检清单一致性，返回问题描述列表（空列表表示通过）。

    检查的是"清单改错了会静默失真"的几处：编号重复、缺少依据、
    某个适用文种下没有任何 checker（那会让该文种永远报"未实现"）。
    由测试调用，保证清单编辑不会悄悄破坏账本语义。
    """
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for item in OBLIGATION_CATALOG:
        seen[item.obligation_id] = seen.get(item.obligation_id, 0) + 1
        if not item.basis:
            problems.append(f"{item.obligation_id}: 缺少适用依据 basis")
        if item.group_id not in OBLIGATION_GROUP_TITLES:
            problems.append(f"{item.obligation_id}: 未知分组 {item.group_id}")
        if item.pending_checkers and not item.gap_note:
            problems.append(f"{item.obligation_id}: 声明了未实现 checker 但缺少 gap_note")
        for kind in item.report_kinds:
            if item.requires_ai:
                # AI 类义务按设计没有规则 checker，其执行状态由 AI 状态机判定。
                continue
            has_codes = bool(item.checkers_for(kind)) or bool(item.pending_checkers)
            if not has_codes:
                problems.append(
                    f"{item.obligation_id}: 文种 {kind} 下没有任何 checker，"
                    "会让该文种永远报未实现"
                )
    for obligation_id, count in seen.items():
        if count > 1:
            problems.append(f"{obligation_id}: 编号重复 {count} 次")
    return problems


@dataclass
class ObligationInstance:
    """一个义务在一份材料上的展开实例（含适用性判定与执行状态）。"""

    obligation_id: str
    group_id: str
    group_title: str
    title: str
    status: str
    reason: Optional[str] = None
    detail: str = ""
    checkers: Tuple[str, ...] = ()
    missing_checkers: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()
    input_gaps: Tuple[str, ...] = ()
    evidence_kind: str = EVIDENCE_LOCATABLE
    basis: str = ""
    gap_note: str = ""
    requires_ai: bool = False
    blocks_gate: bool = True

    @property
    def is_completed(self) -> bool:
        return self.status == OBLIGATION_COMPLETED

    @property
    def is_applicable(self) -> bool:
        return self.status != OBLIGATION_NOT_APPLICABLE

    @property
    def is_unresolved(self) -> bool:
        return self.status in UNRESOLVED_OBLIGATION_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "group_id": self.group_id,
            "group_title": self.group_title,
            "title": self.title,
            "status": self.status,
            "reason": self.reason,
            "reason_label": OBLIGATION_REASON_LABELS.get(self.reason or "", None),
            "detail": self.detail,
            "checkers": list(self.checkers),
            "missing_checkers": list(self.missing_checkers),
            "depends_on": list(self.depends_on),
            "input_gaps": list(self.input_gaps),
            "evidence_kind": self.evidence_kind,
            "basis": self.basis,
            "gap_note": self.gap_note,
            "requires_ai": self.requires_ai,
            "blocks_gate": self.blocks_gate,
        }


def _registry_rule_ids(report_kind: str) -> FrozenSet[str]:
    """按文种返回当前已注册的规则编号集合。

    惰性导入：``src/engine/pipeline.py`` 在模块层就导入规则表，
    本模块被 pipeline 的调用方（api/main.py）使用，顶层导入会形成环。
    """
    ids: List[str] = []
    if report_kind == "budget":
        from src.engine.budget_rules import ALL_BUDGET_RULES

        ids = [getattr(rule, "code", "") for rule in ALL_BUDGET_RULES]
    elif report_kind == "final":
        from src.engine.rules_v33 import ALL_RULES

        ids = [getattr(rule, "code", "") for rule in ALL_RULES]
    try:
        from src.engine.common_rules import ALL_COMMON_RULES

        ids = [*ids, *(getattr(rule, "code", "") for rule in ALL_COMMON_RULES)]
    except Exception:  # pragma: no cover - 规则表缺失时不影响台账结构
        pass
    return frozenset(str(item) for item in ids if item)


def registered_rule_ids(report_kind: str) -> FrozenSet[str]:
    """公开的规则注册表查询（供测试与评测脚本断言"缺口"是真实的）。"""
    return _registry_rule_ids(report_kind)


def _select_obligations(report_kind: str) -> List[Obligation]:
    if report_kind in {"budget", "final"}:
        return [item for item in OBLIGATION_CATALOG if report_kind in item.report_kinds]
    return []


def _ai_state(
    ai_execution: Optional[Mapping[str, Any]],
    ai_required: bool,
) -> Tuple[str, bool]:
    """由 AI 执行状态机推导 (实例状态, 是否阻塞门禁)。

    只有 ``succeeded`` 算完成。未提供 ai_execution 时按"未执行"处理——
    这与 ``api/main.py`` 既有的 ai_not_run 口径一致：没有执行凭证就不算跑过。
    """
    state = ""
    if isinstance(ai_execution, Mapping):
        state = str(ai_execution.get("state") or ai_execution.get("status") or "").strip()
    if state == "succeeded":
        return OBLIGATION_COMPLETED, True
    status = OBLIGATION_AI_FAILED if state in {"failed", "degraded"} else OBLIGATION_AI_NOT_RUN
    return status, ai_required


def expand_obligations(
    profile: Optional[DocumentProfile],
    *,
    report_kind: Optional[str] = None,
) -> Tuple[List[ObligationInstance], List[str]]:
    """按画像展开义务实例，返回 (实例列表, 未解决说明)。

    适用性判定只使用**已确认**的维度：文种与主体层级。层级未识别时按
    "保守纳入"处理（而不是排除），因为把无法确认适用性的检查悄悄排除掉，
    正是完成率被绿化最常见的路径。
    """
    kind = str(report_kind or (profile.kind if profile else "") or "unknown").strip().lower()
    notes: List[str] = []
    instances: List[ObligationInstance] = []

    if kind not in {"budget", "final"}:
        # 文种未识别：不能展开专项义务，但也绝不允许出现"0 条义务、完成率 100%"。
        # 这里登记一条显式的未完成实例，把"选不出检查配置"这件事记在账上。
        instances.append(
            ObligationInstance(
                obligation_id="OBL-PROFILE-UNRESOLVED",
                group_id=GROUP_STRUCTURE,
                group_title=OBLIGATION_GROUP_TITLES[GROUP_STRUCTURE],
                title="报告类型识别与检查配置选择",
                status=OBLIGATION_PROFILE_UNRESOLVED,
                reason=OBLIGATION_PROFILE_UNRESOLVED,
                detail="未能识别材料是预算公开还是决算公开，无法选择适用的专项检查配置",
                evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
                basis="检查配置由文种决定，文种未识别时无法声称检查范围完整",
            )
        )
        notes.append("report_kind_unresolved: 该报告类型尚未完整支持")
        return instances, notes

    level = profile.level if profile else "unknown"
    if level == "unknown":
        notes.append(
            "subject_level_unresolved: 主体层级（部门/单位/政府）未识别，"
            "层级专项检查按保守纳入处理，适用性待人工确认"
        )
    if profile is not None and not profile.is_fully_resolved:
        notes.append(f"profile_status={profile.profile_status}")

    # 文种冲突单独记账并阻塞（独立验收 2026-09-17 P1“画像冲突不闭合”）：
    # 有互斥候选时，按优先级取的文种只是“候选选择”，不是“已确认”。
    # 不登记的话，报告里写着“文种冲突，需人工确认”，台账与质量门却
    # 显示检查完整，冲突永远到不了人工面前。
    if profile is not None and profile.has_kind_conflict:
        # 只列与所选文种互斥的候选：与所选同值的落选候选是"同向证据"，列进
        # "其余候选"会把冲突份量和一致份量混在一起，人工反而不容易判断。
        candidates = "、".join(
            f"{item.get('source')}={item.get('value')}"
            for item in profile.report_kind.rejected
            if item.get("value") and str(item.get("value")) != kind
        )
        instances.append(
            ObligationInstance(
                obligation_id="OBL-PROFILE-KIND-CONFLICT",
                group_id=GROUP_STRUCTURE,
                group_title=OBLIGATION_GROUP_TITLES[GROUP_STRUCTURE],
                title="文种冲突人工确认",
                status=OBLIGATION_KIND_CONFLICT,
                reason=OBLIGATION_KIND_CONFLICT,
                detail=(
                    f"同一材料出现互斥文种候选（已取 {kind}），"
                    f"其余候选：{candidates or '见画像 conflicts'}，需人工确认文种后复跑"
                ),
                evidence_kind=EVIDENCE_DOCUMENT_LEVEL,
                basis="文种决定整套专项检查配置；存在互斥候选而未确认时，检查范围不可信",
            )
        )
        notes.append("report_kind_conflict: 文种存在互斥候选，需人工确认")

    for item in _select_obligations(kind):
        if item.subject_levels and level != "unknown" and level not in item.subject_levels:
            instances.append(
                _not_applicable_instance(
                    item,
                    basis_note=f"材料主体层级为 {level}，本项仅适用 {'/'.join(item.subject_levels)}",
                )
            )
            continue
        instances.append(_pending_instance(item, kind=kind))
    return instances, notes


def _not_applicable_instance(item: Obligation, *, basis_note: str) -> ObligationInstance:
    return ObligationInstance(
        obligation_id=item.obligation_id,
        group_id=item.group_id,
        group_title=OBLIGATION_GROUP_TITLES.get(item.group_id, item.group_id),
        title=item.title,
        status=OBLIGATION_NOT_APPLICABLE,
        detail=basis_note,
        checkers=item.all_checkers,
        depends_on=item.depends_on,
        evidence_kind=item.evidence_kind,
        basis=item.basis,
        blocks_gate=False,
    )


def _pending_instance(item: Obligation, *, kind: str) -> ObligationInstance:
    """先建一个待判定实例，状态在 ``build_obligation_ledger`` 中由回执推导。"""
    return ObligationInstance(
        obligation_id=item.obligation_id,
        group_id=item.group_id,
        group_title=OBLIGATION_GROUP_TITLES.get(item.group_id, item.group_id),
        title=item.title,
        status=OBLIGATION_NOT_EXECUTED,
        checkers=item.checkers_for(kind),
        missing_checkers=item.pending_checkers,
        depends_on=item.depends_on,
        evidence_kind=item.evidence_kind,
        basis=item.basis,
        gap_note=item.gap_note,
        requires_ai=item.requires_ai,
    )


def _rule_statuses(rule_execution_summary: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    if not isinstance(rule_execution_summary, Mapping):
        return {}
    raw = rule_execution_summary.get("rule_statuses")
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _input_gaps(
    depends_on: Sequence[str],
    structured_ingest: Optional[Mapping[str, Any]],
) -> List[str]:
    """诊断用输入缺口：只在入库侧**明确报告缺失**时才记一笔。

    没有入库信息就不是"缺失"，只是"无法确认"——把无法确认写成缺失会制造
    假缺口，反过来把缺失写成无法确认会掩盖真缺口，所以这里只认显式信号。
    """
    if not depends_on or not isinstance(structured_ingest, Mapping):
        return []
    review_items = structured_ingest.get("review_items")
    if not isinstance(review_items, list):
        return []
    missing_tables = {
        str(item.get("table_code") or "")
        for item in review_items
        if isinstance(item, Mapping) and str(item.get("type") or "") == "missing_core_table"
    }
    if not missing_tables:
        return []
    gaps: List[str] = []
    for token in depends_on:
        if not token.startswith("table:"):
            continue
        table_code = token.split(":", 1)[1]
        if table_code in missing_tables:
            gaps.append(token)
    return gaps


def _resolve_rule_backed(
    instance: ObligationInstance,
    *,
    registry: FrozenSet[str],
    statuses: Dict[str, str],
) -> None:
    """就地判定规则类义务的状态。"""
    implemented = tuple(item for item in instance.checkers if item in registry)
    # 清单里声明了、但注册表里查不到的编号：既可能是"尚未实现"（pending），
    # 也可能是清单笔误或规则被撤下。两种都要落账，不能静默忽略。
    absent = tuple(item for item in instance.checkers if item not in registry)
    missing = (*absent, *instance.missing_checkers)

    if not implemented:
        instance.status = OBLIGATION_NOT_IMPLEMENTED
        instance.reason = OBLIGATION_NOT_IMPLEMENTED
        instance.missing_checkers = missing
        instance.detail = (
            f"该要求尚无规则实现（{'/'.join(missing)}）"
            if missing
            else "该要求当前没有可执行的检查实现"
        )
        return

    if missing:
        # 部分实现、部分缺失：既不假装完整，也不丢弃已实现部分的问题。
        instance.status = OBLIGATION_NOT_IMPLEMENTED
        instance.reason = OBLIGATION_NOT_IMPLEMENTED
        instance.missing_checkers = missing
        instance.detail = f"部分子检查尚无实现：{'/'.join(missing)}"
        return

    # 逐条核对执行回执：每个必需 checker 都必须有**合法终态**才算完成。
    # 独立验收 2026-09-17 partial_receipt 反例：OBL-TREND-DIRECTION 依赖
    # CMM-006 与 V33-245，只提供 CMM-006=pass 时旧逻辑同样记 completed。
    # 缺执行、缺回执不能虚增完成数——缺哪条就点名哪条。
    missing_receipts = tuple(item for item in implemented if item not in statuses)
    unknown_receipts = tuple(
        item
        for item in implemented
        if item in statuses and statuses[item] not in KNOWN_RULE_STATUSES
    )
    if missing_receipts or unknown_receipts:
        parts: List[str] = []
        if missing_receipts:
            parts.append(f"无执行回执（{'/'.join(missing_receipts)}）")
        if unknown_receipts:
            parts.append(f"回执状态不在六态内（{'/'.join(unknown_receipts)}）")
        instance.status = OBLIGATION_NOT_EXECUTED
        instance.reason = OBLIGATION_NOT_EXECUTED
        instance.detail = (
            f"适用规则未全部拿到合法终态：{'；'.join(parts)}，不能据此认定该检查已完成"
        )
        return

    observed = {item: statuses[item] for item in implemented}

    for rule_status, mapped in _RULE_STATUS_TO_OBLIGATION_REASON.items():
        hit = [item for item in implemented if observed[item] == rule_status]
        if hit:
            instance.status = mapped
            instance.reason = mapped
            instance.detail = f"规则 {'/'.join(hit)} 判定为 {rule_status}"
            return

    if all(observed[item] == "not_applicable" for item in implemented):
        # 规则自己判定不适用：这是有依据的排除，但仍需写明是哪条规则给的依据。
        instance.status = OBLIGATION_NOT_APPLICABLE
        instance.detail = f"规则 {'/'.join(implemented)} 判定本次材料不适用"
        instance.blocks_gate = False
        return

    # 剩下的组合（pass / fail / not_applicable 的混合）说明每条 checker
    # 都执行并到达了合法终态，检查已完成；逐条写明结果，避免"部分
    # 不适用"被读成"整单不适用"而悄悄掉出完成率分母。
    outcome_summary = "、".join(f"{item}={observed[item]}" for item in implemented)
    instance.status = OBLIGATION_COMPLETED
    instance.detail = f"规则已全部执行并到达终态：{outcome_summary}"


def _resolve_ai_backed(
    instance: ObligationInstance,
    *,
    ai_execution: Optional[Mapping[str, Any]],
    ai_required: bool,
) -> None:
    status, blocks = _ai_state(ai_execution, ai_required)
    instance.status = status
    instance.reason = None if status == OBLIGATION_COMPLETED else status
    instance.blocks_gate = blocks
    if status == OBLIGATION_COMPLETED:
        instance.detail = "语义复核已执行"
    elif status == OBLIGATION_AI_NOT_RUN:
        instance.detail = "本次未执行语义复核（未启用或未请求 AI）"
    else:
        instance.detail = "本次语义复核失败，未得出候选结论"


def _build_summary(
    instances: Sequence[ObligationInstance],
    notes: Sequence[str],
) -> Dict[str, Any]:
    applicable = [item for item in instances if item.is_applicable]
    completed = [item for item in applicable if item.is_completed]
    unresolved = [item for item in applicable if item.is_unresolved]
    blocking = [item for item in unresolved if item.blocks_gate]

    by_reason: Dict[str, int] = {}
    for item in unresolved:
        key = item.reason or item.status
        by_reason[key] = by_reason.get(key, 0) + 1

    by_group: List[Dict[str, Any]] = []
    for group_id, group_title in OBLIGATION_GROUP_TITLES.items():
        items = [item for item in instances if item.group_id == group_id]
        if not items:
            continue
        group_applicable = [item for item in items if item.is_applicable]
        group_done = [item for item in group_applicable if item.is_completed]
        by_group.append(
            {
                "group_id": group_id,
                "group_title": group_title,
                "applicable": len(group_applicable),
                "completed": len(group_done),
                "not_applicable": len(items) - len(group_applicable),
                "unresolved": len(
                    [item for item in group_applicable if item.is_unresolved]
                ),
            }
        )

    # 完成率分母为 0 时返回 None：'没有应检查事项'不等于'检查完整'。
    # 这是 evidence_guard 里同一条纪律的延伸，避免 0 分母被算成 100%。
    coverage_rate = round(len(completed) / len(applicable), 4) if applicable else None
    blocking_rate = (
        round(1 - len(blocking) / len(applicable), 4) if applicable else None
    )

    return {
        "catalog_version": OBLIGATION_CATALOG_VERSION,
        # 指纹进摘要：每一份历史结果都能回答"这条结论是按哪版检查要求产生的"。
        # 与 catalog_version 的区别是——版本号靠人记得改，指纹改不掉。
        "catalog_fingerprint": catalog_fingerprint(),
        "applicable_total": len(applicable),
        "completed_total": len(completed),
        "not_applicable_total": len(instances) - len(applicable),
        "unresolved_total": len(unresolved),
        "blocking_total": len(blocking),
        "coverage_rate": coverage_rate,
        "auto_completion_rate": blocking_rate,
        "by_reason": by_reason,
        "by_reason_labels": {
            key: OBLIGATION_REASON_LABELS.get(key, key) for key in by_reason
        },
        "by_group": by_group,
        "blocking_obligation_ids": [item.obligation_id for item in blocking],
        "notes": list(notes),
        "instances": [item.to_dict() for item in instances],
    }


def build_obligation_ledger(
    profile: Optional[DocumentProfile],
    *,
    report_kind: Optional[str] = None,
    rule_execution_summary: Optional[Mapping[str, Any]] = None,
    ai_execution: Optional[Mapping[str, Any]] = None,
    ai_required: bool = False,
    structured_ingest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """构建检查义务账本（应检查事项的逐项状态 + 覆盖摘要）。

    返回结构是**增量**的：新增字段不会改变既有 ``rule_execution_summary``
    的语义，两者同时存在，前者回答"应检查的事情完成了多少"，后者回答
    "调度出去的规则跑成什么样"。
    """
    instances, notes = expand_obligations(profile, report_kind=report_kind)
    kind = str(report_kind or (profile.kind if profile else "") or "unknown").strip().lower()
    registry = _registry_rule_ids(kind)
    statuses = _rule_statuses(rule_execution_summary)

    for instance in instances:
        if instance.requires_ai:
            _resolve_ai_backed(
                instance, ai_execution=ai_execution, ai_required=ai_required
            )
            continue
        if instance.checkers or instance.missing_checkers:
            _resolve_rule_backed(
                instance,
                registry=registry,
                statuses=statuses,
            )
        instance.input_gaps = tuple(
            _input_gaps(instance.depends_on, structured_ingest)
        )

    return _build_summary(instances, notes)


def obligation_ids_for_rule(rule_id: Any) -> List[str]:
    """规则编号 -> 关联义务编号。供 finding 标注业务分组标识用。

    ``rule_id`` 大小写与空白先归一，避免 "v33-121" 这类写法漏挂。
    """
    token = str(rule_id or "").strip().upper()
    if not token:
        return []
    return [
        item.obligation_id
        for item in OBLIGATION_CATALOG
        if token in {code.upper() for code in item.all_checkers}
    ]


def attach_obligation_ids(result: Mapping[str, Any]) -> int:
    """就地给结果中的 finding 标注关联义务编号，返回被标注的条数。

    复用 evidence_guard 的遍历口径（双模式 + legacy 分桶），保证两条路径
    标注到同一批对象上。
    """
    from src.services.evidence_guard import iter_findings

    attached = 0
    for finding in iter_findings(result):
        rule_id = finding.get("rule_id") or finding.get("rule")
        ids = obligation_ids_for_rule(rule_id)
        if not ids:
            continue
        existing = finding.get("obligation_ids")
        merged = list(existing) if isinstance(existing, list) else []
        for item in ids:
            if item not in merged:
                merged.append(item)
        finding["obligation_ids"] = merged
        attached += 1
    return attached
