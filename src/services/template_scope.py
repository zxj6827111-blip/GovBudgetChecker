"""决算写法裁决的纯函数基础；调用者负责审核证据、年度绑定与身份。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Scope = Literal['unit', 'department']


@dataclass(frozen=True)
class Evidence:
    scope: Scope
    kind: Literal['declaration', 'heading', 'table_note']
    page: int
    text: str
    position: str
    qualified: bool = False


@dataclass(frozen=True)
class HumanDecision:
    value: Literal['unit', 'department', 'unknown']
    decision_id: str
    # 人工依据仅引用封面时不能用来校验同一封面。
    cover_independent: bool = False


@dataclass(frozen=True)
class ScopeResolution:
    observed_template_style: str
    status: str
    value: str | None
    source: str
    reason: str
    cover_independent_scope: str | None
    evidence: tuple[Evidence, ...]
    human_decision_id: str | None


def resolve_material_scope(*, evidence: tuple[Evidence, ...],
                           template_qualified: bool,
                           human: HumanDecision | None = None,
                           archive_scope: Scope | None = None) -> ScopeResolution:
    """按 v3 裁决表处理已合格证据，不从文件名、金额范围或多数票推断。"""
    if human and human.value not in ('unit', 'department', 'unknown'):
        raise ValueError('无效的写法决定')
    if archive_scope not in (None, 'unit', 'department'):
        raise ValueError('无效的归档层级')
    valid = tuple(e for e in evidence if e.qualified and e.scope in ('unit', 'department')
                  and e.kind in ('declaration', 'heading', 'table_note') and e.page > 0 and e.text.strip())
    body = tuple(e for e in valid if e.kind in ('heading', 'table_note') and e.page > 1)
    by_scope = {scope: {e.kind for e in body if e.scope == scope} for scope in ('unit', 'department')}
    present = {scope for scope, kinds in by_scope.items() if kinds}
    if len(present) == 2:
        observed = 'mixed'
    elif len(present) == 1 and by_scope[next(iter(present))] == {'heading', 'table_note'}:
        observed = next(iter(present))
    else:
        observed = 'insufficient'
    # 未批准的模板只展示观察结果，不以其正文信号产生正式裁决。
    effective_body = body if template_qualified else ()
    declarations = {e.scope for e in valid if e.kind == 'declaration'}
    body_scopes = {e.scope for e in effective_body}
    if human:
        if human.value == 'unknown':
            status, value, source, reason = 'insufficient', None, 'human', 'human_unknown'
        else:
            status, value, source, reason = 'confirmed', human.value, 'human', 'human_decision'
    elif len(declarations) > 1 or len(body_scopes) > 1:
        status, value, source, reason = 'conflict', None, 'unresolved', 'conflicting_evidence'
    elif declarations and body_scopes and declarations != body_scopes:
        status, value, source, reason = 'conflict', None, 'unresolved', 'declaration_body_conflict'
    elif declarations:
        status, value, source, reason = 'confirmed', next(iter(declarations)), 'declaration', 'explicit_declaration'
    elif template_qualified and observed in ('unit', 'department'):
        if archive_scope and archive_scope != observed:
            status, value, source, reason = 'conflict', None, 'unresolved', 'archive_conflict'
        else:
            status, value, source, reason = 'confirmed', observed, 'template', 'qualified_template'
    else:
        status, value, source, reason = 'insufficient', None, 'unresolved', 'insufficient_evidence'
    independent = None
    if status == 'confirmed':
        if source == 'human' and human and human.cover_independent:
            independent = value
        elif source == 'template':
            independent = value
        elif source == 'declaration' and any(e.kind == 'declaration' and e.page > 1 and e.scope == value for e in valid):
            independent = value
    return ScopeResolution(observed, status, value, source, reason, independent, valid,
                           human.decision_id if human else None)
