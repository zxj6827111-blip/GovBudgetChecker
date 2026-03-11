"use client";

import { useEffect } from "react";

type CleanupJobItem = {
  job_id?: string | null;
  filename?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  report_year?: number | null;
  report_kind?: string | null;
  scope_key?: string | null;
  document_version_id?: number | null;
  structured_status?: string | null;
  latest_job_id?: string | null;
  latest_filename?: string | null;
};

type CleanupVersionItem = {
  document_version_id?: number | null;
  scope_key?: string | null;
  latest_job_id?: string | null;
  latest_filename?: string | null;
  job_count?: number | null;
  job_ids?: string[] | null;
  jobs?: CleanupJobItem[] | null;
  reason?: string | null;
};

type KeptJobItem = {
  job_id?: string | null;
  filename?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  report_year?: number | null;
  report_kind?: string | null;
  scope_key?: string | null;
  document_version_id?: number | null;
  structured_status?: string | null;
  latest_job_id?: string | null;
  latest_filename?: string | null;
};

type SkippedJobItem = {
  job_id?: string | null;
  filename?: string | null;
  scope_key?: string | null;
  document_version_id?: number | null;
  reason?: string | null;
};

export type StructuredCleanupPreviewPayload = {
  status?: string | null;
  dry_run?: boolean | null;
  department_id?: string | null;
  department_name?: string | null;
  scanned_job_count?: number | null;
  matched_job_count?: number | null;
  scope_count?: number | null;
  kept_job_count?: number | null;
  cleanup_job_count?: number | null;
  cleanup_document_version_count?: number | null;
  blocked_document_version_count?: number | null;
  skipped_job_count?: number | null;
  kept_jobs?: KeptJobItem[] | null;
  cleanup_jobs?: CleanupJobItem[] | null;
  cleanup_document_versions?: CleanupVersionItem[] | null;
  blocked_document_versions?: CleanupVersionItem[] | null;
  skipped_jobs?: SkippedJobItem[] | null;
};

interface StructuredCleanupDialogProps {
  isOpen: boolean;
  preview: StructuredCleanupPreviewPayload | null;
  isExecuting?: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}

