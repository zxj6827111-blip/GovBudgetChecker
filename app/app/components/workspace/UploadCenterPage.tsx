/**
 * UploadCenterPage：Task 5，1:1 还原 `02-upload-center.png` 与
 * `02-upload-center-v2-department-unit.png`。
 *
 * 两个必须用真实值的地方（对照任务书 5.1）：
 * 1. 上传大小/页数限制：从 `/api/config` 读 max_upload_mb / max_upload_pages，
 *    绝不能照抄原型图的"200 MB"——系统默认限制是 30MB，用户按提示传 100MB
 *    文件会被直接拒绝，是可验证的用户伤害。maxUploadMb 在配置到达前为 null，
 *    UploadDropzone 据此不渲染具体数字（不显示占位的 200）。
 * 2. "分析前确认"文案：原型图那句"系统不会把无法识别的年份写成默认值。低置信度
 *    元数据将在任务进入规则分析前要求人工确认"必须保留——它是 M1 语义的用户侧
 *    表达，且与真实行为一致（未识别年份返回 None 而非 2000，见
 *    src/services/... 与 app/lib/uiAdapters.ts 的既有不变量）。
 *
 * 校验状态接 `/api/documents/preflight`，"需要确认"只来自真实低置信度判定
 * （derivePreflightStatus()），不按文件名猜测。
 */
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, SectionTitle } from "@/components/ui";

import { AttributionWizardPanel, type AttributionSelection } from "./AttributionWizardPanel";
import { UploadBatchPresets, type BatchPresetValues } from "./UploadBatchPresets";
import { UploadDropzone } from "./UploadDropzone";
import { UploadFileList, type UploadFileEntry } from "./UploadFileList";
import {
  checkUploadLimit,
  derivePreflightStatus,
  validateAttribution,
  type PreflightResponseLike,
} from "./uploadCenterAdapters";

interface ConfigResponse {
  max_upload_mb?: number;
  max_upload_pages?: number;
}

type UploadMode = "basic" | "attribution";

function createFileEntryId(file: File): string {
  return `${file.name}::${file.size}::${file.lastModified}::${Math.random().toString(36).slice(2, 8)}`;
}

