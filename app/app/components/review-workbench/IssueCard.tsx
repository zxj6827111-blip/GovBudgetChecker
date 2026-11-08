/**
 * IssueCard：审核工作台问题区的三块——列表行（IssueRow）、当前问题的完整解释
 * （IssueCard），以及固定在解释区底部的处理栏（IssueDecisionFooter）。
 *
 * 解释区按“结论 → 金额对照 → 计算明细 → 检查依据 → 原文证据 → 处理建议 →
 * 处理记录”的顺序阅读；计算明细与证据都能点回左侧原文。字号由外层容器的
 * font-size 决定，这里一律用 em，阅读字号切换时整块等比放大。
 *
 * 颜色一律用语义令牌（primary/success/danger/warning/info/surface/slate），
 * 不复用 app/lib/issueSeverity.ts 的 badgeClass/accentClass/panelClass——那批
 * className 用了被禁止的调色板，这里只复用 normalizeSeverityCode/
 * getSeverityMeta(...).riskLabel 这些不含颜色的语义部分。
 *
 * evidence_status=degraded 的降级标识：用户有权知道哪条证据不完整，降级问题
 * 必须带一个明显但不与严重度徽章混淆的独立标识。
 */
"use client";

import { evidenceExcerptLabel } from "@/lib/evidenceIdentity";

import { getSeverityMeta, normalizeSeverityCode, type SeverityCode } from "../../../lib/issueSeverity";
import type { Problem } from "../../../lib/mock";
import { parseDecisionNote } from "./reviewDecisionNotes";
import type { ObligationCascadeItemRecord } from "./reviewLifecycleAdapters";
import {
  buildAmountComparison,
  buildCalculationGroups,
  detailSummary,
  evidenceRoleLabel,
  type CalculationGroup,
} from "./reviewReadingAdapters";
import { dedupeEvidenceRefsForDisplay, isProblemDegraded } from "./reviewWorkbenchAdapters";
import { OBLIGATION_DECISION_LABELS, isObligationProblem, type ObligationDecision } from "./obligationProblems";

const SEVERITY_TONE_CLASSES: Record<SeverityCode, string> = {
  critical: "bg-danger-100 text-danger-700",
  high: "bg-danger-100 text-danger-700",
  medium: "bg-warning-100 text-warning-700",
  low: "bg-slate-100 text-slate-600",
  info: "bg-slate-100 text-slate-600",
  manual_review: "bg-info-100 text-info-700",
};

export type IssueWorkflowAction = "confirm" | "ignore" | "note" | "restore";

function sourceLabel(problem: Problem): string {
  return (problem.findingSources ?? (problem.source === "ai" || problem.source === "rule" ? [problem.source] : []))
    .map((source) => source === "ai" ? "大模型" : "本地规则")
    .join(" + ");
}

export function WorkflowStatusBadge({ workflowStatus }: { workflowStatus: string | null }) {
  // 补核条目的处理状态是补核决定码，与问题工作流共用一个状态表。
  if (workflowStatus === "verified_issue") return <span className="shrink-0 rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已确认有问题</span>;
  if (workflowStatus === "verified_ok") return <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-500">已核对无误</span>;
  if (workflowStatus === "not_applicable") return <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-500">不适用</span>;
  if (workflowStatus === "confirmed") return <span className="shrink-0 rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已确认</span>;
  if (workflowStatus === "no_issue") return <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-500">已忽略</span>;
  if (workflowStatus === "in_package") return <span className="shrink-0 rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已进整改包</span>;
  return null;
}

export interface IssueRowProps {
  problem: Problem;
  isActive: boolean;
  workflowStatus: string | null;
  onSelect: () => void;
}

