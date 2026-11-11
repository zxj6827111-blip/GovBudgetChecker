#!/usr/bin/env python
"""整份确认门禁与报告三分类（计划 D4；IR-06 重写，2026-11-10）。

报告必须清晰区分三类对象，不能混在一张"问题清单"里：
1. ``confirmed_material_issues``——已确定的材料问题：证据校验通过
   （verified）、severity 不是 manual_review、且没有忽略/疑问等人工决定
   （引文可定位≠结论确定）。
2. ``genuine_ambiguities``——需要判断的真实材料歧义：manual_review 级、
   OCR/识别风险（unverified）、或结构化歧义标记（parse_ambiguity）。
3. ``system_incomplete``——系统尚未完成的检查及具体卡点：结构化
   next_action（tech_fix/system_recheck）、证据交付失败、AI 未执行，
   以及**任何**既非 completed 也非 not_applicable 的义务状态。
   其中是否**阻塞整份签认**按义务台账的 ``blocks_gate`` 结构化判据
   区分（缺省按阻塞）；全部未完成项都留在分类里，不因非阻塞而消失。

IR-06 修正要点：
- 结果遍历复用 ``evidence_guard.iter_findings``（双模式 rule_findings 与
  legacy ``issues.all`` 同一遍历口径，不再漏读老结构）；
- 结构化技术卡点（diagnostics.next_action）优先于消息措辞——detail 含
  "需人工复核"字样不能把 tech_fix 归为材料歧义；
- verified+manual_review 归歧义而非确定问题；
- 人工已有决定（ignored/questioned/needs_review）的对象不进确定问题。

IR-08 修正要点：
- 义务是否阻塞签认与复核完成门禁同一口径（blocks_gate），不再用
  "非 completed 一律阻塞"把未接 AI 的部署变成永久不可签认，也不再
  让非阻塞状态从分类中消失；
- 记录/刷新/重开读回与并发幂等见 ``api/routes/jobs.py``。

整份确认（confirm_report）门禁：
- 默认关闭；开启后也只签认当前分析版本中证据充分的确定性问题。
- ``can_confirm_whole_report``：不存在**阻塞性** system_incomplete 项时为 True。
- 确认范围绑定分析版本，不覆盖人工疑问、忽略决定和历史有效结论。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

_SYSTEM_NEXT_ACTIONS = {"system_recheck", "tech_fix", "system_fix"}
#: 人工决定中把对象移出"确定问题"的状态（不覆盖人工疑问/忽略决定）
_ADVERSE_DECISION_STATUSES = {"ignored", "questioned", "needs_review", "no_issue", "in_package"}


def _finding_evidence_status(finding: Dict[str, Any]) -> str:
    verification = finding.get("evidence_verification")
    if isinstance(verification, dict):
        return str(verification.get("status") or "unverified")
    return "unverified"


def _unresolved_signals(entry: Dict[str, Any]) -> Dict[str, Any]:
    """未决规则的结构化信号：next_action 与 reason_codes 优先于文本。"""
    diagnostics = entry.get("diagnostics") or []
    next_actions: List[str] = []
    reason_codes: List[str] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        action = str((diagnostic or {}).get("next_action") or "").strip()
        if action:
            next_actions.append(action)
        reason_codes.extend(
            str(code).strip() for code in ((diagnostic or {}).get("reason_codes") or [])
            if str(code).strip()
        )
    return {"next_actions": next_actions, "reason_codes": reason_codes}


def _human_decision_by_id(payload: Dict[str, Any]) -> Dict[str, str]:
    """payload 已投影的 human_decisions → {稳定问题ID: 状态}。"""
    decisions: Dict[str, str] = {}
    for item in payload.get("human_decisions") or []:
        if isinstance(item, dict):
            issue_id = str(item.get("issue_id") or "").strip()
            status = str(item.get("status") or "").strip()
            if issue_id and status:
                decisions[issue_id] = status
    return decisions


def build_report_confirmation(
    status: Dict[str, Any],
    *,
    confirm_report_enabled: bool = False,
) -> Dict[str, Any]:
    """从任务 status 组装整份确认门禁与三分类。纯读取，不落盘。"""
    from src.services.evidence_guard import iter_findings

    result = status.get("result") or {}
    coverage = result.get("meta", {}).get("obligation_coverage") or status.get("check_coverage") or {}
    review_reasons = status.get("review_reasons") or []
    decisions = _human_decision_by_id(status)

    confirmed: List[Dict[str, Any]] = []
    ambiguities: List[Dict[str, Any]] = []
    system_incomplete: List[Dict[str, Any]] = []
    seen_issue_ids = set()

    for finding in iter_findings(result):
        issue_id = str(finding.get("id") or "").strip()
        entry = {
            "id": issue_id or None,
            "rule_id": finding.get("rule_id") or finding.get("source"),
            "severity": finding.get("severity"),
            "page": (finding.get("location") or {}).get("page"),
            "message": str(finding.get("message") or "")[:200],
        }
        if issue_id:
            if issue_id in seen_issue_ids:
                continue
            seen_issue_ids.add(issue_id)
        evidence_status = _finding_evidence_status(finding)
        severity = str(finding.get("severity") or "").strip().lower()
        decision = decisions.get(issue_id, "") if issue_id else ""
        if evidence_status == "failed":
            system_incomplete.append({
                **entry, "reason": "evidence_delivery_failed", "blocking": True})
            continue
        if severity == "manual_review" or decision == "needs_review":
            ambiguities.append({**entry, "reason": "manual_review_or_needs_review"})
            continue
        if decision in _ADVERSE_DECISION_STATUSES:
            # 人工已表忽略/疑问/归入包等：不进"确定材料问题"签认范围
            ambiguities.append({
                **entry,
                "reason": f"human_decision_{decision or 'unknown'}",
                "decision": decision,
            })
            continue
        if evidence_status == "unverified":
            ambiguities.append({**entry, "reason": "evidence_unverified"})
            continue
        if evidence_status == "verified":
            confirmed.append(entry)
        else:
            system_incomplete.append({**entry, "reason": f"evidence_{evidence_status}"})

    unresolved_rules: List[Dict[str, Any]] = []
    for reason in review_reasons:
        for entry in reason.get("unresolved_rules") or []:
            unresolved_rules.append(entry)
    seen_keys = set()
    for entry in unresolved_rules:
        rule_id = str(entry.get("rule_id") or "")
        detail = str(entry.get("detail") or "")
        key = (rule_id, detail[:80])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        signals = _unresolved_signals(entry)
        item = {
            "rule_id": rule_id,
            "detail": detail[:300],
            "next_actions": signals["next_actions"],
            "reason_codes": signals["reason_codes"],
        }
        # 结构化信号优先于措辞（IR-06）：next_action 为技术修复 → 系统
        # 未完成，即使 detail 写着"需人工复核"；仅无结构化信号时才按
        # 文本歧义标记归类，其余一律保守记系统未完成。
        if set(signals["next_actions"]) & _SYSTEM_NEXT_ACTIONS:
            system_incomplete.append({
                **item, "reason": "structured_next_action",
                "next_action": signals["next_actions"][0], "blocking": True,
            })
        elif any("parse_ambiguity" in code for code in signals["reason_codes"]):
            ambiguities.append({**item, "reason": "structured_parse_ambiguity"})
        elif any(marker in detail for marker in ("parse_ambiguity", "歧义")):
            ambiguities.append({**item, "reason": "parse_ambiguity_or_manual"})
        else:
            system_incomplete.append({**item, "reason": "insufficient_data", "blocking": True})

    # 义务级实例：凡不是 completed/not_applicable 的一律进入系统未完成
    # （此前只认 ai_not_run/insufficient_data 两种，无法证明其他失败
    # 状态受门禁约束——IR-06）。
    #
    # IR-08（Codex 独立核验）：分类必须覆盖全部未完成状态，但"是否阻塞
    # 整份签认"要按义务台账自己的结构化判据 ``blocks_gate`` 区分——
    # 与复核完成门禁（review_lifecycle_service 的 blocking_obligation_ids）
    # 同一口径。若非阻塞项（如 AI 语义复核未启用且非必需）也硬性阻塞，
    # 任何未接 AI 的部署都无法使用整份确认；若反过来把阻塞项当非阻塞，
    # 则会让技术失败放行。缺省（旧快照无该字段）按阻塞处理，保守不变。
    covered_rule_keys = {
        (str(item.get("rule_id")), str(item.get("detail"))[:80])
        for item in system_incomplete + ambiguities
    }
    for instance in (coverage.get("instances") or []):
        instance_status = str(instance.get("status") or "").strip()
        obligation_id = str(instance.get("obligation_id") or "")
        if instance_status in {"completed", "not_applicable"}:
            continue
        key = (obligation_id, "")
        if key[0] and any(k[0] == obligation_id for k in covered_rule_keys):
            continue
        # 只有显式 False 才视为非阻塞；缺字段/未知值一律按阻塞。
        blocking = instance.get("blocks_gate") is not False
        system_incomplete.append({
            "rule_id": obligation_id,
            "detail": f"义务状态={instance_status or 'unknown'}，未完成",
            "next_action": "system_recheck",
            "reason": f"obligation_{instance_status or 'unknown'}",
            "blocking": blocking,
        })

    blocking_items = [
        item for item in system_incomplete if item.get("blocking", True)
    ]
    nonblocking_count = len(system_incomplete) - len(blocking_items)

    return {
        "analysis_revision": status.get("analysis_revision"),
        "conclusion": status.get("analysis_conclusion"),
        "confirm_report_enabled": bool(confirm_report_enabled),
        "gate": {
            "can_confirm_whole_report": len(blocking_items) == 0,
            "blocking_reason": (
                None if not blocking_items
                else (
                    f"存在 {len(blocking_items)} 项阻塞性未完成检查，不能整份签认"
                    + (
                        f"（另有 {nonblocking_count} 项非阻塞未完成项仍列入系统未完成并排除在签认范围外）"
                        if nonblocking_count else ""
                    )
                )
            ),
            "blocking_system_incomplete": len(blocking_items),
            "nonblocking_system_incomplete": nonblocking_count,
            "confirm_scope": "deterministic_findings_only",
        },
        "categories": {
            "confirmed_material_issues": confirmed,
            "genuine_ambiguities": ambiguities,
            "system_incomplete": system_incomplete,
        },
        "counts": {
            "confirmed_material_issues": len(confirmed),
            "genuine_ambiguities": len(ambiguities),
            "system_incomplete": len(system_incomplete),
        },
    }


def confirm_report_allowed(
    confirmation: Dict[str, Any],
    current_revision: Optional[str],
) -> Dict[str, Any]:
    """写侧门禁：默认关闭/系统未完成/版本漂移都不放行。"""
    if not confirmation.get("confirm_report_enabled"):
        return {"allowed": False, "code": "CONFIRM_REPORT_DISABLED"}
    gate = confirmation.get("gate") or {}
    if not gate.get("can_confirm_whole_report"):
        return {"allowed": False, "code": "SYSTEM_INCOMPLETE"}
    revision = confirmation.get("analysis_revision")
    if current_revision and revision and str(current_revision) != str(revision):
        return {"allowed": False, "code": "REVISION_CHANGED"}
    return {"allowed": True, "code": "OK"}


# ---------------------------------------------------------------------------
# IR-07：确认记录的持久化与读回（原子写入 + 追加式业务记录）
# ---------------------------------------------------------------------------

def _confirm_records_path(job_id: str):
    from api import runtime

    job_dir = runtime.UPLOAD_ROOT / str(job_id)
    return job_dir, job_dir / "confirm_reports.jsonl"


def read_confirm_records(job_id: str) -> List[Dict[str, Any]]:
    """读取该任务的整份确认记录（按提交顺序）。读不到视为无记录。"""
    _job_dir, path = _confirm_records_path(str(job_id))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def append_confirm_record(job_id: str, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """原子追加一条确认记录；写失败返回 None（无半状态）。

    追加式 JSONL + 临时文件换名：写侧以读取接口重放为准，读不到就是
    没有确认过——不会出现"接口声称已确认但读不回"的半状态。

    IR-08：读-改-写全程持有文件锁，并在锁内**再次检查**同 key+版本是否
    已被并发请求写入；两个并发双击请求只会留下一条记录，后到者拿到
    先到者的记录（调用方据 ts 判定幂等），不会产生重复业务记录。
    """
    import os
    import tempfile

    job_dir, path = _confirm_records_path(str(job_id))
    lock_path = job_dir / ".confirm_reports.lock"
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_handle:
            _acquire_file_lock(lock_handle)
            try:
                existing = read_confirm_records(str(job_id))
                key = record.get("idempotency_key")
                revision = record.get("analysis_revision")
                if key is not None:
                    for prior in existing:
                        if (prior.get("idempotency_key") == key
                                and prior.get("analysis_revision") == revision):
                            return prior
                existing.append(record)
                fd, tmp = tempfile.mkstemp(
                    dir=str(job_dir), prefix=".confirm_reports.", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        for item in existing:
                            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                return record
            finally:
                _release_file_lock(lock_handle)
    except Exception:
        return None


def _acquire_file_lock(handle) -> None:
    """跨进程排他锁（POSIX）；无 fcntl 的平台退化为进程内锁语义。

    确认记录按任务目录隔离，锁文件与记录同目录，不用全局锁。
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows 分支
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_file_lock(handle) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows 分支
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
