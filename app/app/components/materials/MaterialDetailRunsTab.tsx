"use client";

import { useMemo } from "react";

import { Badge, Card } from "@/components/ui";

import {
  buildRunRows,
  partitionRunRows,
  readSlotRunsPayload,
  type RunRowView,
  type SlotRunListResponse,
} from "./materialDetailAdapters";
import { formatMaterialTimestamp } from "@/lib/materialStatusPresentation";
import { useMaterialResource } from "./useMaterialResource";

/**
 * MaterialDetailRunsTab（页面 12 之后新增的第五个 Tab）：处理记录。
 *
 * 每一行都必须标明**当前文件版本**还是**历史文件版本**（§四十八），
 * 并且两者分成两个区块显示。理由：混在一张表里，用户会把上一版文件的运行
 * 结论当成这一版文件的结果 —— 而这正是"版本替换后仍然显示旧问题数"的根源。
 *
 * 关联口径写在页面顶部而不是埋在文档里：只按
 * `metadata.structured_ingest.document_version_id` 精确关联，
 * 没有这个字段的历史运行不会被猜到任何材料下。这份声明由后端 meta 给出
 * （`linkage_basis` / `legacy_unlinked_runs_excluded`），前端只负责展示。
 */
export interface MaterialDetailRunsTabProps {
  slotId: string;
}

function RunTable({ rows, testId }: { rows: RunRowView[]; testId: string }) {
  if (rows.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid={`${testId}-empty`}>
        没有该分类下的处理记录。
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1100px] border-collapse" data-testid={testId}>
        <thead>
          <tr>
            {["分析时间", "运行状态", "对应文件版本", "分析模式", "finding 摘要", "耗时"].map(
              (label) => (
                <th
                  key={label}
                  className="whitespace-nowrap border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
                >
                  {label}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((run, index) => (
            <tr
              key={run.jobUuid}
              data-testid={`${testId}-row-${index}`}
              data-job-uuid={run.jobUuid}
              data-current-document-version={run.isCurrentVersion ? "true" : "false"}
            >
              <td className="border-b border-border px-4 py-3 align-top text-sm text-slate-700">
                {formatMaterialTimestamp(run.completedAt ?? run.startedAt)}
              </td>
              <td className="border-b border-border px-4 py-3 align-top">
                <span data-testid={`${testId}-row-${index}-status`}>{run.statusLabel}</span>
                {run.errorSummary ? (
                  <p
                    className="mt-1 text-xs text-danger-700"
                    data-testid={`${testId}-row-${index}-error`}
                  >
                    {run.errorSummary}
                  </p>
                ) : null}
              </td>
              <td className="border-b border-border px-4 py-3 align-top">
                <Badge
                  tone={run.isCurrentVersion ? "done" : "neutral"}
                  data-testid={`${testId}-row-${index}-version`}
                >
                  {run.versionBadge}
                </Badge>
                <span className="ml-2 text-xs text-slate-500">v{run.documentVersionId}</span>
              </td>
              <td className="border-b border-border px-4 py-3 align-top text-sm text-slate-700">
                {run.mode ?? "—"}
              </td>
              <td
                className="border-b border-border px-4 py-3 align-top text-sm text-slate-700"
                data-testid={`${testId}-row-${index}-findings`}
              >
                {run.findingsSummary}
              </td>
              <td className="border-b border-border px-4 py-3 align-top text-sm text-slate-700">
                {run.elapsedLabel}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MaterialDetailRunsTab({ slotId }: MaterialDetailRunsTabProps) {
  const url = useMemo(() => `/api/materials/slots/${encodeURIComponent(slotId)}/runs`, [slotId]);
  const response = useMaterialResource<SlotRunListResponse>(url, readSlotRunsPayload);
  const rows = useMemo(
    () => buildRunRows(response.data?.data.items ?? []),
    [response.data],
  );
  const { current, history } = useMemo(() => partitionRunRows(rows), [rows]);
  const legacyExcluded = response.data?.meta?.legacy_unlinked_runs_excluded === true;

  return (
    <div className="space-y-5">
      <div
        className="rounded-card border border-border bg-surface-100 px-4 py-3 text-xs text-slate-600"
        data-testid="gbc-material-runs-linkage"
      >
        <p>
          关联口径：处理记录只包含**能够精确关联到文件版本**的运行
          （依据 <code>analysis_jobs.metadata.structured_ingest.document_version_id</code>
          等于该材料某个文件版本的 id）。
        </p>
        {legacyExcluded ? (
          <p className="mt-1" data-testid="gbc-material-runs-legacy-excluded">
            没有文件版本标识的历史运行不会出现在这里：按文件名或时间「猜」归属
            会把别的材料的结论挂到这条材料上。
          </p>
        ) : null}
      </div>

      {response.loading ? (
        <div
          className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-material-runs-loading"
        >
          正在加载处理记录…
        </div>
      ) : null}

      {response.error ? (
        <div
          className="rounded-card border border-danger-200 bg-danger-50 p-6 text-sm text-danger-700"
          data-testid="gbc-material-runs-error"
        >
          <p>处理记录加载失败：{response.error}</p>
          <button
            type="button"
            onClick={response.reload}
            className="mt-3 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
            data-testid="gbc-material-runs-retry"
          >
            重试
          </button>
        </div>
      ) : null}

      {!response.loading && !response.error ? (
        rows.length === 0 ? (
          <div
            className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
            data-testid="gbc-material-runs-empty"
          >
            <p>该材料当前没有任何可精确关联的分析运行。</p>
            <p className="mt-1 text-xs text-slate-400">
              这不等于「没处理过」：历史运行可能没有文件版本标识，因此不会被归属到任何材料。
            </p>
          </div>
        ) : (
          <>
            <Card
              title="当前文件版本的处理记录"
              desc={`${current.length} 次运行`}
              data-testid="gbc-material-runs-current"
            >
              <RunTable rows={current} testId="gbc-material-runs-current-table" />
            </Card>
            <Card
              title="历史文件版本的处理记录"
              desc={`${history.length} 次运行：这些结论属于已不是当前版本的文件，不能当成当前结果`}
              data-testid="gbc-material-runs-history"
            >
              <RunTable rows={history} testId="gbc-material-runs-history-table" />
            </Card>
          </>
        )
      ) : null}
    </div>
  );
}

export default MaterialDetailRunsTab;