/** 列表行：沿用原问题卡的定位属性（data-review-problem-id / aria-current / 处理状态）。 */
export function IssueRow({ problem, isActive, workflowStatus, onSelect }: IssueRowProps) {
  const severityCode = normalizeSeverityCode(problem.severity);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const sources = sourceLabel(problem);
  return (
    <div
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      data-testid={`gbc-review-issue-card-${problem.id}`}
      data-review-problem-id={problem.id}
      data-workflow-status={workflowStatus ?? "pending"}
      aria-current={isActive ? "true" : undefined}
      title={problem.title}
      className={`flex cursor-pointer flex-col gap-1 rounded-md border px-2.5 py-2 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
        isActive ? "border-primary-500 bg-primary-50" : "border-transparent border-b-border hover:bg-surface-100"
      }`}
    >
      <span className="flex min-w-0 items-center gap-2 text-slate-500">
        {isObligationProblem(problem) ? (
          <span className="shrink-0 rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700">未能自动核对</span>
        ) : (
          <span className={`shrink-0 rounded-full px-2 py-0.5 font-medium ${SEVERITY_TONE_CLASSES[severityCode]}`}>
            {severityMeta.riskLabel}
          </span>
        )}
        {sources ? <span className="shrink-0">{sources}</span> : null}
        {isProblemDegraded(problem) ? <span className="shrink-0 rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700">证据不足</span> : null}
        <WorkflowStatusBadge workflowStatus={workflowStatus} />
        {problem.page ? <span className="ml-auto shrink-0">P{problem.page}</span> : null}
      </span>
      <span className={`line-clamp-2 text-[13px] leading-snug ${isActive ? "font-medium text-slate-900" : "text-slate-700"}`}>{problem.title}</span>
    </div>
  );
}

export interface IssueCardProps {
  problem: Problem;
  workflowStatus: string | null;
  /** 工作流 note：开头可能带忽略理由块，其余是补充意见。 */
  workflowNote?: string;
  selectedEvidenceRefKey?: string | null;
  onSelectEvidenceRef?: (refKey: string) => void;
  /** 因本问题（缺表/空表）而无法自动进行的核对；不单独处理，随本问题了结。 */
  cascadedChecks?: ObligationCascadeItemRecord[];
}

const SECTION_TITLE = "mb-2 text-[0.8em] font-semibold text-slate-500";

/** 数字短的三栏并排；AI 给出的长句子逐行列出，免得被挤成竖条。 */
function isCompactValue(value: string): boolean {
  return value.length <= 16;
}

