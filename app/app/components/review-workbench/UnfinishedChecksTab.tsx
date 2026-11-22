"use client";

import { useState } from "react";
import type { Problem } from "../../../lib/mock";
import { isProblemSubjectBound, type ReviewDiagnosticItem, type UnfinishedCheckGroup } from "../../../lib/uiAdapters";
import { ObligationDecisionPanel, type ObligationPanelDecision } from "./ObligationDecisionPanel";
import { OBLIGATION_DECISION_LABELS, readableObligationReason, type ObligationDecision } from "./obligationProblems";
import {
  isObligationScopeHandled,
  obligationDecisionForScope,
  type ObligationReviewItemRecord,
} from "./reviewLifecycleAdapters";

const linkClass = "rounded border border-border px-2 py-1 text-xs font-medium text-primary-700 hover:bg-primary-50";
const actionBtnClass = "rounded border border-border bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-50";
const primaryActionBtnClass = "rounded border border-primary-600 bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50";

export function groupEvidenceReference(group: UnfinishedCheckGroup): string {
  return group.page
    ? `第 ${group.page} 页${group.tableName ? ` · ${group.tableName}` : ""}（按系统定位核对）`
    : "";
}

export function mergeObligationRecords(group: UnfinishedCheckGroup, records: ObligationReviewItemRecord[]): ObligationReviewItemRecord | null {
  if (!records.length) return null;
  const allOperands = records.flatMap((record) => record.missing_operands ?? []);
  const allBlocks = records.flatMap((record) => record.blocked_by ?? []);
  // R5：按原文位置/科目表态时，面板只展示本次范围的阻断点；同义务的
  // 其他位置仍在各自卡片里待处理，避免“整项确认”误读。
  const subject = String(group.subject ?? "").trim();
  const page = group.page;
  const scopedOperands = group.decisionScope
    ? allOperands.filter((operand) => {
        const operandPage = typeof operand.page === "number" ? operand.page : null;
        if (page && operandPage !== null && operandPage !== page) return false;
        if (!subject) return true;
        const label = [operand.row_key, operand.role, operand.section]
          .filter((value) => typeof value === "string")
          .join(" ");
        return label.includes(subject);
      })
    : allOperands;
  const scopedBlocks = group.decisionScope
    ? allBlocks.filter((block) => !page || !block.ref || String(block.ref).includes(`第${page}页`))
    : allBlocks;
  return {
    ...records[0],
    title: group.title,
    missing_operands: scopedOperands.length ? scopedOperands : allOperands,
    blocked_by: scopedBlocks.length ? scopedBlocks : allBlocks,
  };
}

export function decisionSummaryLabel(
  records: ObligationReviewItemRecord[],
  scope: string | null | undefined,
): string | null {
  if (!records.length) return null;
  const decisions = records.map((record) => obligationDecisionForScope(record, scope).decision);
  if (!decisions.every((decision) => (["verified_ok", "verified_issue", "not_applicable"] as Array<string | null>).includes(decision))) {
    return null;
  }
  const distinct = Array.from(new Set(decisions.map((decision) => String(decision ?? ""))));
  if (distinct.length === 1) {
    const key = distinct[0] as Exclude<ObligationDecision, "pending">;
    return OBLIGATION_DECISION_LABELS[key] ?? distinct[0];
  }
  return "已逐项处理";
}

function locationButtonLabel(group: UnfinishedCheckGroup): string {
  if (group.locationLevel === "exact") return `定位第 ${group.page} 页 ↖`;
  if (group.locationLevel === "table") return `查看第 ${group.page} 页（对应表）↖`;
  return `查看第 ${group.page} 页 ↖`;
}

export function resolveGroupMode(
  group: UnfinishedCheckGroup,
  records: ObligationReviewItemRecord[],
): "manual_required" | "covered_by_issue" | "notice_only" {
  if (records.length > 0) return "manual_required";
  if (group.handlingMode) return group.handlingMode;
  if (group.coveredByIssue) return "covered_by_issue";
  if (group.blocksCompletion) return "manual_required";
  return "notice_only";
}

