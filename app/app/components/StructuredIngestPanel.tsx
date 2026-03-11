"use client";

type StructuredReviewItem = {
  id: string;
  type?: string | null;
  severity?: string | null;
  table_code?: string | null;
  message?: string | null;
  recommended_action?: string | null;
};

type PsSyncSummary = {
  report_id?: string | null;
  department_name?: string | null;
  unit_name?: string | null;
  report_type?: string | null;
  match_mode?: string | null;
  matched_organization_id?: string | null;
  table_data_count?: number | null;
  line_item_count?: number | null;
};

export type StructuredIngestPayload = {
  status?: string | null;
  reason?: string | null;
  latest_job_id?: string | null;
  latest_filename?: string | null;
  document_version_id?: number | null;
  cleaned_at?: number | null;
  cleaned_document_version_id?: number | null;
  tables_count?: number | null;
  recognized_tables?: number | null;
  facts_count?: number | null;
  document_profile?: string | null;
  missing_optional_tables?: string[] | null;
  review_item_count?: number | null;
  low_confidence_item_count?: number | null;
  review_items?: StructuredReviewItem[] | null;
  unknown_tables?: string[] | null;
  ps_sync?: PsSyncSummary | null;
};

interface StructuredIngestPanelProps {
  payload?: StructuredIngestPayload | null;
}

function getStatusMeta(status?: string | null) {
  switch (status) {
    case "done":
      return {
        label: "结构化入库完成",
        className: "bg-emerald-100 text-emerald-700",
        description: "结构化数据已写入共享库，不影响原有规则检查链路。",
      };
    case "error":
      return {
        label: "结构化入库失败",
        className: "bg-red-100 text-red-700",
        description: "本次结构化入库失败，但规则检查结果仍可单独查看。",
      };
    case "skipped":
      return {
        label: "结构化入库跳过",
        className: "bg-gray-100 text-gray-700",
        description: "通常是数据库未连接或当前环境未启用共享库同步。",
      };
    case "warning":
      return {
        label: "结构化入库告警",
        className: "bg-amber-100 text-amber-700",
        description: "已生成结构化结果，但仍建议关注少量异常提示。",
      };
    case "cleaned":
      return {
        label: "历史入库已清理",
        className: "bg-sky-100 text-sky-700",
        description: "该历史任务对应的旧版结构化入库记录已从数据库清理，原始报告与前台合并问题清单仍保留。",
      };
    default:
      return {
        label: "结构化入库待执行",
        className: "bg-blue-100 text-blue-700",
        description: "等待 PDF 解析、九表识别和共享库同步完成。",
      };
  }
}

function getProfileLabel(profile?: string | null) {
  switch (profile) {
    case "canonical_nine_table":
      return "标准九表";
    case "execution_budget_packet":
      return "预算执行+预算表";
    case "narrative_report":
      return "预算草案报告";
    default:
      return "未识别";
  }
}

function getReportTypeLabel(reportType?: string | null) {
  switch ((reportType || "").toUpperCase()) {
    case "BUDGET":
      return "预算";
    case "FINAL":
      return "决算";
    default:
      return reportType || "未知";
  }
}

function getMatchModeLabel(mode?: string | null) {
  switch (mode) {
    case "organization_id":
      return "按上传绑定命中";
    case "name_unit":
      return "按单位名称命中";
    case "name_department":
      return "按部门名称命中";
    case "fallback_name":
      return "按名称回退建档";
    default:
      return mode || "未记录";
  }
}

function getSeverityMeta(severity?: string | null) {
  switch ((severity || "").toLowerCase()) {
    case "error":
      return {
        label: "错误",
        className: "bg-red-100 text-red-700",
      };
    case "info":
      return {
        label: "提示",
        className: "bg-blue-100 text-blue-700",
      };
    default:
      return {
        label: "提醒",
        className: "bg-amber-100 text-amber-700",
      };
  }
}

function formatCount(value?: number | null) {
  return typeof value === "number" ? value.toLocaleString() : "--";
}

function formatRecognized(recognized?: number | null, total?: number | null) {
  if (typeof recognized !== "number" && typeof total !== "number") {
    return "--";
  }
  if (typeof recognized === "number" && typeof total === "number") {
    return `${recognized}/${total}`;
  }
  return typeof recognized === "number" ? `${recognized}` : `--/${total}`;
}