const TEXT = {
  preview: "\u6e05\u7406\u9884\u89c8",
  scopeSuffix: "\u65e7\u7248\u7ed3\u6784\u5316\u5165\u5e93\u6e05\u7406\u786e\u8ba4",
  intro:
    "\u4e0b\u9762\u662f\u672c\u6b21\u6e05\u7406\u7684\u5b8c\u6574\u9884\u89c8\u3002\u7cfb\u7edf\u53ea\u4f1a\u5220\u9664\u6570\u636e\u5e93\u4e2d\u7684\u5386\u53f2\u7ed3\u6784\u5316\u5165\u5e93\u7248\u672c\uff0c\u4e0d\u4f1a\u5220\u9664\u539f\u59cb PDF\u3001\u5386\u53f2\u4efb\u52a1\u548c\u524d\u53f0\u95ee\u9898\u6e05\u5355\u3002",
  keepLatest: "\u4fdd\u7559\u6700\u65b0",
  keepLatestDesc: "\u6bcf\u4e2a\u90e8\u95e8/\u5e74\u5ea6/\u7c7b\u578b\u4ec5\u4fdd\u7559\u5f53\u524d\u7248\u672c",
  cleanupVersions: "\u5f85\u6e05\u7406\u7248\u672c",
  cleanupVersionsDesc: "\u5c06\u4ece\u6570\u636e\u5e93\u5220\u9664\u7684\u65e7\u7248\u5165\u5e93\u7248\u672c",
  cleanupJobs: "\u6d89\u53ca\u5386\u53f2\u4efb\u52a1",
  cleanupJobsDesc: "\u8fd9\u4e9b\u65e7\u4efb\u52a1\u4f1a\u6539\u6210\u201c\u65e7\u7248\u5165\u5e93\u5df2\u6e05\u7406\u201d",
  blocked: "\u81ea\u52a8\u4fdd\u7559",
  blockedDesc: "\u4e0e\u6700\u65b0\u4efb\u52a1\u5171\u4eab\u7248\u672c\u7684\u8bb0\u5f55\u4e0d\u4f1a\u5220\u9664",
  scannedJobs: "\u626b\u63cf\u4efb\u52a1\u6570",
  scopeCount: "\u5339\u914d\u8303\u56f4\u6570",
  comparableJobs: "\u53ef\u6bd4\u8f83\u4efb\u52a1\u6570",
  otherSkipped: "\u5176\u5b83\u8df3\u8fc7\u9879",
  keptReports: "\u4fdd\u7559\u7684\u6700\u65b0\u62a5\u544a",
  keptReportsDesc: "\u8fd9\u4e9b\u62a5\u544a\u5c06\u7ee7\u7eed\u4ee3\u8868\u6b63\u5f0f\u5165\u5e93\u7248\u672c",
  cleanupGroup: "\u5f85\u6e05\u7406\u7684\u65e7\u7248\u5165\u5e93",
  cleanupGroupDesc:
    "\u786e\u8ba4\u540e\u5c06\u5220\u9664\u8fd9\u4e9b document_version \u53ca\u5176\u7ea7\u8054\u7ed3\u6784\u5316\u6570\u636e",
  noKeptReports: "\u5f53\u524d\u6ca1\u6709\u8bc6\u522b\u5230\u53ef\u4fdd\u7559\u7684\u6b63\u5f0f\u5165\u5e93\u7248\u672c\u3002",
  noCleanup: "\u672c\u6b21\u9884\u89c8\u6ca1\u6709\u53d1\u73b0\u53ef\u6e05\u7406\u7684\u65e7\u7248\u7ed3\u6784\u5316\u5165\u5e93\u8bb0\u5f55\u3002",
  blockedGroup: "\u81ea\u52a8\u4fdd\u7559\u9879",
  blockedGroupDesc:
    "\u8fd9\u4e9b\u65e7\u4efb\u52a1\u4e0e\u6700\u65b0\u4efb\u52a1\u5171\u7528\u540c\u4e00\u4e2a\u5165\u5e93\u7248\u672c\uff0c\u7cfb\u7edf\u5df2\u81ea\u52a8\u963b\u6b62\u5220\u9664",
  skippedGroup: "\u5176\u5b83\u8df3\u8fc7\u9879",
  skippedGroupDesc: "\u8fd9\u4e9b\u4efb\u52a1\u672a\u7eb3\u5165\u672c\u6b21\u6e05\u7406\u6267\u884c",
  colFile: "\u6587\u4ef6",
  colVersion: "\u7248\u672c ID",
  colReason: "\u8df3\u8fc7\u539f\u56e0",
  close: "\u5173\u95ed",
  confirm: "\u786e\u8ba4\u6e05\u7406\u65e7\u7248\u5165\u5e93",
  executing: "\u6b63\u5728\u6e05\u7406...",
  footerReady:
    "\u786e\u8ba4\u540e\u5c06\u5b9e\u9645\u6267\u884c\u6570\u636e\u5e93\u6e05\u7406\uff0c\u5e76\u628a\u5bf9\u5e94\u5386\u53f2\u4efb\u52a1\u6807\u8bb0\u4e3a\u201c\u65e7\u7248\u5165\u5e93\u5df2\u6e05\u7406\u201d\u3002",
  footerEmpty: "\u5f53\u524d\u6ca1\u6709\u53ef\u6267\u884c\u7684\u6e05\u7406\u9879\uff0c\u53ef\u76f4\u63a5\u5173\u95ed\u9884\u89c8\u3002",
  keepTarget: "\u4fdd\u7559\u76ee\u6807",
  dept: "\u90e8\u95e8",
  org: "\u7ec4\u7ec7",
  yearUnknown: "\u5e74\u5ea6\u672a\u8bc6\u522b",
  unnamed: "\u672a\u547d\u540d\u6587\u4ef6",
  noCleanupVersion: "\u5f53\u524d\u6ca1\u6709\u53ef\u5220\u9664\u7684 document_version_id",
  cleanupShared: "\u4e0e\u6700\u65b0\u4efb\u52a1\u5171\u7528\u540c\u4e00\u5165\u5e93\u7248\u672c\uff0c\u5df2\u81ea\u52a8\u4fdd\u7559",
  cleanupDone: "\u8be5\u5386\u53f2\u4efb\u52a1\u7684\u65e7\u7248\u5165\u5e93\u8bb0\u5f55\u5df2\u6e05\u7406",
  missingScope: "\u65e0\u6cd5\u8bc6\u522b\u8be5\u4efb\u52a1\u7684\u90e8\u95e8/\u5e74\u5ea6/\u7c7b\u578b\u8303\u56f4",
  skippedDefault: "\u5df2\u8df3\u8fc7",
  yearSuffix: "\u5e74\u5ea6",
  budget: "\u9884\u7b97",
  final: "\u51b3\u7b97",
  unknownKind: "\u7c7b\u578b\u672a\u8bc6\u522b",
  affectedJobs: "\u6d89\u53ca",
  historicalJobs: "\u6761\u5386\u53f2\u4efb\u52a1",
  latestReport: "\u5f53\u524d\u4fdd\u7559\u7684\u6700\u65b0\u62a5\u544a",
};

