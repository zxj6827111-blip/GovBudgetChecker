/**
 * 复核页的三类状态：AI 文字检查（执行与覆盖）、整体检查（检查义务待补核）、
 * 人工复核进度（必须处理的问题与补核）。三者分开记录，不能互相替代：
 * AI 4/4 完成不代表整体检查完成，更不代表人工复核完成。
 *
 * 常驻的只有两枚状态标识（AI 检查、完成条件）；剩余条数已在页签上，这里不再
 * 重复。点开后在问题栏内一次说清三类状态、处理计数和服务端给出的全部阻塞原因。
 * 完成与否仍由服务端门禁判定。
 */
"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Info, X } from "lucide-react";

import { AiScopeBanner } from "../AiScopeBanner";
import type { AiScopeView } from "../../../lib/aiScope";

type ChipTone = "success" | "warning" | "danger" | "muted";

const CHIP_TONE_CLASSES: Record<ChipTone, string> = {
  success: "text-success-700",
  warning: "text-warning-700",
  danger: "text-danger-700",
  muted: "text-slate-500",
};

function ChipIcon({ tone }: { tone: ChipTone }) {
  if (tone === "success") return <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />;
  if (tone === "muted") return <Info className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />;
  return <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />;
}

/** 状态条上的短标签；完整说法放在悬停提示和状态说明里。 */
export function aiScopeChipLabel(scope: AiScopeView | undefined): string {
  if (!scope) return "AI 检查未记录";
  if (scope.requested === false) return "AI 检查未执行";
  if (scope.planned !== null && scope.succeeded !== null && scope.planned > 0) {
    const complete = scope.succeeded >= scope.planned && !scope.failed;
    return `AI 检查 ${scope.succeeded}/${scope.planned}${complete ? "" : " 未完成"}`;
  }
  return `AI 检查${scope.recorded ? scope.stateLabel : "未记录"}`;
}

export interface ReviewProgressCounts {
  requiredTotal: number;
  confirmed: number;
  ignored: number;
  inPackage: number;
  needsReview: number;
  pending: number;
  /** 补核块不可用（旧后端/旧数据）时为 null。 */
  obligationTotal: number | null;
  obligationPending: number | null;
}

export interface ReviewStatusChipsProps {
  aiScope?: AiScopeView;
  /** 服务端门禁是否已放行（已完成复核时也视为满足）。 */
  canComplete: boolean;
  open: boolean;
  onOpen: () => void;
}

export function ReviewStatusChips({ aiScope, canComplete, open, onOpen }: ReviewStatusChipsProps) {
  const aiTone: ChipTone = aiScope?.tone ?? "muted";
  const gateTone: ChipTone = canComplete ? "success" : "warning";
  const chipClass = (tone: ChipTone) => `inline-flex items-center gap-1 whitespace-nowrap rounded px-0.5 py-0.5 text-[11px] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${CHIP_TONE_CLASSES[tone]}`;
  return (
    // contents：两项各自参与外层换行，按钮能与它们同排。
    <div className="contents" aria-label="检查与复核状态">
      <button type="button" onClick={onOpen} aria-expanded={open} data-testid="gbc-review-state-ai" data-tone={aiTone} className={chipClass(aiTone)}
        title={aiScope ? `${aiScope.headline}。点击查看检查与复核状态` : "AI 文字检查：未记录"}>
        <ChipIcon tone={aiTone} />{aiScopeChipLabel(aiScope)}
      </button>
      <button type="button" onClick={onOpen} aria-expanded={open} data-testid="gbc-review-state-progress" data-tone={gateTone} className={chipClass(gateTone)}>
        <ChipIcon tone={gateTone} />{canComplete ? "完成条件已满足" : "完成条件未满足"}<ChevronRight className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
  );
}

export interface ReviewInfoPanelProps {
  aiScope?: AiScopeView;
  progress: ReviewProgressCounts;
  /** 服务端完成门禁给出的阻塞原因（已转成中文）。 */
  gateBlockerLines: string[];
  savedAtText: string | null;
  onClose: () => void;
  onShowRequired: () => void;
  onShowReference: () => void;
  onOpenObligations?: () => void;
  onRecheck?: () => void;
  /** 从检查范围条的“详情”进入时直接定位到 AI 文字检查一节。 */
  focusSection?: "ai" | null;
}

const BUTTON = "rounded-md border border-border px-3 py-1.5 text-xs text-slate-700 hover:bg-surface-100";
const SECTION = "border-b border-border pb-4";
const HEADING = "mb-1.5 text-sm font-semibold text-slate-900";

