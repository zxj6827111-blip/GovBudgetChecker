import { getSeverityMeta } from "@/lib/issueSeverity";
import type { Problem, ProblemEvidenceRef } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { evidenceExcerptLabel } from "@/lib/evidenceIdentity";

import ProblemPreviewFrame from "./ProblemPreviewFrame";
import { buildProblemPdfPageUrl } from "./problemPreview";

interface EvidencePanelProps {
  problem: Problem;
  onOpenViewer: () => void;
  onSelectEvidenceRef?: (refKey: string) => void;
  selectedEvidenceRefKey?: string | null;
  onIgnore?: () => void;
  isIgnoring?: boolean;
}

export default function EvidencePanel({
  problem,
  onOpenViewer,
  onSelectEvidenceRef,
  selectedEvidenceRefKey,
  onIgnore,
  isIgnoring = false,
}: EvidencePanelProps) {
  const sourcePdfUrl = buildProblemPdfPageUrl(problem.jobId, problem.page);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const evidenceRefGroups = problem.roundingCases?.map((roundingCase) => {
    let refs = (problem.evidenceRefs ?? []).filter((ref) =>
      (ref.relationId && ref.relationId === roundingCase.relationId)
      || (ref.operandId && roundingCase.operandIds.includes(ref.operandId)),
    );
    if (refs.length === 0 && problem.roundingCases?.length === 1) {
      refs = problem.evidenceRefs ?? [];
    }
    return { key: roundingCase.relationId, summary: roundingCase.summary, refs };
  }) ?? [];

  const renderEvidenceRef = (ref: ProblemEvidenceRef) => {
    const roleLabel = ref.role === "parent" ? "合计项"
      : ref.role === "child" ? "明细项"
        : ref.role;
    return (
      <button
        key={ref.key}
        type="button"
        onClick={() => onSelectEvidenceRef?.(ref.key)}
        data-testid={`task-evidence-ref-${problem.id}-${ref.key}`}
        aria-pressed={selectedEvidenceRefKey === ref.key}
        className={cn(
          "rounded-lg border px-3 py-2 text-left text-xs leading-relaxed transition-colors",
          selectedEvidenceRefKey === ref.key
            ? "border-primary-400 bg-primary-50 text-primary-800"
            : "border-slate-200 bg-white text-slate-700 hover:border-primary-300 hover:bg-primary-50/50",
        )}
      >
        <span className="font-medium">{roleLabel}</span>
        <span> · {ref.locationText}</span>
        {ref.valueText ? <span> · 金额 {ref.valueText} 万元</span> : null}
        {ref.bbox ? <span className="text-success-700"> · 可精确框选</span> : null}
      </button>
    );
  };

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden rounded-xl border border-border bg-white shadow-sm">
      <div className="flex-1 space-y-6 overflow-y-auto p-8">
        <div className="mb-2 flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium",
                problem.category === "AI 智能分析"
                  ? "border-blue-100 bg-blue-50 text-blue-600"
                  : "border-purple-100 bg-purple-50 text-purple-600"
              )}
            >
              {problem.category === "AI 智能分析" ? "AI 审查" : "本地规则"}
            </span>
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium",
                severityMeta.panelClass
              )}
            >
              {severityMeta.riskLabel}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              {problem.ruleId}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              第 {problem.page} 页
            </span>
          </div>

          {onIgnore ? (
            <button
              type="button"
              data-testid="task-ignore-issue-button"
              onClick={onIgnore}
              disabled={isIgnoring}
              className="rounded-lg border border-danger-100 bg-danger-50 px-4 py-1.5 text-sm font-medium text-danger-600 transition-colors hover:bg-danger-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isIgnoring ? "忽略中..." : "忽略此问题"}
            </button>
          ) : null}
        </div>

        <div>
          <h2 className="mb-2 text-xl font-bold leading-snug text-slate-900">{problem.title}</h2>
          <p className="text-sm text-slate-500">第 {problem.page} 页</p>
        </div>

        <div className="rounded-xl border border-slate-100 bg-slate-50 p-6">
          <h4 className="mb-4 text-base font-medium text-slate-700">问题详情</h4>
          <ul className="space-y-3 text-sm text-slate-600">
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">{problem.description}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">
                定位：{problem.location || `第 ${problem.page} 页`}
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">证据：{problem.snippet}</span>
            </li>
          </ul>
        </div>

        {problem.roundingCases && problem.roundingCases.length > 0 ? (
          <section className="rounded-xl border border-warning-200 bg-warning-50/50 p-6">
            <h4 className="mb-3 text-base font-medium text-slate-800">表内差异位置</h4>
            <div className="space-y-3">
              {evidenceRefGroups.map((group) => (
                <div key={group.key} className="rounded-lg border border-warning-200 bg-white p-3">
                  <p className="mb-2 text-sm leading-relaxed text-slate-700">{group.summary}</p>
                  {group.refs.length > 0 ? (
                    <div className="flex flex-wrap gap-2">{group.refs.map(renderEvidenceRef)}</div>
                  ) : <p className="text-xs text-slate-500">当前无法定位到对应原始单元格。</p>}
                </div>
              ))}
            </div>
          </section>
        ) : problem.evidenceRefs && problem.evidenceRefs.length > 0 ? (
          <section className="rounded-xl border border-slate-200 bg-slate-50 p-6">
            <h4 className="mb-3 text-base font-medium text-slate-800">表格定位</h4>
            <div className="flex flex-wrap gap-2">{problem.evidenceRefs.map(renderEvidenceRef)}</div>
          </section>
        ) : null}

        <div className="rounded-xl border border-danger-100 bg-danger-50/30 p-6">
          <div className="mb-1 flex items-center justify-between">
            <h4 className="text-base font-medium text-danger-900">证据预览</h4>
            <div className="flex items-center gap-4 text-sm">
              <button
                data-testid="task-open-viewer-button"
                onClick={onOpenViewer}
                className="font-medium text-primary-600 transition-colors hover:text-primary-700"
              >
                查看大图
              </button>
              {sourcePdfUrl ? (
                <a
                  href={sourcePdfUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="task-open-source-page"
                  className="font-medium text-primary-600 transition-colors hover:text-primary-700"
                >
                  打开原页
                </a>
              ) : null}
            </div>
          </div>

          <p className="mb-4 text-sm text-danger-600">
            {problem.bbox
              ? "当前截图已切换为原始 PDF 页面，并叠加红框定位问题区域。"
              : "当前问题暂无定位框，展示原始 PDF 页面的整页预览。"}
          </p>

          <ProblemPreviewFrame
            problem={problem}
            onClick={onOpenViewer}
            frameClassName="rounded-xl border border-danger-200 bg-white shadow-sm"
            canvasClassName="min-h-[260px]"
            imageClassName="max-h-[520px]"
            overlayClassName="shadow-[0_0_0_9999px_rgba(15,23,42,0.03)]"
            showHoverHint
            hoverHintText="点击查看大图"
          />
        </div>

        <div className="rounded-xl border border-slate-100 bg-slate-50 p-6">
          <h4 className="mb-4 text-base font-medium text-slate-700">{evidenceExcerptLabel(problem.sourceIdentityVerified === true)}</h4>
          <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm leading-relaxed text-slate-700 shadow-sm">
            {problem.snippet}
          </div>
        </div>
      </div>
    </div>
  );
}
