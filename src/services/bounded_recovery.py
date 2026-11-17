"""有界恢复协调：同一失败路径只尝试一次，成功前必须重新验证来源。

本模块不发明新的解析算法；它把既有恢复能力按策略登记，并负责：

- 只对取数/证据绑定类原因尝试恢复，材料缺陷与执行异常不在此重试；
- 策略指纹 + 原件哈希 + parse_run_uid + 规则/关系 去重，同一路径最多一次；
- 一次协调最多 2 个策略，且不增加 AI/OCR 客户端层重试；
- 恢复后重新验证原件哈希与解析身份，记录尝试前后原因、耗时和影响范围；
- 恢复失败保留原回执，不清理 reason_code。

策略实现由调用方注入；默认注册一个基于既有原生网格的“空表影子表”恢复。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: 只有这些原因允许进入恢复；材料缺失/披露问题不自动重试。
RECOVERABLE_REASON_CODES = frozenset({
    "table_mapping_incomplete",
    "table_missing",
    "operand_missing",
    "operand_ambiguous",
    "row_level_unresolved",
    "row_code_missing",
    "column_identity_unconfirmed",
    "column_width_drift",
    "cell_unbound",
    "cell_low_confidence",
    "text_binding_failed",
    "text_statement_unparsed",
})

MAX_STRATEGIES_PER_RUN = 2


@dataclass(frozen=True)
class RecoveryStrategy:
    """一个可复用的恢复策略；``run`` 返回 True 表示文档状态已改变。"""

    name: str
    fingerprint: str
    run: Callable[[Any], bool]


@dataclass
class RecoveryAttempt:
    strategy: str
    fingerprint: str
    relation_key: str
    rule_id: str
    started_at: float
    changed: bool
    source_verified: bool
    elapsed_ms: int
    before_codes: List[str] = field(default_factory=list)
    after_codes: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "fingerprint": self.fingerprint,
            "relation_key": self.relation_key,
            "rule_id": self.rule_id,
            "changed": self.changed,
            "source_verified": self.source_verified,
            "elapsed_ms": self.elapsed_ms,
            "before_codes": list(self.before_codes),
            "after_codes": list(self.after_codes),
            "error": self.error,
        }


def _artifact_identity(document: Any) -> Tuple[str, str]:
    artifact = getattr(document, "parse_artifact", None)
    context = getattr(document, "fact_context", None)
    artifact_hash = str(getattr(artifact, "source_file_hash", "") or "")
    context_hash = str(getattr(context, "file_hash", "") or "")
    artifact_run = str(getattr(artifact, "parse_run_uid", "") or "")
    context_run = str(getattr(context, "parse_run_uid", "") or "")
    file_hash = artifact_hash or context_hash
    parse_run = artifact_run or context_run
    if artifact_hash and context_hash and artifact_hash != context_hash:
        return "", ""
    if artifact_run and context_run and artifact_run != context_run:
        return "", ""
    return file_hash, parse_run


def _strategy_key(
    *, file_hash: str, parse_run: str, rule_id: str, relation_key: str, strategy: RecoveryStrategy
) -> Tuple[str, str, str, str, str]:
    return (file_hash, parse_run, rule_id, relation_key, strategy.fingerprint)


class RecoveryCoordinator:
    """同一次分析内的恢复执行与去重；实例按任务创建，不跨原件复用。"""

    def __init__(self, strategies: Optional[Sequence[RecoveryStrategy]] = None) -> None:
        self.strategies: List[RecoveryStrategy] = list(strategies or default_strategies())
        self._attempted: set[Tuple[str, str, str, str, str]] = set()
        self.records: List[Dict[str, Any]] = []

    def recover(
        self,
        document: Any,
        *,
        rule_id: str,
        reason_codes: Iterable[str],
        relation_key: str = "",
        rerun: Optional[Callable[[], Any]] = None,
    ) -> Optional[Any]:
        """尝试恢复并可选重跑；返回重跑结果（成功恢复时）或 None。"""
        codes = [str(code) for code in reason_codes if str(code).strip()]
        if not codes or not (set(codes) & RECOVERABLE_REASON_CODES):
            return None
        file_hash, parse_run = _artifact_identity(document)
        if not file_hash or not parse_run:
            self.records.append({
                "rule_id": rule_id, "relation_key": relation_key,
                "status": "skipped", "reason": "source_identity_unverified",
                "before_codes": codes,
            })
            return None

        attempts: List[RecoveryAttempt] = []
        for strategy in self.strategies[:MAX_STRATEGIES_PER_RUN]:
            key = _strategy_key(
                file_hash=file_hash, parse_run=parse_run, rule_id=rule_id,
                relation_key=relation_key, strategy=strategy,
            )
            if key in self._attempted:
                continue
            self._attempted.add(key)
            started = time.monotonic()
            changed = False
            error = None
            try:
                changed = bool(strategy.run(document))
            except Exception as exc:  # noqa: BLE001 - 恢复失败保留原回执
                error = f"{type(exc).__name__}: {exc}"
            after_hash, after_run = _artifact_identity(document)
            source_verified = bool(changed and after_hash == file_hash and after_run == parse_run)
            attempt = RecoveryAttempt(
                strategy=strategy.name,
                fingerprint=strategy.fingerprint,
                relation_key=relation_key,
                rule_id=rule_id,
                started_at=started,
                changed=changed,
                source_verified=source_verified,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                before_codes=codes,
                error=error,
            )
            attempts.append(attempt)
            if not changed:
                continue
            if not source_verified:
                attempt.error = attempt.error or "source_identity_changed_after_recovery"
                continue
            if rerun is None:
                self._record(rule_id, relation_key, "changed", attempts)
                return True
            try:
                result = rerun()
            except BaseException as exc:  # noqa: BLE001 - 恢复后的旧失败保留
                attempt.error = f"rerun_{type(exc).__name__}: {exc}"
                continue
            self._record(rule_id, relation_key, "recovered", attempts)
            return result
        self._record(rule_id, relation_key, "not_recovered", attempts)
        return None

    def _record(self, rule_id: str, relation_key: str, status: str,
                attempts: Sequence[RecoveryAttempt]) -> None:
        self.records.append({
            "rule_id": rule_id,
            "relation_key": relation_key,
            "status": status,
            "attempts": [attempt.to_dict() for attempt in attempts],
        })


def default_strategies() -> List[RecoveryStrategy]:
    """默认只登记不改变原件内容的既有原生恢复。"""

    def resolve_empty_shadow_tables(document: Any) -> bool:
        artifact = getattr(document, "parse_artifact", None)
        if artifact is None:
            return False
        from src.services.source_reconstruction import resolve_empty_table_shadows

        by_page: Dict[int, List[Any]] = {}
        for table in getattr(artifact, "table_fragments", []) or []:
            by_page.setdefault(int(getattr(table, "page_number", 0) or 0), []).append(table)
        changed = False
        for page, tables in by_page.items():
            page_text = ""
            texts = getattr(document, "page_texts", []) or []
            if 1 <= page <= len(texts):
                page_text = str(texts[page - 1] or "")
            selected, decisions = resolve_empty_table_shadows(tables, page_text)
            if decisions and len(selected) != len(tables):
                kept = {getattr(table, "fragment_uid", None) for table in selected}
                artifact.table_fragments = [
                    table for table in artifact.table_fragments
                    if getattr(table, "page_number", 0) != page
                    or getattr(table, "fragment_uid", None) in kept
                ]
                changed = True
        return changed

    fingerprint = hashlib.sha256(b"empty_table_shadow_resolution_v1").hexdigest()[:16]
    return [
        RecoveryStrategy(
            name="empty_table_shadow_resolution",
            fingerprint=fingerprint,
            run=resolve_empty_shadow_tables,
        )
    ]


__all__ = [
    "MAX_STRATEGIES_PER_RUN",
    "RECOVERABLE_REASON_CODES",
    "RecoveryAttempt",
    "RecoveryCoordinator",
    "RecoveryStrategy",
    "default_strategies",
]