function CalculationDetails({ problem, group, selectedKey, onSelect }: {
  problem: Problem;
  group: CalculationGroup;
  selectedKey?: string | null;
  onSelect?: (refKey: string) => void;
}) {
  const rows = [group.total, ...group.items];
  const formula = group.itemsSum === null ? null
    : group.items.length <= 8
      ? `${group.items.map((item) => item.valueText).join(" + ")} = ${group.itemsSum}`
      : `${group.items.length} 项明细合计 = ${group.itemsSum}`;
  return (
    <details open className="mb-5 border-y border-border" data-testid={`gbc-review-calc-${problem.id}`}>
      <summary className="cursor-pointer py-2.5 text-[0.85em] font-medium text-primary-700">
        计算明细 · 合计 1 项、明细 {group.items.length} 项
        <span className="ml-1.5 font-normal text-slate-400">按证据金额逐项加总</span>
      </summary>
      {group.roundingSummary ? <p className="mb-2 text-[0.82em] leading-relaxed text-warning-700">{group.roundingSummary}</p> : null}
      <table className="mb-2 w-full border-collapse text-[0.82em]" aria-label="参与计算的合计与明细，金额单位万元">
        <thead>
          <tr className="text-left text-slate-500">
            <th scope="col" className="px-1 pb-1.5 font-normal">参与项</th>
            <th scope="col" className="px-1 pb-1.5 text-right font-normal">金额（万元）</th>
            <th scope="col" className="px-1 pb-1.5 text-right font-normal">原文</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={row.key}
              className={`border-t border-border ${selectedKey === row.key ? "bg-primary-50" : ""}`}
              data-testid={`gbc-review-calc-row-${row.key}`}
            >
              <td className="px-1 py-1.5 align-top">
                <button type="button" onClick={() => onSelect?.(row.key)} className="text-left text-primary-700 hover:underline">
                  {index === 0 ? <span className="mr-1 rounded bg-primary-100 px-1 text-[0.85em] text-primary-700">合计</span> : null}
                  {row.label}
                </button>
              </td>
              <td className="px-1 py-1.5 text-right align-top tabular-nums">{row.valueText}</td>
              <td className="px-1 py-1.5 text-right align-top">
                <button
                  type="button"
                  onClick={() => onSelect?.(row.key)}
                  aria-label={`定位${row.label}，第 ${row.page} 页`}
                  className="whitespace-nowrap text-primary-700 hover:underline"
                >P{row.page}{row.located ? " ↖" : ""}</button>
              </td>
            </tr>
          ))}
        </tbody>
        {group.itemsSum !== null ? (
          <tfoot>
            <tr className="border-t border-border font-medium">
              <td className="px-1 py-1.5">明细合计</td>
              <td className="px-1 py-1.5 text-right tabular-nums">{group.itemsSum}</td>
              <td />
            </tr>
          </tfoot>
        ) : null}
      </table>
      {formula ? <p className="mb-1 break-all text-[0.8em] leading-relaxed text-slate-500 tabular-nums">{formula} 万元</p> : null}
      {group.difference !== null ? (
        <p className="pb-3 text-[0.82em] leading-relaxed text-slate-700">
          表内合计 {group.total.valueText} − 明细合计 {group.itemsSum} = 差额 <strong className="font-semibold">{group.difference} 万元</strong>
        </p>
      ) : (
        <p className="pb-3 text-[0.82em] leading-relaxed text-warning-700">部分金额无法按数字读取，未给出加总；请对照原文逐项核对。</p>
      )}
    </details>
  );
}