export function UploadCenterPage() {
  const [mode, setMode] = useState<UploadMode>("basic");
  const [maxUploadMb, setMaxUploadMb] = useState<number | null>(null);
  const [maxUploadPages, setMaxUploadPages] = useState<number | null>(null);
  const [entries, setEntries] = useState<UploadFileEntry[]>([]);
  const [preflightResults, setPreflightResults] = useState<Record<string, PreflightResponseLike>>({});
  const [presets, setPresets] = useState<BatchPresetValues>({ organizationId: "", year: "", docType: "dept_budget" });
  const [attribution, setAttribution] = useState<AttributionSelection>({
    departmentId: "",
    fileLevel: null,
    unitId: "",
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadConfig() {
      try {
        const response = await fetch("/api/config", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as ConfigResponse;
        if (!cancelled) {
          if (typeof payload.max_upload_mb === "number") {
            setMaxUploadMb(payload.max_upload_mb);
          }
          if (typeof payload.max_upload_pages === "number") {
            setMaxUploadPages(payload.max_upload_pages);
          }
        }
      } catch {
        // 保持 null，拖拽区不渲染具体限制数字（宁可不显示，也不显示错误的猜测值）。
      }
    }
    void loadConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const runPreflight = useCallback(async (entryId: string, file: File) => {
    try {
      const formData = new FormData();
      formData.set("file", file);
      const response = await fetch("/api/documents/preflight", { method: "POST", body: formData });
      if (!response.ok) {
        setEntries((prev) =>
          prev.map((entry) =>
            entry.id === entryId
              ? { ...entry, status: "failed", errorMessage: "首页识别请求失败" }
              : entry,
          ),
        );
        return;
      }
      const payload = (await response.json()) as PreflightResponseLike & { page_count?: number | null };
      setPreflightResults((prev) => ({ ...prev, [entryId]: payload }));
      const status = derivePreflightStatus(payload);
      setEntries((prev) =>
        prev.map((entry) =>
          entry.id === entryId
            ? { ...entry, status, pageCount: payload.page_count ?? null, errorMessage: undefined }
            : entry,
        ),
      );
    } catch {
      setEntries((prev) =>
        prev.map((entry) =>
          entry.id === entryId ? { ...entry, status: "failed", errorMessage: "首页识别请求失败" } : entry,
        ),
      );
    }
  }, []);

  const handleFilesSelected = useCallback(
    (files: File[]) => {
      const newEntries: UploadFileEntry[] = files.map((file) => ({
        id: createFileEntryId(file),
        file,
        status: "pending_preflight",
        pageCount: null,
      }));
      setEntries((prev) => [...prev, ...newEntries]);
      for (const entry of newEntries) {
        void runPreflight(entry.id, entry.file);
      }
    },
    [runPreflight],
  );

  const handleRemoveEntry = useCallback((id: string) => {
    setEntries((prev) => prev.filter((entry) => entry.id !== id));
    setPreflightResults((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const uploadLimitViolations = useMemo(() => {
    if (maxUploadMb === null || maxUploadPages === null) {
      return new Map<string, string>();
    }
    const violations = new Map<string, string>();
    for (const entry of entries) {
      const violation = checkUploadLimit(entry.file.size, maxUploadMb, entry.pageCount, maxUploadPages);
      if (violation) {
        violations.set(entry.id, violation.message);
      }
    }
    return violations;
  }, [entries, maxUploadMb, maxUploadPages]);

  const attributionValidation = useMemo(() => validateAttribution(attribution), [attribution]);

  const canSubmit =
    entries.length > 0 &&
    uploadLimitViolations.size === 0 &&
    entries.every((entry) => entry.status !== "pending_preflight" && entry.status !== "failed") &&
    (mode === "basic" || attributionValidation.isComplete);

  const handleSubmit = useCallback(async () => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const targetOrgId = mode === "attribution" ? attribution.unitId || attribution.departmentId : presets.organizationId;
      const createdJobIds: string[] = [];
      for (const entry of entries) {
        const formData = new FormData();
        formData.set("file", entry.file);
        if (targetOrgId) {
          formData.set("org_unit_id", targetOrgId);
        }
        const preflight = preflightResults[entry.id];
        const fiscalYear = preflight?.report_year ? String(preflight.report_year) : presets.year;
        if (fiscalYear) {
          formData.append("fiscal_year", fiscalYear);
        }
        formData.append("doc_type", preflight?.doc_type || presets.docType);

        const response = await fetch("/api/documents/upload", { method: "POST", body: formData });
        if (!response.ok) {
          throw new Error(`文件 ${entry.file.name} 上传失败`);
        }
        const payload = (await response.json().catch(() => ({}))) as { id?: string; job_id?: string };
        const jobId = payload.id || payload.job_id;
        if (jobId) {
          createdJobIds.push(jobId);
        }
      }
      setEntries([]);
      setPreflightResults({});
      if (createdJobIds.length === 1) {
        window.location.assign(`/queue?job=${encodeURIComponent(createdJobIds[0])}`);
      } else if (createdJobIds.length > 1) {
        window.location.assign("/queue");
      }
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "上传失败");
    } finally {
      setIsSubmitting(false);
    }
  }, [attribution, entries, mode, preflightResults, presets]);

  return (
    <div className="p-8" data-testid="gbc-upload-center-page">
      <SectionTitle
        title="上传中心"
        desc="批量校验 PDF，并在分析前预设或确认关键元数据。"
        action={
          <div className="flex items-center gap-2">
            <Button
              variant={mode === "basic" ? "primary" : "secondary"}
              onClick={() => setMode("basic")}
              data-testid="gbc-upload-mode-basic"
            >
              基础上传
            </Button>
            <Button
              variant={mode === "attribution" ? "primary" : "secondary"}
              onClick={() => setMode("attribution")}
              data-testid="gbc-upload-mode-attribution"
            >
              三步归属上传
            </Button>
          </div>
        }
      />

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <UploadDropzone onFilesSelected={handleFilesSelected} maxUploadMb={maxUploadMb} maxUploadPages={maxUploadPages} />

          {mode === "basic" ? (
            <div className="rounded-card border border-border bg-white p-4">
              <div className="mb-3 text-sm font-medium text-slate-900">批量预设（可选）</div>
              <UploadBatchPresets value={presets} onChange={setPresets} />
            </div>
          ) : (
            <AttributionWizardPanel value={attribution} onChange={setAttribution} />
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-card border border-border bg-white p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-medium text-slate-900">待上传文件（{entries.length}）</div>
            </div>
            <UploadFileList
              entries={entries.map((entry) => ({
                ...entry,
                errorMessage: uploadLimitViolations.get(entry.id) ?? entry.errorMessage,
                status: uploadLimitViolations.has(entry.id) ? "failed" : entry.status,
              }))}
              onRemove={handleRemoveEntry}
            />
          </div>

          <div
            className="rounded-md border border-warning-200 bg-warning-50 px-4 py-3 text-xs text-warning-700"
            data-testid="gbc-upload-preanalysis-banner"
          >
            <div className="font-semibold text-warning-700">分析前确认</div>
            <p className="mt-1">
              系统不会把无法识别的年份写成默认值。低置信度元数据将在任务进入规则分析前要求人工确认。
            </p>
          </div>

          {mode === "attribution" && !attributionValidation.isComplete ? (
            <div className="text-xs text-danger-600" data-testid="gbc-upload-attribution-incomplete">
              {attributionValidation.reason}
            </div>
          ) : null}

          {submitError ? (
            <div className="text-xs text-danger-600" data-testid="gbc-upload-submit-error">
              {submitError}
            </div>
          ) : null}

          <Button
            variant="primary"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || isSubmitting}
            data-testid="gbc-upload-submit"
            className="w-full"
          >
            {isSubmitting ? "正在上传…" : `开始分析 ${entries.length} 个文件`}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default UploadCenterPage;
