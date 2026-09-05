/**
 * MetadataTab：右栏 tab 2「元数据」（Task 6.4）。
 *
 * 这是 M2 版本留痕第一次在前端露面：rule_version/engine_version/report_kind/
 * report_year/组织/报告编号。年份未识别显示"未识别到"，禁止 2000 兜底；
 * 类型未识别显示"未识别到"，不得猜。
 */
"use client";

import type { JobDetailRecord, JobSummaryRecord } from "../../../lib/uiAdapters";
import {
  extractFindingVersions,
  formatMetadataReportKind,
  formatMetadataYear,
  formatVersionList,
} from "./reviewWorkbenchAdapters";

export interface MetadataTabProps {
  job: JobSummaryRecord;
  detail: JobDetailRecord | null;
}

function MetadataRow({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border py-2.5 text-sm">
      <span className="shrink-0 text-slate-500">{label}</span>
      <span className="text-right font-medium text-slate-900" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}

export function MetadataTab({ job, detail }: MetadataTabProps) {
  const versions = extractFindingVersions(detail);
  const reportId = String(job.structured_report_id ?? "").trim() || "未识别到";

  return (
    <div className="h-full overflow-y-auto px-4 py-3" data-testid="gbc-review-metadata-tab">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">文档信息</div>
      <MetadataRow label="报告年度" value={formatMetadataYear(job.report_year)} testId="gbc-review-metadata-year" />
      <MetadataRow label="文档类型" value={formatMetadataReportKind(job.report_kind)} testId="gbc-review-metadata-report-kind" />
      <MetadataRow
        label="所属组织"
        value={String(job.organization_name ?? "").trim() || "未关联"}
        testId="gbc-review-metadata-organization"
      />
      <MetadataRow label="报告编号" value={reportId} testId="gbc-review-metadata-report-id" />

      <div className="mb-1 mt-5 text-xs font-semibold uppercase tracking-wide text-slate-400">版本留痕（M2）</div>
      <MetadataRow
        label="规则集版本"
        value={formatVersionList(versions?.rule_versions)}
        testId="gbc-review-metadata-rule-version"
      />
      <MetadataRow
        label="引擎版本"
        value={versions?.engine_version?.trim() || "未识别到"}
        testId="gbc-review-metadata-engine-version"
      />
      <MetadataRow
        label="模型标识"
        value={formatVersionList(versions?.model_versions)}
        testId="gbc-review-metadata-model-version"
      />
      <MetadataRow
        label="提示词版本"
        value={formatVersionList(versions?.prompt_versions)}
        testId="gbc-review-metadata-prompt-version"
      />
    </div>
  );
}

export default MetadataTab;
