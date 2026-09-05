/**
 * UploadBatchPresets：基础版上传中心的「批量预设」三个下拉（组织/年份/文档类型）。
 * 对照原型图 02-upload-center.png：批量预设是可选项，应用到后续新增文件的默认值，
 * 不强制覆盖已经过 preflight 识别的字段（识别结果优先于预设，与 BatchUploadModal.tsx
 * 现有"首页识别优先于文件名猜测和默认值"的既有原则一致）。
 *
 * 修复 A1：docType 不再预填 "dept_budget"。预填会在上传决算材料时必然触发
 * 后端 422 report_type_conflict（前端提交"预算"、封面识别为"决算"），
 * 且旧实现把结构化错误丢弃成一句"上传失败"，用户完全无从排查。
 */
"use client";

import { useEffect, useState } from "react";

import { flattenOrganizationTree, type OrganizationFilterOption } from "./OrganizationFilterSelect";

export interface BatchPresetValues {
  organizationId: string;
  year: string;
  docType: "" | "dept_budget" | "dept_final";
}

export interface UploadBatchPresetsProps {
  value: BatchPresetValues;
  onChange: (value: BatchPresetValues) => void;
}

interface OrganizationTreeResponse {
  tree?: Array<{ id: string; name: string; level?: string; children?: unknown[] }>;
}

const CURRENT_YEAR = new Date().getFullYear();
/** 年份下拉候选：当前年度前后各若干年，覆盖预算/决算材料常见的申报年度范围。 */
const YEAR_OPTIONS = Array.from({ length: 6 }, (_, index) => CURRENT_YEAR - 3 + index);

export function UploadBatchPresets({ value, onChange }: UploadBatchPresetsProps) {
  const [organizationOptions, setOrganizationOptions] = useState<OrganizationFilterOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function loadOrganizations() {
      try {
        const response = await fetch("/api/organizations?stats=none", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as OrganizationTreeResponse;
        if (!cancelled) {
          setOrganizationOptions(flattenOrganizationTree(payload.tree as never));
        }
      } catch {
        // 保持空列表，下拉只显示"不预设"。
      }
    }
    void loadOrganizations();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" data-testid="gbc-upload-batch-presets">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-upload-preset-org">
          组织
        </label>
        <select
          id="gbc-upload-preset-org"
          value={value.organizationId}
          onChange={(event) => onChange({ ...value, organizationId: event.target.value })}
          data-testid="gbc-upload-preset-org"
          className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <option value="">不预设</option>
          {organizationOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {"　".repeat(option.depth)}
              {option.name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-upload-preset-year">
          年份
        </label>
        <select
          id="gbc-upload-preset-year"
          value={value.year}
          onChange={(event) => onChange({ ...value, year: event.target.value })}
          data-testid="gbc-upload-preset-year"
          className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <option value="">不预设</option>
          {YEAR_OPTIONS.map((year) => (
            <option key={year} value={String(year)}>
              {year}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-upload-preset-doctype">
          文档类型
        </label>
        <select
          id="gbc-upload-preset-doctype"
          value={value.docType}
          onChange={(event) => onChange({ ...value, docType: event.target.value as BatchPresetValues["docType"] })}
          data-testid="gbc-upload-preset-doctype"
          className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <option value="">不预设</option>
          <option value="dept_budget">部门预算</option>
          <option value="dept_final">部门决算</option>
        </select>
      </div>
    </div>
  );
}

export default UploadBatchPresets;
