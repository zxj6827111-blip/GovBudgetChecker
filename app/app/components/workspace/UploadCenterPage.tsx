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
 *
 * 前置修复 1（分析前确认闸门，决策 B——真的实现这个闸门）：
 * banner 文案写着"低置信度元数据将在任务进入规则分析前要求人工确认"，
 * 因此存在未解决的 needs_confirmation 文件时 canSubmit 必须为 false
 * （见下方 canSubmit 计算），且必须提供真实解决路径：
 * - 批量预设：应用到全部文件缺失的对应字段（只补缺失项，不覆盖已识别正确的字段）；
 * - 单文件补齐（UploadConfirmationPanel）：当同一批文件缺失项不同（例如文件 A
 *   缺年份、文件 B 缺组织）时，批量预设无法同时解决两者，因此还需要逐文件覆盖。
 * 补齐后的有效值通过 effectivePreflightFor() 统一计算，提交时使用这份有效值
 * （而非原始 preflight 响应），确保后端真正收到用户补齐后的数据，不是前端
 * 单方面把徽章改绿。
 */
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, SectionTitle } from "@/components/ui";

import { AttributionWizardPanel, type AttributionSelection } from "./AttributionWizardPanel";
import { UploadBatchPresets, type BatchPresetValues } from "./UploadBatchPresets";
import { UploadDropzone } from "./UploadDropzone";
import { UploadFileList, type UploadFileEntry } from "./UploadFileList";
import {
  applyManualConfirmationOverride,
  checkUploadLimit,
  derivePreflightStatus,
  validateAttribution,
  type ManualConfirmationOverride,
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

/** 把批量预设的组织/年份/文档类型转换成 applyManualConfirmationOverride 能消费的覆盖值。
 *  年份/文档类型直接转发；组织只有 organizationId 时（批量预设下拉未强制携带名称），
 *  organizationName 留空——不影响提交（提交只用 id），只影响 UI 展示文案的完整度。 */
function presetsToOverride(presets: BatchPresetValues): ManualConfirmationOverride {
  return {
    reportYear: presets.year || undefined,
    docType: presets.docType || undefined,
    organizationId: presets.organizationId || undefined,
  };
}

export function UploadCenterPage() {
  const [mode, setMode] = useState<UploadMode>("basic");
  const [maxUploadMb, setMaxUploadMb] = useState<number | null>(null);
  const [maxUploadPages, setMaxUploadPages] = useState<number | null>(null);
  const [entries, setEntries] = useState<UploadFileEntry[]>([]);
  const [preflightResults, setPreflightResults] = useState<Record<string, PreflightResponseLike>>({});
  /** 单文件人工补齐值：key 是 entry.id，只在用户主动通过"补齐"表单保存后才有记录。 */
  const [manualOverrides, setManualOverrides] = useState<Record<string, ManualConfirmationOverride>>({});
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
    setManualOverrides((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  /** 前置修复 1：保存单文件补齐值——只记录覆盖，不直接修改 preflightResults，
   *  真正的"有效 preflight"由 effectivePreflightFor() 在渲染/提交时统一计算，
   *  避免"原始识别结果"与"人工补齐值"两份数据源互相覆盖后无法区分。 */
  const handleManualConfirm = useCallback((entryId: string, override: ManualConfirmationOverride) => {
    setManualOverrides((prev) => ({ ...prev, [entryId]: override }));
  }, []);

  /** 计算某个文件"补齐后的有效 preflight"：先叠加批量预设，再叠加该文件的单文件
   *  人工覆盖（单文件覆盖优先级更高——用户专门为这个文件填的值，不应该被批量预设
   *  的全局值覆盖回去）。 */
  const effectivePreflightFor = useCallback(
    (entryId: string): PreflightResponseLike | undefined => {
      const raw = preflightResults[entryId];
      if (!raw) {
        return raw;
      }
      const afterPresets =
        mode === "basic" ? applyManualConfirmationOverride(raw, presetsToOverride(presets)) : raw;
      const manualOverride = manualOverrides[entryId];
      if (!manualOverride) {
        return afterPresets;
      }
      return applyManualConfirmationOverride(afterPresets, manualOverride);
    },
    [manualOverrides, mode, preflightResults, presets],
  );

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

  /** 每个文件基于"有效 preflight"（含批量预设 + 单文件补齐）重新计算的状态。
   *  这就是"补齐后状态转换必须真实生效"的核心：needs_confirmation 的解除
   *  永远来自 derivePreflightStatus 重新判定有效值，不是前端直接改一个标记位。 */
  const effectiveEntries = useMemo(
    () =>
      entries.map((entry) => {
        if (entry.status === "pending_preflight" || entry.status === "failed") {
          return entry;
        }
        const effective = effectivePreflightFor(entry.id);
        return { ...entry, status: derivePreflightStatus(effective), preflight: effective };
      }),
    [entries, effectivePreflightFor],
  );

  const hasUnresolvedConfirmation = effectiveEntries.some((entry) => entry.status === "needs_confirmation");

  const canSubmit =
    entries.length > 0 &&
    uploadLimitViolations.size === 0 &&
    !hasUnresolvedConfirmation &&
    effectiveEntries.every((entry) => entry.status !== "pending_preflight" && entry.status !== "failed") &&
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
        // 有效 preflight（含批量预设 + 单文件补齐）是本次提交唯一的取值来源，
        // 保证"用户在补齐表单里填的值"真正进入上传请求，而不是被静默丢弃。
        const effectivePreflight = effectivePreflightFor(entry.id);
        const effectiveOrgId = effectivePreflight?.current?.organization_id || targetOrgId;
        if (effectiveOrgId) {
          formData.set("org_unit_id", effectiveOrgId);
        }
        const fiscalYear = effectivePreflight?.report_year ? String(effectivePreflight.report_year) : presets.year;
        if (fiscalYear) {
          formData.append("fiscal_year", fiscalYear);
        }
        formData.append("doc_type", effectivePreflight?.doc_type || presets.docType);

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
      setManualOverrides({});
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
  }, [attribution, effectivePreflightFor, entries, mode, presets]);

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
              entries={effectiveEntries.map((entry) => ({
                ...entry,
                errorMessage: uploadLimitViolations.get(entry.id) ?? entry.errorMessage,
                status: uploadLimitViolations.has(entry.id) ? "failed" : entry.status,
              }))}
              onRemove={handleRemoveEntry}
              onManualConfirm={handleManualConfirm}
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
            {hasUnresolvedConfirmation ? (
              <p className="mt-2 font-medium text-warning-700" data-testid="gbc-upload-confirmation-blocking-notice">
                还有 {effectiveEntries.filter((entry) => entry.status === "needs_confirmation").length} 个文件需要确认后才能开始分析——
                在左侧填写批量预设，或点击文件右侧「补齐」逐个填写缺失信息。
              </p>
            ) : null}
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