function getReportKindLabel(kind?: string | null) {
  switch ((kind || "").toLowerCase()) {
    case "budget":
      return TEXT.budget;
    case "final":
      return TEXT.final;
    default:
      return TEXT.unknownKind;
  }
}

function getSkippedReasonLabel(reason?: string | null) {
  switch ((reason || "").trim()) {
    case "shared_with_latest_job":
      return TEXT.cleanupShared;
    case "already_cleaned":
      return TEXT.cleanupDone;
    case "missing_document_version_id":
      return TEXT.noCleanupVersion;
    case "missing_scope":
      return TEXT.missingScope;
    default:
      return reason || TEXT.skippedDefault;
  }
}

function formatCount(value?: number | null) {
  return typeof value === "number" ? value.toLocaleString() : "--";
}

function SectionCard({
  title,
  subtitle,
  count,
  tone,
}: {
  title: string;
  subtitle: string;
  count: number | null | undefined;
  tone: "slate" | "emerald" | "sky" | "amber";
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : tone === "sky"
        ? "border-sky-200 bg-sky-50 text-sky-800"
        : tone === "amber"
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-slate-200 bg-slate-50 text-slate-800";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <div className="text-xs font-semibold uppercase tracking-[0.18em] opacity-70">{title}</div>
      <div className="mt-2 text-3xl font-bold tabular-nums">{formatCount(count)}</div>
      <div className="mt-1 text-sm opacity-80">{subtitle}</div>
    </div>
  );
}

function ScopeBadge({
  year,
  kind,
  departmentName,
  organizationName,
}: {
  year?: number | null;
  kind?: string | null;
  departmentName?: string | null;
  organizationName?: string | null;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-1 font-medium text-slate-700">
        {typeof year === "number" ? `${year} ${TEXT.yearSuffix}` : TEXT.yearUnknown}
      </span>
      <span className="inline-flex items-center rounded-full bg-indigo-50 px-2 py-1 font-medium text-indigo-700">
        {getReportKindLabel(kind)}
      </span>
      {departmentName ? (
        <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-1 font-medium text-amber-700">
          {TEXT.dept}：{departmentName}
        </span>
      ) : null}
      {organizationName ? (
        <span className="inline-flex items-center rounded-full bg-sky-50 px-2 py-1 font-medium text-sky-700">
          {TEXT.org}：{organizationName}
        </span>
      ) : null}
    </div>
  );
}