export default function StructuredIngestPanel({
  payload,
}: StructuredIngestPanelProps) {
  const statusMeta = getStatusMeta(payload?.status);
  const reviewItems = Array.isArray(payload?.review_items) ? payload?.review_items : [];
  const optionalTables = Array.isArray(payload?.missing_optional_tables)
    ? payload?.missing_optional_tables
    : [];
  const psSync = payload?.ps_sync && typeof payload.ps_sync === "object" ? payload.ps_sync : null;

  return (
    <div className="bg-white/60 dark:bg-gray-800/60 backdrop-blur-md rounded-2xl border border-white/20 dark:border-gray-700/50 shadow-sm p-5 space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">结构化入库</h3>
            <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${statusMeta.className}`}>
              {statusMeta.label}
            </span>
          </div>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{statusMeta.description}</p>
        </div>
        {psSync?.report_id && (
          <div className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-900/40 dark:text-slate-300">
            报告 ID：<span className="font-mono">{psSync.report_id}</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">识别表数</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {formatRecognized(payload?.recognized_tables, payload?.tables_count)}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">结构化 facts</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {formatCount(payload?.facts_count)}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">PS 表数据</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {formatCount(psSync?.table_data_count)}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">PS 行项目</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {formatCount(psSync?.line_item_count)}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">文档画像</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {getProfileLabel(payload?.document_profile)}
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40">
          <div className="text-xs text-slate-500 dark:text-slate-400">文档版本</div>
          <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-white">
            {payload?.document_version_id ?? "--"}
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-slate-200/70 bg-white/70 p-4 dark:border-slate-700/60 dark:bg-slate-900/20">
          <div className="text-sm font-medium text-slate-900 dark:text-white">共享库落库</div>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">部门</dt>
              <dd className="text-right text-slate-900 dark:text-white">{psSync?.department_name || "--"}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">单位</dt>
              <dd className="text-right text-slate-900 dark:text-white">{psSync?.unit_name || "--"}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">报告类型</dt>
              <dd className="text-right text-slate-900 dark:text-white">{getReportTypeLabel(psSync?.report_type)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">命中方式</dt>
              <dd className="text-right text-slate-900 dark:text-white">{getMatchModeLabel(psSync?.match_mode)}</dd>
            </div>
          </dl>
        </div>

        <div className="rounded-xl border border-slate-200/70 bg-white/70 p-4 dark:border-slate-700/60 dark:bg-slate-900/20">
          <div className="text-sm font-medium text-slate-900 dark:text-white">复核状态</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">
              待复核 {formatCount(payload?.review_item_count)}
            </span>
            <span className="inline-flex items-center rounded-full bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700">
              低置信 {formatCount(payload?.low_confidence_item_count)}
            </span>
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
              可选表缺失 {optionalTables.length}
            </span>
          </div>
          {optionalTables.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {optionalTables.map((tableCode) => (
                <span
                  key={tableCode}
                  className="inline-flex items-center rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-600 dark:border-slate-700 dark:text-slate-300"
                >
                  {tableCode}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {reviewItems.length > 0 ? (
        <div className="rounded-xl border border-amber-200/70 bg-amber-50/70 p-4 dark:border-amber-900/40 dark:bg-amber-950/20">
          <div className="text-sm font-medium text-amber-800 dark:text-amber-200">结构化复核项</div>
          <div className="mt-3 space-y-3">
            {reviewItems.map((item) => {
              const severityMeta = getSeverityMeta(item.severity);
              return (
                <div
                  key={item.id}
                  className="rounded-lg border border-amber-200/70 bg-white/70 px-3 py-3 text-sm dark:border-amber-900/30 dark:bg-slate-900/30"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${severityMeta.className}`}>
                      {severityMeta.label}
                    </span>
                    {item.table_code && (
                      <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                        {item.table_code}
                      </span>
                    )}
                    {item.type && (
                      <span className="text-[11px] text-slate-500 dark:text-slate-400">{item.type}</span>
                    )}
                  </div>
                  <div className="mt-2 text-slate-800 dark:text-slate-100">{item.message || "待人工复核"}</div>
                  {item.recommended_action && (
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      建议动作：{item.recommended_action}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : payload?.status === "done" ? (
        <div className="rounded-xl border border-emerald-200/70 bg-emerald-50/70 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-200">
          当前结构化入库已通过复核判定，可直接沉淀到 PS 共享库供填报系统复用。
        </div>
      ) : payload?.status === "cleaned" ? (
        <div className="rounded-xl border border-sky-200/70 bg-sky-50/70 px-4 py-3 text-sm text-sky-700 dark:border-sky-900/40 dark:bg-sky-950/20 dark:text-sky-200">
          该历史任务原先关联的旧版结构化入库记录已被清理，当前仅保留前台合并问题清单和原始报告用于回顾。
          {typeof payload?.cleaned_document_version_id === "number"
            ? ` 已清理版本 ID：${payload.cleaned_document_version_id}。`
            : ""}
          {payload?.latest_filename ? ` 当前保留的最新报告：${payload.latest_filename}。` : ""}
        </div>
      ) : payload?.status === "skipped" ? (
        <div className="rounded-xl border border-slate-200/70 bg-slate-50/70 px-4 py-3 text-sm text-slate-600 dark:border-slate-700/50 dark:bg-slate-900/20 dark:text-slate-300">
          {payload?.reason === "not_latest_version"
            ? "\u8be5\u62a5\u544a\u4e0d\u662f\u540c\u4e00\u7ec4\u7ec7\u3001\u5e74\u5ea6\u548c\u7c7b\u578b\u4e0b\u7684\u6700\u65b0\u7248\u672c\uff0c\u5df2\u8df3\u8fc7\u6b63\u5f0f\u5165\u5e93\u3002\u5386\u53f2\u62a5\u544a\u4ecd\u4f1a\u5728\u524d\u53f0\u4fdd\u7559\u4f9b\u56de\u987e\u3002"
            : "\u5f53\u524d\u73af\u5883\u672a\u6267\u884c\u5171\u4eab\u5e93\u540c\u6b65\uff0c\u5f85\u6570\u636e\u5e93\u6216\u90e8\u7f72\u73af\u5883\u51c6\u5907\u5b8c\u6210\u540e\u53ef\u91cd\u65b0\u5165\u5e93\u3002"}
        </div>
      ) : payload?.status === "error" ? (
        <div className="rounded-xl border border-red-200/70 bg-red-50/70 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200">
          本次结构化入库未成功完成，建议先查看任务日志和数据库连接状态。
        </div>
      ) : (
        <div className="rounded-xl border border-blue-200/70 bg-blue-50/70 px-4 py-3 text-sm text-blue-700 dark:border-blue-900/40 dark:bg-blue-950/20 dark:text-blue-200">
          结构化入库信息会在 PDF 解析完成后自动刷新到这里。
        </div>
      )}
    </div>
  );
}