export function ReviewInfoPanel({
  aiScope,
  progress,
  gateBlockerLines,
  savedAtText,
  onClose,
  onShowRequired,
  onShowReference,
  onOpenObligations,
  onRecheck,
  focusSection = null,
}: ReviewInfoPanelProps) {
  const aiSectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focusSection === "ai") aiSectionRef.current?.scrollIntoView({ block: "start" });
  }, [focusSection]);
  const handled = progress.confirmed + progress.ignored + progress.inPackage;
  const obligationHandled = progress.obligationTotal !== null && progress.obligationPending !== null
    ? progress.obligationTotal - progress.obligationPending : null;
  return (
    <div
      className="absolute inset-0 z-40 flex flex-col bg-white"
      data-testid="gbc-review-info-panel"
      onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
        <strong className="text-sm font-semibold text-slate-900">检查与复核状态</strong>
        <button type="button" onClick={onClose} aria-label="关闭说明" data-testid="gbc-review-info-panel-close" className="rounded p-1 text-slate-500 hover:bg-surface-100">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 text-sm leading-relaxed text-slate-700">
        <section className={SECTION} data-testid="gbc-review-info-progress">
          <h3 className={HEADING}>人工复核：问题 {handled}/{progress.requiredTotal}{obligationHandled !== null ? ` · 补核 ${obligationHandled}/${progress.obligationTotal}` : ""}</h3>
          <p className="mb-2 text-xs text-slate-600" data-testid="gbc-review-status-bar-counts">
            正式问题：已确认 {progress.confirmed} · 已忽略 {progress.ignored} · 已进整改包 {progress.inPackage} · 待复核 {progress.needsReview} · 待处理 {progress.pending}
            {savedAtText ? <span className="ml-2 text-slate-400">{savedAtText}</span> : null}
          </p>
          {gateBlockerLines.length > 0 ? (
            <div className="mb-2 rounded-md bg-warning-50 px-3 py-2 text-xs text-warning-700" data-testid="gbc-review-gate-blockers">
              <span className="font-medium">当前无法完成复核：</span>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {gateBlockerLines.map((line) => <li key={line} data-testid="gbc-review-gate-blocker-item">{line}</li>)}
              </ul>
            </div>
          ) : (
            <p className="mb-2 text-xs text-success-700">服务端完成条件已满足。</p>
          )}
          <p className="mb-2 text-xs text-slate-500">确认，或记录理由后忽略，计为已处理；参考条目不影响完成复核。</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} onClick={onShowRequired}>查看必须处理</button>
            {onOpenObligations ? <button type="button" className={BUTTON} onClick={onOpenObligations}>查看检查补核</button> : null}
          </div>
        </section>
        <section className={SECTION}>
          <h3 className={HEADING}>整体检查</h3>
          {progress.obligationPending === null ? (
            <p>当前复核上下文没有提供检查义务信息。</p>
          ) : progress.obligationPending > 0 ? (
            <p>自动检查留下 {progress.obligationPending} 项需要人工核实的检查义务，需逐项记录查阅依据和结论。</p>
          ) : (
            <p>自动检查留下的检查义务已全部补核。</p>
          )}
          <p className="mt-1 text-xs text-slate-500">这是整体检查的待办，与 AI 文字检查是否执行完成分开记录。</p>
        </section>
        <section ref={aiSectionRef} data-testid="gbc-review-info-ai">
          <h3 className={HEADING}>AI 文字检查</h3>
          {aiScope ? (
            <AiScopeBanner scope={aiScope} testId="gbc-review-ai-scope-panel" onRecheck={aiScope.needsRecheck ? onRecheck : undefined} recheckLabel="前往任务页补检" />
          ) : <p>本次分析没有记录 AI 文字检查的执行情况。</p>}
          {aiScope?.tone === "success" && aiScope.conclusionScope === "incomplete_scope" ? (
            <p className="mt-2 text-xs text-warning-700" data-testid="gbc-review-ai-scope-incomplete-note">
              AI 文字检查本身已全部完成；“检查未完成”指整体检查仍有待补核事项，补核完成前不能得出完整结论。
            </p>
          ) : null}
          <p className="mt-2 text-xs text-slate-500">AI 候选须对照原文判断：已核实的进入“必须处理”，待核实、证据不足的在“参考”。</p>
          <button type="button" className={`${BUTTON} mt-2`} onClick={onShowReference}>查看参考条目</button>
        </section>
      </div>
    </div>
  );
}
