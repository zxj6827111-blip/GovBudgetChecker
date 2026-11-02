"use client";

import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import {
  type ManualConfirmationOverride,
  type PreflightConfirmationReason,
  type PreflightResponseLike,
  PREFLIGHT_CONFIRMATION_REASON_LABELS,
} from "./uploadCenterAdapters";
import { UploadOrganizationPicker } from "./UploadOrganizationPicker";
import type { UploadOrganizationOption } from "./uploadOrganizationOptions";

const CURRENT_YEAR = new Date().getFullYear();
const YEAR_OPTIONS = Array.from({ length: 6 }, (_, index) => CURRENT_YEAR - 3 + index);
const FIELD_CLASS = "w-full rounded-md border border-border bg-white px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400";

export interface UploadConfirmationPanelProps {
  fileName: string;
  reasons: PreflightConfirmationReason[];
  preflight?: PreflightResponseLike | null;
  onSave: (override: ManualConfirmationOverride) => void;
  onCancel: () => void;
}

/** 单文件确认值仍交给上传中心的统一校验与提交链，不改动原始预检证据。 */
export function UploadConfirmationPanel({ fileName, reasons, preflight, onSave, onCancel }: UploadConfirmationPanelProps) {
  const id = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [reportYear, setReportYear] = useState("");
  const [docType, setDocType] = useState("");
  const [selected, setSelected] = useState<UploadOrganizationOption | null>(null);
  const needsYear = reasons.includes("missing_report_year");
  const needsDocType = reasons.includes("missing_doc_type");
  const needsOrg = reasons.includes("low_confidence_org") || reasons.includes("organization_scope_unconfirmed");
  const onlyOrg = needsOrg && !needsYear && !needsDocType;
  const canSave = (!needsYear || Boolean(reportYear)) && (!needsDocType || Boolean(docType)) && (!needsOrg || Boolean(selected));

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    // 原生模态提供 Escape 与焦点约束，避免窄卡片限制宽度和背景文件误操作。
    dialog?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  function handleSave() {
    if (!canSave) return;
    onSave({
      reportYear: needsYear ? reportYear : undefined,
      docType: needsDocType ? docType : undefined,
      organizationId: needsOrg ? selected?.id : undefined,
      organizationName: needsOrg ? selected?.name : undefined,
      organizationPath: needsOrg ? selected?.path : undefined,
      organizationLevel: needsOrg ? selected?.level : undefined,
    });
  }

  return (
    <dialog ref={dialogRef} aria-labelledby={`${id}-title`}
      onCancel={(event) => { event.preventDefault(); onCancel(); }}
      className="fixed inset-y-0 left-auto right-0 m-0 h-[100dvh] max-h-none w-full max-w-[720px] border-0 border-l border-border bg-white p-0 text-slate-900 shadow-xl backdrop:bg-slate-900/30"
      data-testid="gbc-upload-confirmation-panel">
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-5 py-4 sm:px-6">
          <h2 id={`${id}-title`} className="text-lg font-semibold">{onlyOrg ? "确认材料归属" : "补齐材料信息"}</h2>
          <button type="button" onClick={onCancel} aria-label="关闭材料确认"
            className="rounded-md p-2 text-slate-500 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-5 sm:px-6">
          <div>
            <p className="break-words text-sm font-medium">{fileName}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {reasons.map((reason) => <Badge key={reason} tone="lowconf">{PREFLIGHT_CONFIRMATION_REASON_LABELS[reason]}</Badge>)}
            </div>
          </div>
          {needsOrg ? (
            <div className="rounded-lg border border-warning-200 bg-warning-50 p-3 text-sm text-warning-700">
              {reasons.includes("organization_scope_unconfirmed")
                ? "请按原件确认属于部门汇总还是单位材料，两者分别归档。"
                : "请核对相关候选并选择归属，也可以搜索其他组织。"}
            </div>
          ) : null}
          {needsYear || needsDocType ? (
            <div className={`grid gap-4 ${needsYear && needsDocType ? "sm:grid-cols-2" : "grid-cols-1"}`}>
              {needsYear ? (
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor={`${id}-year`}>年份</label>
                  <select id={`${id}-year`} value={reportYear} onChange={(event) => setReportYear(event.target.value)}
                    data-testid="gbc-upload-confirm-year" className={FIELD_CLASS}>
                    <option value="">请选择年份 *</option>
                    {YEAR_OPTIONS.map((year) => <option key={year} value={String(year)}>{year}</option>)}
                  </select>
                </div>
              ) : null}
              {needsDocType ? (
                <div>
                  <label className="mb-1 block text-sm font-medium" htmlFor={`${id}-doctype`}>文档类型</label>
                  <select id={`${id}-doctype`} value={docType} onChange={(event) => setDocType(event.target.value)}
                    data-testid="gbc-upload-confirm-doctype" className={FIELD_CLASS}>
                    <option value="">请选择类型 *</option>
                    <option value="dept_budget">部门预算</option>
                    <option value="dept_final">部门决算</option>
                  </select>
                </div>
              ) : null}
            </div>
          ) : null}
          {needsOrg ? <UploadOrganizationPicker preflight={preflight} value={selected?.id ?? ""} onSelect={setSelected} /> : null}
        </div>
        <div className="shrink-0 space-y-3 border-t border-border bg-white px-5 py-4 sm:px-6">
          {needsOrg ? (
            <div aria-live="polite" data-testid="gbc-upload-org-selection" className="break-words rounded-lg bg-surface-50 p-3 text-xs text-slate-600">
              {selected ? `已选择：${selected.path} · ${selected.level === "unit" ? "单位材料" : "部门汇总"}` : "尚未选择归属"}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs text-slate-500">仅对当前文件生效</span>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={onCancel} data-testid="gbc-upload-confirm-cancel">取消</Button>
              <Button variant="primary" size="sm" onClick={handleSave} disabled={!canSave} data-testid="gbc-upload-confirm-save">
                {onlyOrg ? "确认归属" : "保存并校验"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </dialog>
  );
}

export default UploadConfirmationPanel;
