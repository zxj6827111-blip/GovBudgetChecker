/**
 * 复核页的三类状态：AI 文字检查（执行与覆盖）、整体检查（未能自动核对的检查）、
 * 人工复核进度（必须处理的问题与未能自动核对的检查）。三者分开记录，不能互相替代：
 * AI 4/4 完成不代表整体检查完成，更不代表人工复核完成。
 *
 * 常驻的只有两枚状态标识（AI 检查、完成条件）；剩余条数已在页签上，这里不再
 * 重复。AI 标识同时写出未完成的专项与页码，问题队列里不再另放一条检查范围条。
 * 点开后在问题栏内一次说清三类状态、处理计数和服务端给出的全部阻塞原因。
 * 完成与否仍由服务端门禁判定。
 */
"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Info, X } from "lucide-react";

import { AiScopeBanner } from "../AiScopeBanner";
import { formatPageList, type AiScopeView } from "../../../lib/aiScope";

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

/**
 * 状态条上的短标签：count 是检查单元完成计数，where 是未检的专项与页码，或全部完成时
 * 的大模型发现条数（窄栏时只截断 where）；完整说法放在悬停提示和状态说明里。
 * 写明“单元”，避免把 4/4 读成问题条数。
 */
export function aiScopeChipParts(scope: AiScopeView | undefined, aiFindingCount?: number): { count: string; where: string } {
  if (!scope) return { count: "AI 检查未记录", where: "" };
  if (scope.requested === false) return { count: "AI 检查未执行", where: "" };
  if (scope.planned !== null && scope.succeeded !== null && scope.planned > 0) {
    const complete = scope.succeeded >= scope.planned && !scope.failed;
    const count = `AI 检查完成 ${scope.succeeded}/${scope.planned} 单元`;
    if (complete && scope.uncoveredPages.length === 0) {
      return { count, where: aiFindingCount === undefined ? "" : `发现 ${aiFindingCount} 条` };
    }
    const where = scope.failedGroups.length > 0
      ? scope.failedGroups.map((group) => `${group.specialtyLabel}${group.pagesLabel}`).join("；")
      : scope.uncoveredPages.length > 0 ? `未覆盖${formatPageList(scope.uncoveredPages)}` : "";
    return { count, where: where ? `未完成：${where}` : "未完成" };
  }
  return { count: `AI 检查${scope.recorded ? scope.stateLabel : "未记录"}`, where: "" };
}

export function aiScopeChipLabel(scope: AiScopeView | undefined, aiFindingCount?: number): string {
  const { count, where } = aiScopeChipParts(scope, aiFindingCount);
  return where ? `${count} · ${where}` : count;
}

function aiScopeChipTitle(scope: AiScopeView | undefined, aiFindingCount?: number): string {
  if (!scope) return "AI 文字检查：未记录";
  return [
    `${scope.headline}（${scope.conclusionScopeLabel}）`,
    scope.planned ? `检查单元是大模型的调用分段，不是问题条数${scope.unitBreakdown ? `：共 ${scope.planned} 个 = ${scope.unitBreakdown}` : ""}` : null,
    aiFindingCount === undefined ? null : `大模型发现 ${aiFindingCount} 条，按“必须处理 / 参考”分在两个页签`,
    ...scope.failedUnits.map((unit) => `未完成：${unit.specialtyLabel} · ${unit.pagesLabel} · ${unit.statusLabel}`),
    "点击查看检查与复核状态",
  ].filter((line): line is string => line !== null).join("\n");
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
  /** 本次大模型发现条数；没有来源分桶（旧结果）时不传。 */
  aiFindingCount?: number;
  /** 服务端门禁是否已放行（已完成复核时也视为满足）。 */
  canComplete: boolean;
  open: boolean;
  /** AI 标识打开时直接定位到 AI 文字检查一节；完成条件标识从头看。 */
  onOpen: (section: "ai" | null) => void;
}

export function ReviewStatusChips({ aiScope, aiFindingCount, canComplete, open, onOpen }: ReviewStatusChipsProps) {
  const aiTone: ChipTone = aiScope?.tone ?? "muted";
  const gateTone: ChipTone = canComplete ? "success" : "warning";
  const chipClass = (tone: ChipTone) => `inline-flex items-center gap-1 whitespace-nowrap rounded px-0.5 py-0.5 text-[11px] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${CHIP_TONE_CLASSES[tone]}`;
  const aiParts = aiScopeChipParts(aiScope, aiFindingCount);
  return (
    // 两项占外层一行的剩余宽度：窄时先截断 AI 标识里的页码，不把右侧按钮挤到第二行。
    // 换行只看这里的显式最小宽度（完成计数 + 完成条件），文字本身的长度不参与。
    <div className="flex min-w-[17rem] flex-1 items-center gap-x-3" aria-label="检查与复核状态">
      <button type="button" onClick={() => onOpen("ai")} aria-expanded={open} data-testid="gbc-review-state-ai" data-tone={aiTone} className={`${chipClass(aiTone)} min-w-0`}
        title={aiScopeChipTitle(aiScope, aiFindingCount)}>
        <ChipIcon tone={aiTone} /><span className="shrink-0">{aiParts.count}</span>
        {aiParts.where ? <span className="min-w-0 truncate" data-testid="gbc-review-state-ai-where">· {aiParts.where}</span> : null}
      </button>
      <button type="button" onClick={() => onOpen(null)} aria-expanded={open} data-testid="gbc-review-state-progress" data-tone={gateTone} className={`${chipClass(gateTone)} shrink-0`}>
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
  /** 查看参考条目；传 "ai" 时直接带上「大模型」来源筛选，避免在全量参考里翻找。 */
  onShowReference: (source?: "ai") => void;
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
  // 补核已并入「必须处理」：标题给合计，括号里再分问题与未能自动核对两类。
  const requiredHandled = handled + (obligationHandled ?? 0);
  const requiredAll = progress.requiredTotal + (progress.obligationTotal ?? 0);
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
          <h3 className={HEADING}>
            人工复核：必须处理 {requiredHandled}/{requiredAll}
            {obligationHandled !== null && progress.obligationTotal ? `（问题 ${handled}/${progress.requiredTotal} · 未能自动核对 ${obligationHandled}/${progress.obligationTotal}）` : ""}
          </h3>
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
          <p className="mb-2 text-xs text-slate-500">证据充分、定位明确的系统问题可随整份问题清单一起确认。存在歧义的线索仍需判断；系统未完成的检查需先修复或重查，整份确认不会把它们改成已核对。参考条目不影响完成复核。</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} onClick={onShowRequired}>查看必须处理</button>
          </div>
        </section>
        <section className={SECTION}>
          <h3 className={HEADING}>整体检查</h3>
          {progress.obligationPending === null ? (
            <p>当前复核上下文没有提供检查覆盖信息。</p>
          ) : progress.obligationPending > 0 ? (
            <p>有 {progress.obligationPending} 项检查尚未完成，原因和位置列在“未能自动核对”中。技术问题由系统修复或重查；只有材料本身的歧义才需要人工判断。</p>
          ) : (
            <p>未能自动核对的检查已全部处理。</p>
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
              AI 文字检查本身已全部完成；“检查未完成”指仍有未能自动核对的检查待处理，处理完前不能得出完整结论。
            </p>
          ) : null}
          <p className="mt-2 text-xs text-slate-500">AI 候选须对照原文判断：已核实的进入“必须处理”，待核实、证据不足的在“参考”。</p>
          <button type="button" className={`${BUTTON} mt-2`} onClick={() => onShowReference("ai")}>查看大模型参考条目</button>
        </section>
      </div>
    </div>
  );
}
