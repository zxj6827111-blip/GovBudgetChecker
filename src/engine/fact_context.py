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
        usable.sort(key=lambda f: (f.recognition_status != "verified", f.confidence or 0.0), reverse=True)
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

    行号按片段顺序重排，单元格保留各自物理页与 bbox（证据可回溯到原始页）。
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
                dataclasses.replace(cell, row_idx=base + cell.row_idx)
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

    def to_record(self) -> Dict[str, Any]:
        return {
            "fact_uid": self.fact_uid,
            "normalized_value": str(self.normalized_value),
            "raw_value": self.raw_value,
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

            normalized = normalize_amount_to_wan(overlay.raw_value, target.unit)
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

    兼容 corrections API 的 change 结构：``{"target": "fact", "fact_uid": ...,
    "normalized_value": ...}``。其他目标（table_instance/column_mapping）不是
    改值，跳过。
    """
    overlays: List[FactOverlay] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        reason = str(record.get("reason") or "")
        actor = str(record.get("actor") or "")
        basis_token = str(record.get("analysis_basis_token") or "")

        # corrections API 结构：record.changes = [{target_type, target_id, field, value}]
        changes = record.get("changes")
        candidates: List[Dict[str, Any]] = []
        if isinstance(changes, list):
            candidates.extend(item for item in changes if isinstance(item, dict))
        else:
            change = record.get("change") if isinstance(record.get("change"), dict) else record
            candidates.append(change)

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
            field = str(change.get("field") or "").strip()
            value = change.get("value")
            overlay = FactOverlay(fact_uid=fact_uid, reason=reason, corrected_by=actor, basis_token=basis_token)
            if field == "raw_value" and isinstance(value, str) and value.strip():
                overlay.raw_value = value.strip()
            else:
                # 显式归一值校正（corrected_value/normalized_value 字段）
                raw = change.get("normalized_value", change.get("corrected_value", value if field in {"", "amount"} else None))
                if raw is None:
                    continue
                try:
                    overlay.normalized_value = Decimal(str(raw))
                except Exception:
                    continue
            overlays.append(overlay)
    return overlays


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