export function IssueCard({
  problem,
  workflowStatus,
  workflowNote,
  selectedEvidenceRefKey,
  onSelectEvidenceRef,
  cascadedChecks,
}: IssueCardProps) {
  const severityCode = normalizeSeverityCode(problem.severity);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const degraded = isProblemDegraded(problem);
  const sources = sourceLabel(problem);
  const unlocatedReasonLabels = Array.from(new Set([
    ...(problem.evidenceMissing ?? []),
    ...(problem.evidenceVerificationReasons ?? []),
  ])).flatMap((reason) => {
    if (reason.includes("text_not_found_on_page")) return ["AI 引用原文未在对应页找到"];
    if (reason.includes("missing_or_invalid_source_identity")) return ["证据未绑定到当前 PDF 原件"];
    if (reason.includes("missing_bbox")) return ["没有可核验的精确坐标"];
    if (reason.includes("page_out_of_range")) return ["引用页码超出 PDF 页数"];
    return [];
  });
  const groups = buildCalculationGroups(problem);
  const comparison = buildAmountComparison(problem, groups);
  const calcKeys = new Set(groups.flatMap((group) => [group.total.key, ...group.items.map((item) => item.key)]));
  const evidenceRefs = dedupeEvidenceRefsForDisplay(problem.evidenceRefs ?? []).filter((ref) => !calcKeys.has(ref.key));
  // 取整说明没有对上计算明细时仍要展示，不能因为改版丢掉。
  const looseRoundingCases = (problem.roundingCases ?? []).filter((item) => !groups.some((group) => group.relationId === item.relationId));
  const summary = detailSummary(problem.title, problem.description);
  const { ignoreReason, userNote } = parseDecisionNote(workflowNote);
  const missingOperands = groups.length === 0 && /合计|加总|之和|分项/.test(`${problem.title}${problem.description}`);
  const hasBasis = Boolean(problem.check || problem.tableOrSection || problem.amountsProvenance);
  const compactComparison = comparison?.values.every(isCompactValue) ?? false;
  const quote = problem.snippet ? (problem.sourceIdentityVerified ? `“${problem.snippet}”` : problem.snippet) : "";

  return (
    <article
      data-testid="gbc-review-issue-detail"
      data-detail-problem-id={problem.id}
      data-workflow-status={workflowStatus ?? "pending"}
      className="px-4 py-4 text-slate-800 sm:px-5"
    >
      {/* 一行说清“多严重、谁发现、哪条规则、在哪页”；所属队列看页签，定位看证据。 */}
      <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.75em] text-slate-500">
        <span className={`rounded-full px-2 py-0.5 font-medium ${SEVERITY_TONE_CLASSES[severityCode]}`}>
          {severityMeta.riskLabel}
        </span>
        <span title={problem.relatedRuleIds?.length ? `同一原文缺陷：${problem.relatedRuleIds.join("、")}` : undefined}>
          {[sources, problem.ruleId, problem.relatedRuleIds?.length && problem.relatedRuleIds.length > 1
            ? `同源规则 ${problem.relatedRuleIds.join("、")}` : "", problem.page ? `第 ${problem.page} 页` : ""]
            .filter(Boolean).join(" · ")}
        </span>
        {problem.usesManualFill ? (
          <span className="rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700" title="该结论用到了审核员对照原页补录的数据，可在「读取校正」页签查看记录">
            含人工补录数据
          </span>
        ) : null}
        {problem.sourceRelationLabel ? (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600">
            {problem.sourceRelationLabel}
          </span>
        ) : null}
        <WorkflowStatusBadge workflowStatus={workflowStatus} />
      </div>
      {problem.claimedPage && problem.page && problem.claimedPage !== problem.page ? (
        <details className="mb-2 text-[0.8em] text-info-700">
          <summary className="cursor-pointer">按 PDF 原文校正页码 · 查看定位记录</summary>
          分析时标注第 {problem.claimedPage} 页，原文在 PDF 第 {problem.page} 页。定位通过仍需核实 AI 判断。
        </details>
      ) : null}

      {degraded ? (
        <div
          className="mb-3 rounded-md bg-warning-50 px-2.5 py-1.5 text-[0.8em] font-medium leading-relaxed text-warning-700"
          data-testid={`gbc-review-issue-degraded-badge-${problem.id}`}
        >
          <div>证据不足待复核：本条为 AI 分析结果，因证据不完整已降级，未计入正式问题数</div>
          {unlocatedReasonLabels.length > 0 ? (
            <div className="mt-0.5 font-normal">未绘制定位框：{unlocatedReasonLabels.join("；")}</div>
          ) : null}
        </div>
      ) : null}

      <h2 className="mb-2 break-words text-[1.25em] font-semibold leading-snug text-slate-900">{problem.title}</h2>
      {summary ? <p className="mb-3 whitespace-pre-line text-[0.92em] leading-relaxed text-slate-600">{summary}</p> : null}

      {comparison || hasBasis ? (
        <div className="mb-5" data-testid={`gbc-review-issue-explanation-${problem.id}`}>
          {comparison && compactComparison ? (
            <div className="grid grid-cols-3 gap-1 rounded-md bg-surface-50 px-2 py-3" aria-label="金额对照">
              {comparison.labels.map((label, index) => (
                <div key={label} className={`flex min-w-0 flex-col px-2 ${index > 0 ? "border-l border-border" : ""}`}>
                  <span className="mb-1 text-[0.72em] text-slate-500">{label}</span>
                  <span className={`break-all text-[1.2em] font-semibold tabular-nums ${index === 2 ? "text-danger-700" : "text-slate-900"}`}>{comparison.values[index]}</span>
                </div>
              ))}
            </div>
          ) : comparison ? (
            <div className="space-y-1 rounded-md bg-surface-50 px-3 py-2 text-[0.88em] leading-relaxed text-slate-700">
              {comparison.labels.map((label, index) => <div key={label}>{label}：{comparison.values[index]}</div>)}
            </div>
          ) : null}
          {hasBasis ? (
            <div className="mt-2 space-y-0.5 text-[0.8em] leading-relaxed text-slate-600">
              {problem.check ? <div>检查项：{problem.check}</div> : null}
              {problem.tableOrSection ? <div>表/章节：{problem.tableOrSection}</div> : null}
              {problem.amountsProvenance === "model_claimed_not_verified" ? (
                <div className="text-warning-700">数值来源：模型声称，尚未经程序核实。</div>
              ) : problem.amountsProvenance === "program_calculated" ? (
                <div className="text-slate-500">
                  数值来源：程序按已定位事实计算
                  {problem.calculationBasis && typeof problem.calculationBasis === "object"
                    ? `（单位：${String(problem.calculationBasis.unit ?? "未记录")}，精度：${String(problem.calculationBasis.precision ?? "未记录")}，规则版本：${String(problem.calculationBasis.rule_version ?? "未记录")}）`
                    : ""}
                  。
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {groups.map((group) => (
        <CalculationDetails
          key={group.relationId}
          problem={problem}
          group={group}
          selectedKey={selectedEvidenceRefKey}
          onSelect={onSelectEvidenceRef}
        />
      ))}
      {missingOperands ? (
        <p className="mb-4 text-[0.8em] leading-relaxed text-slate-500" data-testid={`gbc-review-calc-missing-${problem.id}`}>
          本条规则未列出参与计算的各项金额，请在原文{problem.page ? `第 ${problem.page} 页` : ""}逐项核对。
        </p>
      ) : null}
      {looseRoundingCases.map((roundingCase) => (
        <p key={roundingCase.relationId} className="mb-3 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-[0.85em] leading-relaxed text-slate-700">
          {roundingCase.summary}
        </p>
      ))}

      {problem.snippet || evidenceRefs.length > 0 ? (
        <section className="mb-5">
          <h3 className={SECTION_TITLE}>{evidenceExcerptLabel(problem.sourceIdentityVerified === true)}</h3>
          {/* 只有一处证据时，引文就放在这条可点击的证据里，不再单独重复一遍。 */}
          {problem.snippet && evidenceRefs.length !== 1 ? (
            <div className="mb-2 rounded-md bg-surface-100 px-3 py-2 text-[0.9em] leading-relaxed text-slate-700">{quote}</div>
          ) : null}
          {evidenceRefs.length > 0 ? (
            <div className="grid gap-1.5">
              {evidenceRefs.map((ref, index) => {
                const roleLabel = evidenceRoleLabel(ref.role);
                // 位置文字只有页码时不重复显示（下方已有页码）。
                const location = /^第 \d+ 页$/.test(ref.locationText) ? "" : ref.locationText.replace(/^第 \d+ 页 · /, "");
                const main = evidenceRefs.length === 1 && quote ? quote
                  : [roleLabel, ref.valueText ? `${ref.valueText} 万元` : ""].filter(Boolean).join(" · ");
                const detail = [`第 ${ref.page} 页`, ref.bbox ? "精确定位" : "", location].filter(Boolean).join(" · ");
                return (
                  <button
                    key={ref.key}
                    type="button"
                    onClick={() => onSelectEvidenceRef?.(ref.key)}
                    aria-pressed={selectedEvidenceRefKey === ref.key}
                    data-testid={`gbc-review-evidence-ref-${problem.id}-${ref.key}`}
                    className={`grid w-full grid-cols-[1.6em_minmax(0,1fr)] items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${
                      selectedEvidenceRefKey === ref.key ? "border-primary-500 bg-primary-50" : "border-border hover:bg-surface-50"
                    }`}
                    title={`定位到原文：${[roleLabel, detail].filter(Boolean).join(" · ")}`}
                  >
                    <span className="mt-0.5 grid h-[1.6em] w-[1.6em] place-items-center rounded bg-surface-100 text-[0.75em] text-slate-500">{String.fromCharCode(65 + (index % 26))}</span>
                    <span className="min-w-0">
                      <span className="block break-words text-[0.88em] leading-relaxed text-slate-800">{main || roleLabel}</span>
                      <span className="block break-words text-[0.72em] leading-relaxed text-slate-500">{detail} ↖</span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : null}
        </section>
      ) : null}

      {problem.suggestion ? (
        <section className="mb-5 rounded-md bg-primary-50 px-3.5 py-3">
          <h3 className="mb-1 text-[0.8em] font-semibold text-primary-700">处理建议</h3>
          <p className="whitespace-pre-line text-[0.88em] leading-relaxed text-slate-700">{problem.suggestion}</p>
        </section>
      ) : null}

      {cascadedChecks && cascadedChecks.length > 0 ? (
        <section className="mb-5 border-t border-border pt-3" data-testid="gbc-review-cascaded-checks">
          <h3 className={SECTION_TITLE}>受此问题影响，以下 {cascadedChecks.length} 项核对未能自动进行</h3>
          <p className="mb-1.5 text-[0.82em] text-slate-500">无需单独处理：补齐或更正本问题涉及的表格后重新检查即可。</p>
          <ul className="list-disc space-y-0.5 pl-5 text-[0.88em] leading-relaxed text-slate-700">
            {cascadedChecks.map((check) => (
              <li key={check.obligation_id}>{check.title}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {ignoreReason && workflowStatus === "no_issue" ? (
        <section className="mb-4 border-t border-border pt-3" data-testid="gbc-review-ignore-record">
          <h3 className={SECTION_TITLE}>忽略记录</h3>
          <p className="whitespace-pre-line break-words text-[0.88em] leading-relaxed text-slate-700">
            理由：{ignoreReason.label}{ignoreReason.detail ? `\n说明：${ignoreReason.detail}` : ""}
          </p>
        </section>
      ) : null}
      {userNote ? (
        <section className="mb-4 border-t border-border pt-3" data-testid="gbc-review-note-record">
          <h3 className={SECTION_TITLE}>补充意见</h3>
          <p className="whitespace-pre-line break-words text-[0.88em] leading-relaxed text-slate-700">{userNote}</p>
        </section>
      ) : null}
    </article>
  );
}

export interface IssueDecisionNoticeView {
  text: string;
  canUndo: boolean;
  onUndo: () => void;
}

export interface IssueDecisionFooterProps {
  /** 队列已空时为 null：只留下撤销提示与下一步引导。 */
  problem: Problem | null;
  workflowStatus: string | null;
  showActions: boolean;
  /** 不能提交判定时的说明（预览、失效、历史、已完成）。 */
  readOnlyHint?: { text: string; invalidatedReason: string | null; completed?: boolean } | null;
  savedAtText?: string | null;
  isSubmitting: boolean;
  onAction: (action: IssueWorkflowAction) => void;
  /** 「未能自动核对」条目的三个结论（及置回待处理），提交到补核接口。 */
  onObligationDecision?: (decision: ObligationDecision) => void;
  notice: IssueDecisionNoticeView | null;
  nextStep?: { label: string; onClick: () => void } | null;
}

/** 操作栏固定在解释区底部：长解释滚动时按钮不会被推出视野；不能操作时说明原因。 */
export function IssueDecisionFooter({
  problem,
  workflowStatus,
  showActions,
  readOnlyHint,
  savedAtText,
  isSubmitting,
  onAction,
  onObligationDecision,
  notice,
  nextStep,
}: IssueDecisionFooterProps) {
  const isConfirmed = workflowStatus === "confirmed";
  const isIgnored = workflowStatus === "no_issue";
  const actions = problem && showActions;
  const obligation = isObligationProblem(problem);
  if (!actions && !readOnlyHint && !notice && !nextStep && !savedAtText) return null;
  return (
    <footer className="shrink-0 space-y-1.5 border-t border-border bg-white px-4 py-2" data-testid="gbc-review-decision-footer">
      {readOnlyHint ? (
        <p className="text-xs leading-relaxed text-slate-600" data-testid={readOnlyHint.completed ? "gbc-review-readonly-hint" : "gbc-review-preview-notice"}>
          {readOnlyHint.invalidatedReason ? (
            <span className="font-medium text-warning-700" data-testid="gbc-review-invalidated-notice">{readOnlyHint.invalidatedReason}。</span>
          ) : null}
          {readOnlyHint.text}
        </p>
      ) : null}
      {notice || nextStep || (!actions && savedAtText) ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          {notice ? (
            <span role="status" className="flex min-w-0 items-center gap-1.5 text-success-700" data-testid="gbc-review-decision-notice">
              <span className="min-w-0 truncate" title={notice.text}>{notice.text}</span>
              {notice.canUndo ? (
                <button type="button" onClick={notice.onUndo} disabled={isSubmitting} data-testid="gbc-review-undo" className="shrink-0 font-medium underline disabled:cursor-not-allowed disabled:opacity-50">撤销</button>
              ) : null}
            </span>
          ) : null}
          {nextStep ? (
            <button type="button" onClick={nextStep.onClick} className="font-medium text-primary-700 hover:underline" data-testid="gbc-review-next-step">{nextStep.label}</button>
          ) : null}
          {/* 队列处理完没有按钮行时，保存时间放在这一行。 */}
          {!actions && savedAtText ? <span className="ml-auto text-slate-400" data-testid="gbc-review-status-bar-saved-at">{savedAtText}</span> : null}
        </div>
      ) : null}
      {actions && obligation ? (
        <div className="flex flex-wrap items-center gap-2" data-testid="gbc-review-obligation-actions">
          {(Object.keys(OBLIGATION_DECISION_LABELS) as Array<keyof typeof OBLIGATION_DECISION_LABELS>).map((decision, index) => (
            <button
              key={decision}
              type="button"
              onClick={() => onObligationDecision?.(decision)}
              disabled={isSubmitting || workflowStatus === decision}
              data-testid={`gbc-review-obligation-${decision}-${problem.id}`}
              className={index === 0
                ? "rounded-md border border-primary-600 bg-primary-600 px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                : "rounded-md border border-border bg-white px-3.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-50"}
            >
              {OBLIGATION_DECISION_LABELS[decision]}
            </button>
          ))}
          {workflowStatus ? (
            <button
              type="button"
              onClick={() => onObligationDecision?.("pending")}
              disabled={isSubmitting}
              data-testid={`gbc-review-obligation-pending-${problem.id}`}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-60"
            >恢复待处理</button>
          ) : null}
          {savedAtText ? <span className="ml-auto text-[11px] text-slate-400" data-testid="gbc-review-status-bar-saved-at">{savedAtText}</span> : null}
        </div>
      ) : actions ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onAction("confirm")}
            disabled={isSubmitting || isConfirmed}
            data-testid={`gbc-review-issue-confirm-${problem.id}`}
            className="rounded-md border border-primary-600 bg-primary-600 px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            确认问题
          </button>
          <button
            type="button"
            onClick={() => onAction("ignore")}
            disabled={isSubmitting || isIgnored}
            data-testid={`gbc-review-issue-ignore-${problem.id}`}
            className="rounded-md border border-border bg-white px-3.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            忽略
          </button>
          <button
            type="button"
            onClick={() => onAction("note")}
            disabled={isSubmitting}
            data-testid={`gbc-review-issue-note-${problem.id}`}
            className="rounded-md border border-border bg-white px-3.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            补充意见
          </button>
          {isConfirmed || isIgnored ? (
            <button
              type="button"
              onClick={() => onAction("restore")}
              disabled={isSubmitting}
              data-testid={`gbc-review-issue-restore-${problem.id}`}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-60"
            >恢复待处理</button>
          ) : null}
          {savedAtText ? <span className="ml-auto text-[11px] text-slate-400" data-testid="gbc-review-status-bar-saved-at">{savedAtText}</span> : null}
        </div>
      ) : null}
    </footer>
  );
}

export default IssueCard;