function JobCard({
  item,
  accent,
}: {
  item: CleanupJobItem | KeptJobItem;
  accent: "emerald" | "sky" | "slate";
}) {
  const accentClass =
    accent === "emerald"
      ? "border-emerald-200 bg-emerald-50/50"
      : accent === "sky"
        ? "border-sky-200 bg-sky-50/50"
        : "border-slate-200 bg-white";

  return (
    <div className={`rounded-2xl border p-4 ${accentClass}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-900">
            {item.filename || TEXT.unnamed}
          </div>
          <ScopeBadge
            year={item.report_year}
            kind={item.report_kind}
            departmentName={item.department_name}
            organizationName={item.organization_name}
          />
        </div>
        <div className="text-right text-xs text-slate-500">
          <div className="font-mono">job: {String(item.job_id || "").slice(0, 8) || "--"}</div>
          <div className="mt-1 font-mono">
            version: {typeof item.document_version_id === "number" ? item.document_version_id : "--"}
          </div>
        </div>
      </div>
      {item.latest_filename && item.latest_job_id ? (
        <div className="mt-3 rounded-xl border border-white/70 bg-white/70 px-3 py-2 text-xs text-slate-600">
          {TEXT.keepTarget}：{item.latest_filename}，job {String(item.latest_job_id).slice(0, 8)}
        </div>
      ) : null}
    </div>
  );
}

function VersionGroupCard({
  item,
  tone,
}: {
  item: CleanupVersionItem;
  tone: "sky" | "amber";
}) {
  const jobs = Array.isArray(item.jobs) ? item.jobs : [];
  const toneClass =
    tone === "sky"
      ? "border-sky-200 bg-sky-50/60"
      : "border-amber-200 bg-amber-50/60";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">
            document_version_id {typeof item.document_version_id === "number" ? item.document_version_id : "--"}
          </div>
          <div className="mt-1 text-xs text-slate-600">
            {TEXT.affectedJobs} {formatCount(item.job_count)} {TEXT.historicalJobs}
          </div>
          {item.latest_filename ? (
            <div className="mt-2 text-xs text-slate-600">
              {TEXT.latestReport}：{item.latest_filename}
              {item.latest_job_id ? `，job ${String(item.latest_job_id).slice(0, 8)}` : ""}
            </div>
          ) : null}
          {item.reason ? (
            <div className="mt-2 inline-flex rounded-full bg-white/80 px-2 py-1 text-[11px] font-medium text-amber-700">
              {getSkippedReasonLabel(item.reason)}
            </div>
          ) : null}
        </div>
      </div>
      {jobs.length > 0 ? (
        <div className="mt-4 grid gap-3">
          {jobs.map((job) => (
            <JobCard
              key={`${item.document_version_id}:${job.job_id}`}
              item={job}
              accent={tone === "sky" ? "sky" : "slate"}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function StructuredCleanupDialog({
  isOpen,
  preview,
  isExecuting = false,
  onClose,
  onConfirm,
}: StructuredCleanupDialogProps) {
  useEffect(() => {
    if (!isOpen || isExecuting) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isExecuting, isOpen, onClose]);

  if (!isOpen || !preview) {
    return null;
  }

  const keptJobs = Array.isArray(preview.kept_jobs) ? preview.kept_jobs : [];
  const cleanupVersions = Array.isArray(preview.cleanup_document_versions)
    ? preview.cleanup_document_versions
    : [];
  const blockedVersions = Array.isArray(preview.blocked_document_versions)
    ? preview.blocked_document_versions
    : [];
  const skippedJobs = Array.isArray(preview.skipped_jobs) ? preview.skipped_jobs : [];
  const scopeLabel = preview.department_name || "\u5168\u5e93";
  const canConfirm = Number(preview.cleanup_document_version_count || 0) > 0 && !isExecuting;

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm"
      onClick={() => {
        if (!isExecuting) {
          onClose();
        }
      }}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-[28px] border border-white/60 bg-[#f8fafc] shadow-[0_30px_80px_rgba(15,23,42,0.28)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="relative overflow-hidden border-b border-slate-200/80 bg-[radial-gradient(circle_at_top_left,_rgba(14,165,233,0.18),_transparent_38%),linear-gradient(135deg,#ffffff,#eef6ff)] px-8 py-7">
          <div className="max-w-3xl">
            <div className="inline-flex rounded-full border border-sky-200 bg-white/80 px-3 py-1 text-xs font-semibold tracking-[0.18em] text-sky-700">
              {TEXT.preview}
            </div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-900">
              {scopeLabel}
              {TEXT.scopeSuffix}
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">{TEXT.intro}</p>
          </div>
        </div>

        <div className="overflow-y-auto px-8 py-6">
          <div className="grid gap-4 md:grid-cols-4">
            <SectionCard
              title={TEXT.keepLatest}
              subtitle={TEXT.keepLatestDesc}
              count={preview.kept_job_count}
              tone="emerald"
            />
            <SectionCard
              title={TEXT.cleanupVersions}
              subtitle={TEXT.cleanupVersionsDesc}
              count={preview.cleanup_document_version_count}
              tone="sky"
            />
            <SectionCard
              title={TEXT.cleanupJobs}
              subtitle={TEXT.cleanupJobsDesc}
              count={preview.cleanup_job_count}
              tone="slate"
            />
            <SectionCard
              title={TEXT.blocked}
              subtitle={TEXT.blockedDesc}
              count={preview.blocked_document_version_count}
              tone="amber"
            />
          </div>

          <div className="mt-5 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm text-slate-600">
            <div className="flex flex-wrap items-center gap-4">
              <span>{TEXT.scannedJobs}：{formatCount(preview.scanned_job_count)}</span>
              <span>{TEXT.scopeCount}：{formatCount(preview.scope_count)}</span>
              <span>{TEXT.comparableJobs}：{formatCount(preview.matched_job_count)}</span>
              <span>{TEXT.otherSkipped}：{formatCount(preview.skipped_job_count)}</span>
            </div>
          </div>

          <div className="mt-8">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-slate-900">{TEXT.keptReports}</h3>
              <span className="text-xs font-medium text-slate-500">{TEXT.keptReportsDesc}</span>
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {keptJobs.length > 0 ? (
                keptJobs.map((item) => (
                  <JobCard key={`kept:${item.job_id}`} item={item} accent="emerald" />
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-5 py-8 text-sm text-slate-500">
                  {TEXT.noKeptReports}
                </div>
              )}
            </div>
          </div>

          <div className="mt-8">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-slate-900">{TEXT.cleanupGroup}</h3>
              <span className="text-xs font-medium text-slate-500">{TEXT.cleanupGroupDesc}</span>
            </div>
            <div className="mt-4 grid gap-4">
              {cleanupVersions.length > 0 ? (
                cleanupVersions.map((item) => (
                  <VersionGroupCard
                    key={`cleanup:${item.document_version_id}`}
                    item={item}
                    tone="sky"
                  />
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-5 py-8 text-sm text-slate-500">
                  {TEXT.noCleanup}
                </div>
              )}
            </div>
          </div>

          {blockedVersions.length > 0 ? (
            <div className="mt-8">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-lg font-semibold text-slate-900">{TEXT.blockedGroup}</h3>
                <span className="text-xs font-medium text-slate-500">{TEXT.blockedGroupDesc}</span>
              </div>
              <div className="mt-4 grid gap-4">
                {blockedVersions.map((item) => (
                  <VersionGroupCard
                    key={`blocked:${item.document_version_id}`}
                    item={item}
                    tone="amber"
                  />
                ))}
              </div>
            </div>
          ) : null}

          {skippedJobs.length > 0 ? (
            <div className="mt-8">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-lg font-semibold text-slate-900">{TEXT.skippedGroup}</h3>
                <span className="text-xs font-medium text-slate-500">{TEXT.skippedGroupDesc}</span>
              </div>
              <div className="mt-4 rounded-2xl border border-slate-200 bg-white">
                <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,0.9fr)_minmax(0,1.4fr)] gap-4 border-b border-slate-200 px-5 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                  <div>{TEXT.colFile}</div>
                  <div>{TEXT.colVersion}</div>
                  <div>{TEXT.colReason}</div>
                </div>
                <div className="max-h-72 overflow-y-auto">
                  {skippedJobs.map((item, index) => (
                    <div
                      key={`skipped:${item.job_id || item.filename || index}`}
                      className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,0.9fr)_minmax(0,1.4fr)] gap-4 border-b border-slate-100 px-5 py-3 text-sm text-slate-700 last:border-b-0"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium">{item.filename || TEXT.unnamed}</div>
                        {item.job_id ? (
                          <div className="mt-1 font-mono text-xs text-slate-500">
                            job {String(item.job_id).slice(0, 8)}
                          </div>
                        ) : null}
                      </div>
                      <div className="font-mono text-xs text-slate-500">
                        {typeof item.document_version_id === "number" ? item.document_version_id : "--"}
                      </div>
                      <div>{getSkippedReasonLabel(item.reason)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between gap-4 border-t border-slate-200 bg-white px-8 py-5">
          <div className="text-sm text-slate-500">
            {canConfirm ? TEXT.footerReady : TEXT.footerEmpty}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={isExecuting}
              className="rounded-xl border border-slate-200 bg-slate-100 px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {TEXT.close}
            </button>
            {canConfirm ? (
              <button
                type="button"
                onClick={onConfirm}
                disabled={isExecuting}
                className="inline-flex items-center rounded-xl bg-gradient-to-r from-sky-600 to-cyan-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/30 transition-all hover:from-sky-700 hover:to-cyan-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isExecuting ? TEXT.executing : TEXT.confirm}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
