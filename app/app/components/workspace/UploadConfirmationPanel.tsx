/**
 * UploadConfirmationPanel：前置修复 1「分析前确认闸门」的单文件补齐表单。
 *
 * 背景（独立复核发现的真实问题，任务书原文）：
 * `UploadCenterPage.tsx` 的 banner 写着"低置信度元数据将在任务进入规则分析前
 * 要求人工确认"，但这个闸门此前不存在——`canSubmit` 不阻断 `needs_confirmation`，
 * 点了徽章也不影响流程。本组件是"让文案成立"的关键一环：不仅要阻断提交，
 * 还必须提供真实可用的解决路径。
 *
 * 为什么需要单文件编辑（不仅是批量预设）：
 * 批量预设（组织/年份/文档类型）只能设置一个全局统一值，应用到本轮全部待上传
 * 文件。但真实场景里同一批文件的缺失项可能不同——例如文件 A 只是年份识别失败
 * （封面扫描质量差），文件 B 是组织匹配置信度不足（新单位、匹配库里没有），
 * 这两个文件需要补齐的字段完全不同，硬套同一个批量预设无法同时解决两者
 * （给文件 A 补的年份对文件 B 没用，反之亦然）。因此除了批量预设，
 * 还必须提供"逐文件覆盖"的入口，本组件就是这个入口。
 *
 * 补齐值的生效方式：本组件只负责收集用户输入并通过 onSave 交给父组件，
 * 真正的"状态解除"逻辑（用 applyManualConfirmationOverride 重新计算
 * derivePreflightStatus）在 UploadCenterPage 里完成——本组件不重新发明
 * 状态判定，只做表单交互。
 */
"use client";

import { useEffect, useState } from "react";

import { Badge, Button } from "@/components/ui";

import { flattenOrganizationTree } from "./OrganizationFilterSelect";
import {
  formatUnitScopeHint,
  selectDepartmentOptions,
  type ManualConfirmationOverride,
  type OrganizationRecordLike,
  type PreflightConfirmationReason,
  PREFLIGHT_CONFIRMATION_REASON_LABELS,
} from "./uploadCenterAdapters";

const CURRENT_YEAR = new Date().getFullYear();
const YEAR_OPTIONS = Array.from({ length: 6 }, (_, index) => CURRENT_YEAR - 3 + index);

interface OrganizationTreeResponse {
  tree?: Array<{ id: string; name: string; level?: string; parent_id?: string | null; children?: OrganizationTreeResponse["tree"] }>;
}

export interface UploadConfirmationPanelProps {
  fileName: string;
  reasons: PreflightConfirmationReason[];
  onSave: (override: ManualConfirmationOverride) => void;
  onCancel: () => void;
}

export function UploadConfirmationPanel({ fileName, reasons, onSave, onCancel }: UploadConfirmationPanelProps) {
  const [organizations, setOrganizations] = useState<OrganizationRecordLike[]>([]);
  const [reportYear, setReportYear] = useState("");
  const [docType, setDocType] = useState("");
  const [organizationId, setOrganizationId] = useState("");

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
          // Task 9 共享化：组织拉平统一走 OrganizationFilterSelect 的
          // flattenOrganizationTree（含 parent_id），不再本地复制一份实现。
          setOrganizations(flattenOrganizationTree(payload.tree));
        }
      } catch {
        // 保持空列表，组织下拉只显示"请选择"。
      }
    }
    void loadOrganizations();
    return () => {
      cancelled = true;
    };
  }, []);

  const needsYear = reasons.includes("missing_report_year");
  const needsDocType = reasons.includes("missing_doc_type");
  const needsOrg = reasons.includes("low_confidence_org");
  const departmentOptions = selectDepartmentOptions(organizations);

  const canSave = (!needsYear || reportYear) && (!needsDocType || docType) && (!needsOrg || organizationId);

  const handleSave = () => {
    const selectedOrg = organizations.find((org) => org.id === organizationId);
    onSave({
      reportYear: needsYear ? reportYear : undefined,
      docType: needsDocType ? docType : undefined,
      organizationId: needsOrg ? organizationId : undefined,
      organizationName: needsOrg ? selectedOrg?.name : undefined,
    });
  };

  return (
    <div
      className="mt-2 rounded-md border border-warning-200 bg-warning-50 p-3"
      data-testid="gbc-upload-confirmation-panel"
    >
      <div className="mb-2 text-xs font-medium text-warning-700">
        补齐「{fileName}」缺失的信息
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {reasons.map((reason) => (
          <Badge key={reason} tone="lowconf">
            {PREFLIGHT_CONFIRMATION_REASON_LABELS[reason]}
          </Badge>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {needsYear ? (
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-confirm-year">
              年份
            </label>
            <select
              id="gbc-confirm-year"
              value={reportYear}
              onChange={(event) => setReportYear(event.target.value)}
              data-testid="gbc-upload-confirm-year"
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <option value="">请选择年份 *</option>
              {YEAR_OPTIONS.map((year) => (
                <option key={year} value={String(year)}>
                  {year}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        {needsDocType ? (
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-confirm-doctype">
              文档类型
            </label>
            <select
              id="gbc-confirm-doctype"
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              data-testid="gbc-upload-confirm-doctype"
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <option value="">请选择类型 *</option>
              <option value="dept_budget">部门预算</option>
              <option value="dept_final">部门决算</option>
            </select>
          </div>
        ) : null}

        {needsOrg ? (
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="gbc-confirm-org">
              组织
            </label>
            <select
              id="gbc-confirm-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              data-testid="gbc-upload-confirm-org"
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <option value="">请选择组织 *</option>
              {departmentOptions.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-slate-400">{formatUnitScopeHint("")}</p>
          </div>
        ) : null}
      </div>

      <div className="mt-3 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onCancel} data-testid="gbc-upload-confirm-cancel">
          取消
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          disabled={!canSave}
          data-testid="gbc-upload-confirm-save"
        >
          保存并重新校验
        </Button>
      </div>
    </div>
  );
}

export default UploadConfirmationPanel;
