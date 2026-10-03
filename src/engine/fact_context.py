"""统一财政事实上下文（ZCode 整改 AC-01/AC-10）。

这是"规则执行前生成统一事实"的落点：主链路在规则执行前从同一次解析
产生的 :class:`ParseArtifact` 物化 :class:`FiscalFact`，包装成
:class:`FactContext` 挂到规则 Document 上。规则、AI 上下文、证据与
结构化入库消费同一版本的事实；人工校正通过覆盖层产生新的事实版本，
重算后规则输出随之变化。

纪律（验收 AC-01 再次验收条件）：

- 事实构建是纯函数，不访问数据库——无库环境同样完成事实审校。
- ``recognition_status`` 为 ``ambiguous`` / ``low_confidence`` / ``missing``
  的事实不能作为确定操作数：引用它们的关系必须转未完成，不得判 pass/fail。
- ``unverified`` 表示"解析层已提取、尚无人工复核"：允许作为常规操作数
  参与勾稽（这是原生文本层材料的正常路径），但由此产生的 finding 仍受
  证据门约束；"已验证"只能由人工校正或真实核验写入。
- 事实身份包含主体/年度/期间/资金/阶段/指标/科目/表实例；金额不参与身份。
  校正覆盖层只改值，不改身份。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.schemas.parse_artifact import EvidenceRef, FiscalFact, ParseArtifact

#: 不能作为确定操作数的识别状态（AC-01：引用即转未完成）
NON_OPERAND_STATUSES = frozenset({"ambiguous", "low_confidence", "missing"})
#: 可作为确定操作数的状态（unverified = 解析层事实，证据门另行把关）
OPERAND_STATUSES = frozenset({"unverified", "verified"})

FACT_CONTEXT_VERSION = "fact-context-v1"

#: 总计角色标记：subject_code 语义值或行科目名中的合计/总计词（RC-01：
#: 总计必须由行角色唯一确定，不得取首个同指标事实）。
TOTAL_ROLE_SUBJECT_CODES = frozenset({"total", "总计", "合计"})
_TOTAL_ROLE_NAME_TOKENS = ("合计", "总计")


def is_total_role_fact(fact: Any) -> bool:
    """该事实是否占据"总计/合计"行角色（行角色判定，与金额无关）。"""
    if fact is None:
        return False
    subject_code = str(getattr(fact, "subject_code", None) or "").strip()
    if subject_code and (
        subject_code in TOTAL_ROLE_SUBJECT_CODES or subject_code.lower() == "total"
    ):
        return True
    subject_name = str(getattr(fact, "subject_name", None) or "").strip()
    if not subject_name:
        return False
    return any(token in subject_name for token in _TOTAL_ROLE_NAME_TOKENS)


@dataclass(frozen=True)
class OperandResolution:
    """一次事实取数的完整裁决（RC-01：歧义/低置信不得静默变确定值）。

    - ``ok``：唯一确定操作数，``value``/``fact`` 可用；
    - ``blocked``：候选存在但不可作确定操作数（ambiguous/low_confidence/
      missing，或同指标多个值不同的总计候选无法消歧）——调用方必须转
      未完成，**不得回退旧表值**；
    - ``missing``：上下文中没有该指标的任何候选（调用方可走 legacy 路径，
      但回执须如实标注 fact_migration_status）。
    """

    status: str
    value: Optional[Decimal] = None
    fact: Optional[FiscalFact] = None
    reason: Optional[str] = None
    candidate_uids: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _fact_matches_year_stage(
    fact: FiscalFact,
    *,
    report_year: Optional[int],
    budget_stages: Sequence[str],
) -> bool:
    """年度/阶段口径过滤：与本年口径冲突的事实不是本年操作数（AC-03）。"""
    fact_year = getattr(fact, "fiscal_year", None)
    if (
        report_year is not None
        and fact_year is not None
        and int(fact_year) != int(report_year)
    ):
        return False
    if budget_stages:
        fact_stage = str(getattr(fact, "budget_stage", None) or "").strip()
        if fact_stage and not any(stage in fact_stage for stage in budget_stages):
            return False
    return True


def resolve_operand(
    context: Optional["FactContext"],
    indicator: str,
    *,
    table_code: Optional[str] = None,
    subject_code: Optional[str] = None,
    subject_tokens: Sequence[str] = (),
    report_year: Optional[int] = None,
    budget_stages: Sequence[str] = (),
) -> OperandResolution:
    """从事实上下文解析一个确定操作数（角色唯一化 + 歧义拒绝）。

    选择规则（RC-01 再次验收）：
    1. 候选 = 指标（+表种/科目/年度/阶段口径）匹配的事实；
    2. 只有 ``unverified``/``verified`` 可作操作数，其余候选使裁决 blocked；
    3. 存在总计角色事实时只从总计角色中选；多个总计候选值不同 → blocked
       （歧义）；无总计角色且多个可用候选值不同 → blocked；
    4. 同值多候选不歧义，按 verified > 总计角色 > 层级 > 置信度取一。
    """
    if context is None:
        return OperandResolution(status="missing", reason="fact context absent")
    candidates: List[FiscalFact] = []
    for fact in context.facts:
        if indicator and fact.indicator != indicator:
            continue
        if table_code and fact.table_code != table_code:
            continue
        if subject_code and fact.subject_code != subject_code:
            continue
        if subject_tokens:
            name = str(fact.subject_name or "").strip()
            if not any(token in name for token in subject_tokens):
                continue
        if not _fact_matches_year_stage(
            fact, report_year=report_year, budget_stages=budget_stages
        ):
            continue
        candidates.append(fact)
    if not candidates:
        return OperandResolution(status="missing", reason="no candidate fact")

    blocked = [f for f in candidates if f.recognition_status not in OPERAND_STATUSES]
    usable = [f for f in candidates if f.recognition_status in OPERAND_STATUSES]
    if not usable:
        first = blocked[0]
        uids = ",".join(f.fact_uid for f in blocked[:4])
        return OperandResolution(
            status="blocked",
            fact=first,
            reason=(
                f"候选事实识别状态不可作操作数（{first.recognition_status}）：{uids}"
            ),
            candidate_uids=tuple(f.fact_uid for f in candidates),
        )

    totals = [f for f in usable if is_total_role_fact(f)]
    pool = totals if totals else usable
    distinct_values = {str(f.normalized_value) for f in pool if f.normalized_value is not None}
    if len(pool) > 1 and len(distinct_values) > 1:
        detail = ", ".join(
            f"{f.fact_uid}={f.normalized_value}" for f in pool[:4]
        )
        return OperandResolution(
            status="blocked",
            reason=(
                "同指标多个候选值不同且无法唯一确定总计角色，须人工消歧：" + detail
            ),
            candidate_uids=tuple(f.fact_uid for f in candidates),
        )
    pool.sort(
        key=lambda f: (
            f.recognition_status != "verified",
            not is_total_role_fact(f),
            -(f.classification_level if f.classification_level is not None else 99),
            -(f.confidence or 0.0),
        )
    )
    top = pool[0]
    return OperandResolution(
        status="ok",
        value=top.normalized_value,
        fact=top,
        candidate_uids=tuple(f.fact_uid for f in candidates),
    )


def _fact_value_token(fact: FiscalFact) -> str:
    """参与版本令牌的值签名：身份 + 归一值 + 识别状态。"""
    normalized = (
        str(fact.normalized_value) if fact.normalized_value is not None else ""
    )
    payload = "|".join(
        str(part)
        for part in (
            fact.fact_uid,
            fact.recognition_status,
            normalized,
            fact.raw_value or "",
        )
    )
    return payload


@dataclass
class FactContext:
    """一次分析的事实快照：事实列表 + 证据索引 + 版本令牌。

    可 pickle（子进程规则链通过 Document 携带）；``to_dict`` 供 JSON 留痕。
    """

    facts: List[FiscalFact]
    file_hash: str = ""
    parse_run_uid: str = ""
    org_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    report_kind: Optional[str] = None
    budget_stage: Optional[str] = None
    #: 已应用的人工校正覆盖记录（审计留痕，随事实版本一起传输）
    overlay_records: List[Dict[str, Any]] = field(default_factory=list)
    version_token: str = ""
    context_version: str = FACT_CONTEXT_VERSION

    def __post_init__(self) -> None:
        if not self.version_token:
            self.version_token = self.compute_version_token()

    # ------------------------------------------------------------------
    # 访问器
    # ------------------------------------------------------------------

    def evidence_refs(self) -> List[Dict[str, Any]]:
        """事实携带的全部证据实体（来自 extra_dims["evidence_refs"]）。"""
        refs: List[Dict[str, Any]] = []
        for fact in self.facts:
            raw = (fact.extra_dims or {}).get("evidence_refs")
            if isinstance(raw, list):
                refs.extend(dict(item) for item in raw if isinstance(item, dict))
        return refs

    def facts_by_indicator(
        self,
        indicator: str,
        *,
        table_code: Optional[str] = None,
        subject_code: Optional[str] = None,
    ) -> List[FiscalFact]:
        return [
            fact
            for fact in self.facts
            if fact.indicator == indicator
            and (table_code is None or fact.table_code == table_code)
            and (subject_code is None or fact.subject_code == subject_code)
        ]

    def fact_by_uid(self, fact_uid: str) -> Optional[FiscalFact]:
        for fact in self.facts:
            if fact.fact_uid == fact_uid:
                return fact
        return None

    def definite_facts(self) -> List[FiscalFact]:
        """可作为确定操作数的事实（排除 ambiguous/low_confidence/missing）。"""
        return [fact for fact in self.facts if fact.recognition_status in OPERAND_STATUSES]

    def non_operand_facts(self) -> List[FiscalFact]:
        return [fact for fact in self.facts if fact.recognition_status in NON_OPERAND_STATUSES]

    def usable_for_rules(self) -> bool:
        """是否有可用的确定操作数（全部事实都被识别风险压住时为 False）。"""
        return bool(self.definite_facts())

    def best_value(
        self,
        indicator: str,
        *,
        table_code: Optional[str] = None,
        subject_code: Optional[str] = None,
    ) -> Tuple[Optional[Decimal], Optional[FiscalFact]]:
        """取某指标的最佳确定操作数。

        返回 (万元归一值, 命中事实)。命中事实为 ``None`` 表示没有可用事实；
        若该指标只存在不可作操作数的事实，返回命中事实但值为 None——
        调用方必须转未完成，不得把 None 当 0。
        """
        candidates = self.facts_by_indicator(
            indicator, table_code=table_code, subject_code=subject_code
        )
        if not candidates:
            return None, None
        usable = [f for f in candidates if f.recognition_status in OPERAND_STATUSES]
        if not usable:
            return None, candidates[0]
        # RC-01：verified 优先于 unverified（识别分数不再反超验证状态），
        # 同状态下总计角色优先、层级更高优先、识别分数只作末位 tie-break。
        usable.sort(
            key=lambda f: (
                f.recognition_status != "verified",
                not is_total_role_fact(f),
                -(f.classification_level or 99),
                -(f.confidence or 0.0),
            )
        )
        top = usable[0]
        return top.normalized_value, top

    def summary(self) -> Dict[str, Any]:
        statuses: Dict[str, int] = {}
        for fact in self.facts:
            statuses[fact.recognition_status] = statuses.get(fact.recognition_status, 0) + 1
        return {
            "context_version": self.context_version,
            "fact_count": len(self.facts),
            "recognition_statuses": statuses,
            "operand_count": len(self.definite_facts()),
            "non_operand_count": len(self.non_operand_facts()),
            "version_token": self.version_token,
            "parse_run_uid": self.parse_run_uid,
            "file_hash": self.file_hash,
            "overlay_count": len(self.overlay_records),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_version": self.context_version,
            "file_hash": self.file_hash,
            "parse_run_uid": self.parse_run_uid,
            "org_name": self.org_name,
            "fiscal_year": self.fiscal_year,
            "report_kind": self.report_kind,
            "budget_stage": self.budget_stage,
            "overlay_records": list(self.overlay_records),
            "version_token": self.version_token,
            "facts": [fact.to_dict() for fact in self.facts],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FactContext":
        return cls(
            facts=[FiscalFact.from_dict(item) for item in data.get("facts") or []],
            file_hash=str(data.get("file_hash") or ""),
            parse_run_uid=str(data.get("parse_run_uid") or ""),
            org_name=data.get("org_name"),
            fiscal_year=data.get("fiscal_year"),
            report_kind=data.get("report_kind"),
            budget_stage=data.get("budget_stage"),
            overlay_records=[dict(item) for item in data.get("overlay_records") or []],
            version_token=str(data.get("version_token") or ""),
        )

    def compute_version_token(self) -> str:
        payload = ";".join(_fact_value_token(fact) for fact in self.facts)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 事实构建（纯函数，与结构化入库共用同一物化实现）
# ---------------------------------------------------------------------------


def build_fact_context_from_artifact(
    artifact: ParseArtifact,
    *,
    org_name: Optional[str] = None,
    fiscal_year: Optional[int] = None,
    report_kind: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> Optional[FactContext]:
    """从统一解析对象构建规则侧事实上下文（不访问数据库）。

    与结构化入库共用 :func:`FiscalFactMaterializer.build_fiscal_facts_for_table`
    和列映射推导——规则与入库消费同一版本的事实（AC-01）。解析对象缺失
    或未识别出任何事实时返回 None，规则链按 legacy 路径运行并如实留痕。
    """
    if artifact is None:
        return None

    from src.services.fiscal_fact_materializer import FiscalFactMaterializer
    from src.services.table_recognizer import TABLE_RECOGNITION_RULES  # noqa: F401  (保证规则表已加载)

    materializer = FiscalFactMaterializer(None)
    budget_stage = {"budget": "年初预算", "final": "决算"}.get(
        str(doc_type or "").strip().lower(), None
    )
    facts: List[FiscalFact] = []
    for instance in artifact.logical_tables:
        fragment = _fragment_for_instance(artifact, instance)
        if fragment is None:
            continue
        mappings = _column_mappings_for_instance(instance, fragment)
        if not mappings:
            continue
        facts.extend(
            materializer.build_fiscal_facts_for_table(
                fragment,
                mappings,
                org_name=instance.org_name or org_name,
                fiscal_year=instance.fiscal_year or fiscal_year,
                period="本年",
                budget_stage=budget_stage,
                instance_uid=instance.instance_uid,
                file_hash=artifact.source_file_hash,
            )
        )

    if not facts:
        return None

    return FactContext(
        facts=facts,
        file_hash=artifact.source_file_hash,
        parse_run_uid=artifact.parse_run_uid,
        org_name=org_name,
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        budget_stage=budget_stage,
    )


def _fragment_for_instance(artifact: ParseArtifact, instance: Any):
    """取逻辑实例的首个片段；续页实例返回跨页合并视图。"""
    fragments = [
        item for item in artifact.table_fragments if item.fragment_uid in set(instance.fragment_uids)
    ]
    if not fragments:
        return None
    if len(fragments) == 1:
        return fragments[0]
    return _combine_fragments(instance, fragments)


def _combine_fragments(instance: Any, fragments: Sequence[Any]):
    """把同一逻辑实例的多个物理片段组合成一个物化视图。

    行号按片段顺序重排；单元格保留各自物理页与 bbox（证据可回溯到原始页），
    并写入 origin_fragment_uid/origin_row_idx——证据格 ID 指回原页原格（RC-02）。
    """
    from src.schemas.parse_artifact import ParsedCell, ParsedTable

    ordered = sorted(fragments, key=lambda item: (item.page_number, item.table_index))
    rows: List[List[str]] = []
    cells: List[ParsedCell] = []
    for fragment in ordered:
        base = len(rows)
        rows.extend([list(row) for row in fragment.rows])
        for cell in fragment.cells:
            cells.append(
                dataclasses.replace(
                    cell,
                    row_idx=base + cell.row_idx,
                    origin_fragment_uid=(
                        cell.origin_fragment_uid or fragment.fragment_uid
                    ),
                    origin_row_idx=(
                        cell.origin_row_idx
                        if cell.origin_row_idx is not None
                        else cell.row_idx
                    ),
                )
            )
    first = ordered[0]
    return ParsedTable(
        table_code=instance.table_code,
        title=instance.title or first.title,
        page_number=first.page_number,
        table_index=first.table_index,
        bbox=first.bbox,
        header_row_count=first.header_row_count,
        confidence=min(item.confidence for item in ordered),
        extraction_method=first.extraction_method,
        unit_hint=first.unit_hint,
        rows=rows,
        cells=cells,
        fragment_uid=instance.instance_uid,
        source_fragment_uids=sorted(
            {*(instance.fragment_uids or []), *(first.source_fragment_uids or [])}
        ),
    )


def _column_mappings_for_instance(instance: Any, fragment: Any) -> List[Dict[str, Any]]:
    """列映射：优先实例自带映射，缺省时按表头别名推导（与入库同源）。"""
    if getattr(instance, "column_mappings", None):
        return [dict(item) for item in instance.column_mappings]
    return _canonical_mappings_for_fragment(fragment, instance.table_code)


def _canonical_mappings_for_fragment(fragment: Any, table_code: str) -> List[Dict[str, Any]]:
    """按表片段表头与九表规则的 measure_aliases 推导列映射（纯函数）。

    与 structured_ingest_runner 同名实现保持同一语义（该实现改为委托这里，
    保证两条路径不会各自演化）。只消费表头行（``header_row_count``）——
    此前固定取前 3 行拼接，会把数据行文本并进表头造成"合计"误命中列 0。
    """
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    rule = NINE_TABLE_RULES.get(table_code)
    if rule is None or not fragment.rows:
        return []

    header_row_count = max(1, min(int(getattr(fragment, "header_row_count", 1) or 1), 3))
    header_rows = [row for row in fragment.rows[:header_row_count] if row]
    if not header_rows:
        return []
    col_count = max(len(row) for row in header_rows)
    headers = [
        "".join(str(row[col]) for row in header_rows if col < len(row)).strip()
        for col in range(col_count)
    ]
    mappings: List[Dict[str, Any]] = []
    for measure, aliases in (rule.measure_aliases or {}).items():
        # 同名列优先精确匹配，再退子串——避免"合计"命中"项目合计说明"类列
        best_col: Optional[int] = None
        best_exact = False
        for col, header_text in enumerate(headers):
            hit = any(alias and str(alias) == header_text for alias in aliases)
            substring = any(alias and str(alias) in header_text for alias in aliases)
            if hit and (best_col is None or not best_exact):
                best_col, best_exact = col, True
            elif substring and best_col is None:
                best_col, best_exact = col, False
        if best_col is not None:
            mappings.append(
                {
                    "source_col_idx": best_col,
                    "canonical_measure": measure,
                    "source_col_name": headers[best_col],
                    "confidence": 0.9,
                }
            )
    return mappings


# ---------------------------------------------------------------------------
# 校正覆盖层（AC-10：人工校正改变事实值 → 新版本 → 规则输出变化）
# ---------------------------------------------------------------------------


@dataclass
class FactOverlay:
    """一条人工校正对事实值的覆盖。

    ``normalized_value``（万元口径）可直接给出，或由 ``raw_value`` 配合
    事实自身单位在应用时换算。事实身份（主体/年度/指标/科目/实例）不可
    通过校正修改——身份错误的事实应走反馈/重解析，不应在旧身份上改值。
    """

    fact_uid: str
    normalized_value: Optional[Decimal] = None
    raw_value: Optional[str] = None
    reason: str = ""
    corrected_by: str = ""
    basis_token: str = ""
    #: 校正声明的口径单位（如"元"）：raw_value 优先按该单位换算（RC-10）。
    unit: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "fact_uid": self.fact_uid,
            "normalized_value": str(self.normalized_value),
            "raw_value": self.raw_value,
            "unit": self.unit,
            "reason": self.reason,
            "corrected_by": self.corrected_by,
            "basis_token": self.basis_token,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FactOverlay":
        normalized_raw = data.get("normalized_value")
        return cls(
            fact_uid=str(data.get("fact_uid") or ""),
            normalized_value=Decimal(str(normalized_raw)) if normalized_raw is not None else None,
            raw_value=data.get("raw_value"),
            unit=(str(data.get("unit")).strip() or None) if data.get("unit") else None,
            reason=str(data.get("reason") or ""),
            corrected_by=str(data.get("corrected_by") or ""),
            basis_token=str(data.get("basis_token") or ""),
        )


def apply_fact_overlay(
    context: FactContext,
    overlays: Sequence[FactOverlay],
) -> FactContext:
    """应用校正覆盖层，返回**新**事实上下文（原上下文不可变）。

    - 只覆盖存在于本版本的事实；目标 fact_uid 不存在时拒绝（``ValueError``），
      防止把校正写到未知对象上（AC-10 再次验收条件）。
    - 被校正事实标记 ``recognition_status="verified"``（人工校正即验证），
      原值保留在 extra_dims 供审计；覆盖记录进入新上下文留痕。
    - 新上下文的 version_token 必然不同——分析版本据此判定事实变化。
    """
    if not overlays:
        return context

    by_uid = {fact.fact_uid: fact for fact in context.facts}
    applied: List[Dict[str, Any]] = []
    new_facts: List[FiscalFact] = []
    for fact in context.facts:
        new_facts.append(dataclasses.replace(fact))

    for overlay in overlays:
        target = by_uid.get(overlay.fact_uid)
        if target is None:
            raise ValueError(f"correction targets unknown fact: {overlay.fact_uid}")
        idx = next(i for i, item in enumerate(new_facts) if item.fact_uid == overlay.fact_uid)
        previous = new_facts[idx]
        normalized = overlay.normalized_value
        if normalized is None and overlay.raw_value is not None:
            from src.engine.amount_math import normalize_amount_to_wan

            # 校正自带口径单位时按校正单位换算；否则用事实自身单位（RC-10）。
            normalized = normalize_amount_to_wan(
                overlay.raw_value, overlay.unit or target.unit
            )
        if normalized is None:
            raise ValueError(
                f"correction for {overlay.fact_uid} carries neither a parsable raw_value nor a normalized_value"
            )
        record = overlay.to_record()
        record["normalized_value"] = str(normalized)
        applied.append(record)
        extra = dict(previous.extra_dims or {})
        pre_correction = extra.setdefault("pre_correction_values", [])
        pre_correction.append(
            {
                "normalized_value": (
                    str(previous.normalized_value)
                    if previous.normalized_value is not None
                    else None
                ),
                "raw_value": previous.raw_value,
                "recognition_status": previous.recognition_status,
            }
        )
        extra["correction"] = record
        # 校正后证据实体保持原位（证据指向原件位置，不因改值而消失），
        # 但验证状态仍如实保留——证据验证与数据修正是两个动作。
        new_facts[idx] = dataclasses.replace(
            previous,
            normalized_value=normalized,
            raw_value=overlay.raw_value if overlay.raw_value is not None else previous.raw_value,
            recognition_status="verified",
            extra_dims=extra,
        )

    return FactContext(
        facts=new_facts,
        file_hash=context.file_hash,
        parse_run_uid=context.parse_run_uid,
        org_name=context.org_name,
        fiscal_year=context.fiscal_year,
        report_kind=context.report_kind,
        budget_stage=context.budget_stage,
        overlay_records=[*context.overlay_records, *applied],
    )


def parse_overlays_from_records(records: Iterable[Dict[str, Any]]) -> List[FactOverlay]:
    """把校正台账（corrections.jsonl）中的事实改值记录解析为覆盖层。

    兼容 corrections API 的 changes 结构：``{"target_type": "fact",
    "target_id": ..., "field": ..., "value": ...}``。同一条记录内指向同一
    事实的多个字段（如 raw_value + unit）合并为**一个**覆盖——单位改了而
    原值不变是合法校正，换算必须按校正后的单位进行（RC-10）。

    身份字段（year/org/scope/indicator）与 column_mapping/manual_location
    不在此解析——它们由 :func:`collect_unsupported_changes` 单独记账，
    主链路据此给出"校正未生效"的明确状态，而不是静默丢弃。
    """
    overlays: List[FactOverlay] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        reason = str(record.get("reason") or "")
        actor = str(record.get("actor") or "")
        basis_token = str(record.get("analysis_basis_token") or "")

        changes = record.get("changes")
        if isinstance(changes, list):
            candidates = [item for item in changes if isinstance(item, dict)]
        else:
            change = record.get("change") if isinstance(record.get("change"), dict) else record
            candidates = [change]

        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for change in candidates:
            target_type = str(change.get("target_type") or change.get("target") or "fact").strip()
            if target_type != "fact":
                continue
            fact_uid = str(
                change.get("target_id")
                or change.get("fact_uid")
                or record.get("fact_uid")
                or ""
            ).strip()
            if not fact_uid:
                continue
            bucket = grouped.setdefault(fact_uid, {})
            if fact_uid not in order:
                order.append(fact_uid)
            field = str(change.get("field") or "").strip()
            value = change.get("value")
            if field == "raw_value":
                if isinstance(value, str) and value.strip():
                    bucket["raw_value"] = value.strip()
            elif field == "unit":
                if isinstance(value, str) and value.strip():
                    bucket["unit"] = value.strip()
            elif field in {"", "amount", "normalized_value", "corrected_value"}:
                raw = change.get("normalized_value", change.get("corrected_value", value))
                if raw is None:
                    continue
                try:
                    bucket["normalized_value"] = Decimal(str(raw))
                except Exception:
                    continue

        for fact_uid in order:
            bucket = grouped[fact_uid]
            overlay = FactOverlay(
                fact_uid=fact_uid, reason=reason, corrected_by=actor, basis_token=basis_token
            )
            if "normalized_value" in bucket:
                overlay.normalized_value = bucket["normalized_value"]
            overlay.raw_value = bucket.get("raw_value")
            overlay.unit = bucket.get("unit")
            if overlay.normalized_value is None and not overlay.raw_value:
                # 该事实的变更里没有任何可换算的值（例如只改了单位）——
                # 不产生静默空覆盖，交给 collect_unsupported_changes 记账。
                continue
            overlays.append(overlay)
    return overlays


#: 覆盖层不消费的 fact 校正字段 →（是否阻断、原因说明）。
#: "阻断"意味着这些校正未生效时，新分析版本必须亮出"校正未完全生效"状态。
UNSUPPORTED_FACT_FIELDS: Dict[str, Dict[str, str]] = {
    "year": {
        "blocking": "true",
        "reason": "年度属于事实身份，覆盖层不改身份；该校正未生效，需通过重新解析或反馈管线处理",
    },
    "org": {
        "blocking": "true",
        "reason": "主体属于事实身份，覆盖层不改身份；该校正未生效，需通过重新解析或反馈管线处理",
    },
    "scope": {
        "blocking": "true",
        "reason": "资金口径属于事实身份，覆盖层不改身份；该校正未生效，需通过重新解析或反馈管线处理",
    },
    "indicator": {
        "blocking": "true",
        "reason": "指标属于事实身份，覆盖层不改身份；该校正未生效，需通过重新解析或反馈管线处理",
    },
    "column_mapping": {
        "blocking": "false",
        "reason": "列映射校正面向表实例管线，不进入事实值覆盖层",
    },
    "manual_location": {
        "blocking": "false",
        "reason": "人工定位由证据重绑定管线消费，不改变事实值",
    },
}


def collect_unsupported_changes(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """收集覆盖层不消费的事实校正字段，供主链路亮出"校正未生效"状态（RC-10）。

    返回条目：``{"target_id", "field", "reason"}``；blocking 条目代表该
    校正在本轮重算中没有生效，主链路必须转待复核，不得当作已处理。
    """
    unsupported: List[Dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        changes = record.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            if str(change.get("target_type") or "fact").strip() != "fact":
                continue
            field = str(change.get("field") or "").strip()
            meta = UNSUPPORTED_FACT_FIELDS.get(field)
            if meta is None:
                continue
            unsupported.append(
                {
                    "target_id": str(change.get("target_id") or ""),
                    "field": field,
                    "blocking": meta["blocking"] == "true",
                    "reason": meta["reason"],
                }
            )
    return unsupported


def remap_overlay_targets(
    old_facts: Iterable[FiscalFact],
    context: "FactContext",
    overlays: Sequence[FactOverlay],
) -> Tuple[List[FactOverlay], List[Dict[str, Any]], int]:
    """把旧解析版本事实上的覆盖目标重映射到当前版本（RC-10 跨解析映射）。

    事实 UID 由片段 UID 组成，解析器升级或内容变化后旧 UID 可能失效。
    重映射按事实身份键（主体/年度/期间/资金/阶段/指标/科目/实例）唯一
    命中时改写目标；0 个或多个命中一律进 unresolved，由主链路转待复核，
    **绝不猜一个目标写值**。

    返回 ``(重映射后覆盖层, 无法落点清单, 实际改写目标的条数)``。
    """
    if not overlays:
        return [], [], 0
    old_by_uid: Dict[str, FiscalFact] = {}
    for item in old_facts or []:
        # 快照往返后是 dict（JSON），原地对象是 FiscalFact——两种形态都接受
        fact = item if isinstance(item, FiscalFact) else FiscalFact.from_dict(item)
        if fact.fact_uid:
            old_by_uid[fact.fact_uid] = fact
    by_identity: Dict[Tuple[Any, ...], List[FiscalFact]] = {}
    for fact in context.facts:
        by_identity.setdefault(fact.identity_key(), []).append(fact)

    remapped: List[FactOverlay] = []
    unresolved: List[Dict[str, Any]] = []
    rewritten = 0
    for overlay in overlays:
        if context.fact_by_uid(overlay.fact_uid) is not None:
            remapped.append(overlay)
            continue
        old_fact = old_by_uid.get(overlay.fact_uid)
        if old_fact is None:
            unresolved.append(
                {
                    "fact_uid": overlay.fact_uid,
                    "reason": "目标不在上一版本事实快照中，无法跨版本映射",
                }
            )
            continue
        matches = by_identity.get(old_fact.identity_key()) or []
        if len(matches) != 1:
            # 放宽一档：实例身份（instance_uid）可能因表格分组变化而漂移
            relaxed = tuple(old_fact.identity_key()[:-1])
            relaxed_map: Dict[Tuple[Any, ...], List[FiscalFact]] = {}
            for fact in context.facts:
                relaxed_map.setdefault(fact.identity_key()[:-1], []).append(fact)
            matches = relaxed_map.get(relaxed) or []
        if len(matches) == 1:
            remapped.append(
                dataclasses.replace(overlay, fact_uid=matches[0].fact_uid)
            )
            rewritten += 1
        else:
            unresolved.append(
                {
                    "fact_uid": overlay.fact_uid,
                    "reason": (
                        f"按身份键找到 {len(matches)} 个候选，无法唯一映射"
                        if matches
                        else "当前版本不存在同身份事实"
                    ),
                }
            )
    return remapped, unresolved, rewritten


def apply_fact_overlays_with_remap(
    context: FactContext,
    overlays: Sequence[FactOverlay],
    *,
    previous_facts: Iterable[FiscalFact] = (),
) -> Tuple[FactContext, Dict[str, Any]]:
    """应用覆盖层；目标失效时先做跨版本重映射，再应用。

    返回 ``(新上下文或原上下文, 应用状态)``。状态字段：

    - ``applied``：成功应用的覆盖条数；
    - ``remapped``：经身份映射改写目标的条数；
    - ``unresolved``：无法安全落点的目标清单（主链路必须转待复核）；
    - ``status``：applied / partial / failed。
    """
    remapped, unresolved, rewritten = remap_overlay_targets(
        previous_facts, context, overlays
    )
    applied_count = 0
    new_context = context
    if remapped:
        try:
            new_context = apply_fact_overlay(context, remapped)
            applied_count = len(remapped)
        except ValueError:
            # 重映射后的目标仍失效（例如身份键本身变化）——如实报失败
            unresolved.extend(
                {"fact_uid": item.fact_uid, "reason": "重映射后目标仍不存在"}
                for item in remapped
                if context.fact_by_uid(item.fact_uid) is None
            )
            remapped = []
    status = "failed" if (not applied_count and unresolved) else (
        "partial" if unresolved else "applied"
    )
    report = {
        "status": status,
        "applied": applied_count,
        "remapped": rewritten,
        "unresolved": unresolved,
    }
    return new_context, report


# ---------------------------------------------------------------------------
# 关系级子检查回执（AC-04：每个适用检查关系一条真实回执）
# ---------------------------------------------------------------------------


def make_fact_receipt(
    rule_id: str,
    sub_check_id: str,
    *,
    operand_fact_ids: Sequence[str] = (),
    required_fact_ids: Sequence[str] = (),
    formula: Optional[str] = None,
    tolerance: Optional[Dict[str, Any]] = None,
    status: str = "pass",
    incomplete_reason: Optional[str] = None,
    issue_refs: Sequence[str] = (),
    obligation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造一条关系级 CheckReceipt（dict 形态，规则直接写入 doc.fact_receipts）。

    容差缺省记录统一政策（v3_3_portable）：绝对 0.5 万元 + 独立舍入包络；
    规则实际使用了其它容差时必须显式传入并说明来源。
    """
    from src.schemas.parse_artifact import CheckReceipt

    receipt = CheckReceipt(
        rule_id=str(rule_id),
        sub_check_id=str(sub_check_id),
        required_fact_ids=[str(item) for item in required_fact_ids],
        operand_fact_ids=[str(item) for item in operand_fact_ids],
        formula=formula,
        tolerance=dict(
            tolerance
            or {
                "configured_amount_tolerance_wan": "0.5",
                "configured_percentage_point_tolerance": "0.1",
                "rounding_envelope_wan": "policy",
                "actual_envelope_source": "fact_policy",
            }
        ),
        status=status,
        incomplete_reason=incomplete_reason,
        issue_refs=[str(item) for item in issue_refs],
        obligation_id=obligation_id,
        evaluation_basis="fiscal_fact",
        fact_migration_status="complete",
    )
    return receipt.to_dict()