export function UnfinishedChecksTab({
  checks,
  groups,
  initialGroupId,
  related,
  onLocate,
  onOpenProblem,
  obligationById = {},
  onDecideGroup,
  readOnly = false,
  readOnlyHint,
}: {
  checks: ReviewDiagnosticItem[];
  groups: UnfinishedCheckGroup[];
  initialGroupId: string | null;
  related: Map<string, Problem[]>;
  onLocate: (group: UnfinishedCheckGroup) => void;
  onOpenProblem: (problem: Problem) => void;
  obligationById?: Record<string, ObligationReviewItemRecord>;
  onDecideGroup?: (
    items: ObligationReviewItemRecord[],
    decision: ObligationDecision,
    note: string,
    evidenceReference: string,
    scopeConfirmed: boolean,
    decisionScope: string | null,
  ) => Promise<boolean>;
  readOnly?: boolean;
  readOnlyHint?: string | null;
}) {
  const [expanded, setExpanded] = useState<string | null>(initialGroupId ?? groups[0]?.id ?? null);
  const [panelTarget, setPanelTarget] = useState<{
    group: UnfinishedCheckGroup;
    items: ObligationReviewItemRecord[];
    decision: ObligationPanelDecision;
  } | null>(null);
  const [submittingGroupId, setSubmittingGroupId] = useState<string | null>(null);
  const byId = new Map(checks.map((check) => [check.id, check]));

  if (!groups.length) {
    return <p className="p-4 text-sm text-slate-600">本版本暂无可展示的未完成核对明细。</p>;
  }

  const hasModeMetadata = groups.some((group) => typeof group.blocksCompletion === "boolean" || Boolean(group.handlingMode));
  const groupObligations = (group: UnfinishedCheckGroup): ObligationReviewItemRecord[] => {
    const ids = group.blockingObligationIds?.length ? group.blockingObligationIds : (group.obligationIds ?? group.checkIds);
    return ids.map((id) => obligationById[id]).filter((item): item is ObligationReviewItemRecord => Boolean(item));
  };

  const primaryGroups = hasModeMetadata
    ? groups.filter((group) => resolveGroupMode(group, groupObligations(group)) === "manual_required")
    : groups;
  const secondaryGroups = hasModeMetadata
    ? groups.filter((group) => resolveGroupMode(group, groupObligations(group)) !== "manual_required")
    : [];
  const pendingPrimaryGroups = primaryGroups.filter((group) => {
    const records = groupObligations(group);
    return records.length === 0
      || records.some((record) => !isObligationScopeHandled(record, group.decisionScope));
  });

  const summaryText = (() => {
    if (!hasModeMetadata) return `${groups.length} 处待核对 · 影响 ${checks.length} 项检查`;
    if (primaryGroups.length === 0) {
      return "本材料无需在此额外核对，按「必须处理」完成复核即可。";
    }
    if (pendingPrimaryGroups.length === 0) {
      return "人工核对已全部完成，可以点击右上角「完成复核」。";
    }
    const pendingAffected = pendingPrimaryGroups.reduce(
      (total, group) => total + (group.affectedCheckCount ?? group.checkIds.length),
      0,
    );
    return `${pendingPrimaryGroups.length} 处需人工核对 · 影响 ${pendingAffected} 项检查（核对表态后才能完成复核）`;
  })();

  const submitGroupDecision = async (
    group: UnfinishedCheckGroup,
    items: ObligationReviewItemRecord[],
    decision: ObligationDecision,
    note: string,
  ) => {
    if (!onDecideGroup || !items.length) return;
    setSubmittingGroupId(group.id);
    const evidenceReference = groupEvidenceReference(group);
    const ok = await onDecideGroup(
      items, decision, note, evidenceReference, decision !== "pending",
      group.decisionScope ?? null,
    );
    setSubmittingGroupId(null);
    if (ok) setPanelTarget(null);
  };

  const renderGroupCard = (group: UnfinishedCheckGroup, index: number) => {
    const open = expanded === group.id;
    const groupChecks = group.checkIds.flatMap((id) => byId.get(id) ?? []);
    // P24（2026-11-21 第三轮复审）：同页两科目共享页码、规则甚至检查项时，
    // "去处理"必须逐笔对应本科目（isProblemSubjectBound：related_issue_ids
    // 优先，历史快照退回科目名比对）。没有可唯一对应的正式问题时保留
    // 原文定位与核对状态，不渲染错误的"去处理"入口。
    const candidates = group.checkIds.flatMap((id) => related.get(id) ?? []).filter((problem) =>
      isProblemSubjectBound(group, problem)
      && (!group.page || problem.page === group.page || problem.evidenceRefs?.some((ref) => ref.page === group.page))
    );
    const problems = Array.from(new Map(candidates.map((problem) => [problem.id, problem])).values());
    const context = [group.tableName, group.section, group.subject && `科目 ${group.subject}`].filter(Boolean);
    const locationHint = group.locationLevel === "table" ? "已定位到对应表"
      : group.locationLevel === "page" ? "已定位到对应页"
      : group.locationLevel === "unlocated" ? (group.unlocatedReason || "系统尚未定位到原文") : null;
    const records = groupObligations(group);
    const mode = resolveGroupMode(group, records);
    const handledLabel = decisionSummaryLabel(records, group.decisionScope);
    const unhandledRecords = records.filter(
      (record) => !isObligationScopeHandled(record, group.decisionScope),
    );
    const isBusy = submittingGroupId === group.id;
    const bodyId = `gbc-unfinished-group-${index}-${group.id}`;
    return (
      <section key={group.id} className="overflow-hidden rounded-md border border-border" data-testid={`gbc-unfinished-group-${group.id}`}>
        <button type="button" aria-controls={bodyId} aria-expanded={open}
          onClick={() => { setExpanded(open ? null : group.id); if (!open) onLocate(group); }}
          className="flex w-full flex-wrap items-center justify-between gap-2 bg-surface-50 px-3 py-2 text-left text-sm font-medium text-slate-900 hover:bg-surface-100">
          <span className="min-w-0">
            <span className="block">{group.page ? `第 ${group.page} 页 · ` : ""}{group.title}</span>
            {!open && context.length ? <span className="block text-xs font-normal text-slate-600">{context.join(" · ")}</span> : null}
          </span>
          <span className="flex shrink-0 items-center gap-1.5 text-xs font-normal">
            {hasModeMetadata ? (
              handledLabel ? (
                <span className="rounded bg-success-50 px-1.5 py-0.5 font-medium text-success-700" data-testid={`gbc-unfinished-status-${group.id}`}>
                  已核对 · {handledLabel}
                </span>
              ) : mode === "manual_required" ? (
                <span
                  className="rounded bg-warning-50 px-1.5 py-0.5 font-medium text-warning-700"
                  title="核对并表态后才能点击右上角「完成复核」"
                  data-testid={`gbc-unfinished-status-${group.id}`}
                >
                  需人工核对
                </span>
              ) : mode === "covered_by_issue" ? (
                <span
                  className="rounded bg-info-50 px-1.5 py-0.5 font-medium text-info-700"
                  title="已在「必须处理」生成对应问题，处理该问题即可，无需在此重复补核"
                  data-testid={`gbc-unfinished-status-${group.id}`}
                >
                  已并入「必须处理」
                </span>
              ) : (
                <span
                  className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600"
                  title="仅作参考提示，不影响点击「完成复核」"
                  data-testid={`gbc-unfinished-status-${group.id}`}
                >
                  参考提示
                </span>
              )
            ) : null}
            <span className="text-slate-500">
              影响 {group.affectedCheckCount ?? groupChecks.length} 项
            </span>
          </span>
        </button>
        <div id={bodyId} hidden={!open} className="space-y-2.5 border-t border-border px-3 py-2.5 text-sm text-slate-700">
          {context.length ? <p className="text-xs leading-relaxed text-slate-600">{context.join(" · ")}</p> : null}
          {group.plainReason ? (
            <div className="space-y-1 rounded bg-surface-50 px-2.5 py-2 text-xs leading-relaxed text-slate-700">
              <p>{group.plainReason}</p>
              {group.actionHint ? <p className="text-slate-500">{group.actionHint}</p> : null}
            </div>
          ) : null}
          {locationHint ? (
            <p className={group.locationLevel === "unlocated" ? "text-xs text-warning-700" : "text-xs text-slate-500"}>
              {locationHint}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {group.page ? (
              <button type="button" className={linkClass} onClick={() => onLocate(group)}>
                {locationButtonLabel(group)}
              </button>
            ) : null}
            {problems.map((problem) => (
              <button key={problem.id} type="button" className={linkClass}
                aria-label={`查看关联问题 ${problem.title}（第 ${problem.page ?? "未知"} 页）`}
                onClick={() => onOpenProblem(problem)}>
                去处理：{problem.actualName || problem.title} →
              </button>
            ))}
          </div>

          {mode === "covered_by_issue" ? (
            <p className="rounded bg-info-50 px-2.5 py-1.5 text-xs text-info-700">
              无需在此重复确认：处理完「必须处理」中的对应问题后，本处会自动办结。
            </p>
          ) : mode === "notice_only" && hasModeMetadata ? (
            <p className="text-xs text-slate-500">
              本条仅为参考提示，不强制表态，也不会限制点击「完成复核」。
            </p>
          ) : null}

          {onDecideGroup && records.length > 0 ? (
            <div className="space-y-1.5 rounded-md border border-border bg-white p-2.5" data-testid={`gbc-unfinished-actions-${group.id}`}>
              {handledLabel ? (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="text-success-700">
                    已完成核对（{handledLabel}）{records[0]?.note ? `：${records[0].note}` : ""}
                  </span>
                  {!readOnly ? (
                    <button
                      type="button"
                      disabled={isBusy}
                      onClick={() => void submitGroupDecision(group, records, "pending", "")}
                      className="text-xs text-slate-500 hover:text-slate-800 hover:underline disabled:opacity-50"
                    >
                      {isBusy ? "处理中…" : "撤销表态"}
                    </button>
                  ) : null}
                </div>
              ) : readOnly ? (
                <p className="text-xs text-slate-500">
                  {readOnlyHint || "点击上方「开始复核」后，可在此直接完成人工核对并表态。"}
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={isBusy}
                      data-testid={`gbc-unfinished-decide-ok-${group.id}`}
                      onClick={() => setPanelTarget({ group, items: unhandledRecords.length ? unhandledRecords : records, decision: "verified_ok" })}
                      className={primaryActionBtnClass}
                    >
                      核对无误
                    </button>
                    <button
                      type="button"
                      disabled={isBusy}
                      data-testid={`gbc-unfinished-decide-issue-${group.id}`}
                      onClick={() => setPanelTarget({ group, items: unhandledRecords.length ? unhandledRecords : records, decision: "verified_issue" })}
                      className={actionBtnClass}
                    >
                      确认有问题
                    </button>
                    <button
                      type="button"
                      disabled={isBusy}
                      data-testid={`gbc-unfinished-decide-na-${group.id}`}
                      onClick={() => setPanelTarget({ group, items: unhandledRecords.length ? unhandledRecords : records, decision: "not_applicable" })}
                      className={actionBtnClass}
                    >
                      不适用
                    </button>
                  </div>
                  {records.length > 1 ? (
                    <p className="text-xs text-slate-500">
                      表态一次将同步更新本处关联的 {unhandledRecords.length || records.length} 项检查，无需逐条重复确认。
                    </p>
                  ) : null}
                </>
              )}
            </div>
          ) : null}

          <details open={groupChecks.length === 1}>
            <summary className="cursor-pointer py-1 font-medium text-primary-700">影响的 {groupChecks.length} 项检查</summary>
            <ul className="divide-y divide-border pl-4">
              {groupChecks.map((check) => (
                <li key={check.id} className="py-1.5">
                  <details open={groupChecks.length === 1}>
                    <summary className="cursor-pointer text-sm">{check.title}</summary>
                    <p className="mt-1 whitespace-pre-line text-xs leading-relaxed text-slate-600">
                      {readableObligationReason(check.message.split(/[；;]\s*结构[：:]/u, 1)[0])}
                    </p>
                  </details>
                </li>
              ))}
            </ul>
          </details>
        </div>
      </section>
    );
  };

  return (
    <div className="relative h-full">
      <div className="h-full space-y-2 overflow-y-auto p-3" data-testid="gbc-review-unfinished-checks">
        <p className="pb-1 text-sm font-medium text-slate-800" data-testid="gbc-unfinished-summary">
          {summaryText}
        </p>

        {primaryGroups.map((group, index) => renderGroupCard(group, index))}

        {secondaryGroups.length > 0 ? (
          <details open={primaryGroups.length === 0} className="pt-2" data-testid="gbc-unfinished-secondary-section">
            <summary className="cursor-pointer py-1 text-xs font-medium text-slate-500 hover:text-slate-700">
              无需在此单独处理的说明（{secondaryGroups.length} 处，已并入「必须处理」或仅作参考提示）
            </summary>
            <div className="mt-2 space-y-2">
              {secondaryGroups.map((group, index) => renderGroupCard(group, primaryGroups.length + index))}
            </div>
          </details>
        ) : null}
      </div>

      {panelTarget ? (
        <ObligationDecisionPanel
          key={`${panelTarget.group.id}:${panelTarget.decision}`}
          decision={panelTarget.decision}
          checkName={`${panelTarget.group.page ? `第 ${panelTarget.group.page} 页 · ` : ""}${panelTarget.group.title}${panelTarget.items.length > 1 ? `（同步处理 ${panelTarget.items.length} 项检查）` : ""}`}
          defaultNote={panelTarget.group.plainReason || readableObligationReason(panelTarget.items[0]?.detail)}
          item={mergeObligationRecords(panelTarget.group, panelTarget.items)}
          scopeLabel={panelTarget.group.decisionScope
            ? [panelTarget.group.page ? `第 ${panelTarget.group.page} 页` : null, panelTarget.group.subject || panelTarget.group.title]
                .filter(Boolean).join(" · ")
            : null}
          isSubmitting={submittingGroupId === panelTarget.group.id}
          onSubmit={(note) => void submitGroupDecision(panelTarget.group, panelTarget.items, panelTarget.decision, note)}
          onCancel={() => setPanelTarget(null)}
        />
      ) : null}
    </div>
  );
}
