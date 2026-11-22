"use client";

import { useEffect, useMemo, useState } from "react";

/**
 * 正文金额校正面板（R4/R5）。
 *
 * 同一页可能存在多处正文金额目标：先按唯一目标 ID 选中；无法唯一绑定时
 * 由复核人在这里明确选择，不能拿“同页第一条”代替。面板同时给出读数、
 * 单位和科目三个可确认要素，原句与系统依据只读展示，校正依据随请求提交。
 */
export interface TextCorrectionTargetRecord {
  target_id: string;
  rule_id?: string;
  issue_ids?: string[];
  title?: string;
  page?: number;
  pos?: number;
  span?: string;
  amount_raw?: string;
  raw_is_valid?: boolean;
  unit_current?: string | null;
  suggested_unit?: string | null;
  subject?: { code?: string | null; name?: string | null } | null;
  table_basis?: Record<string, unknown> | null;
  actions?: Array<{ field: string; label: string; value?: unknown }>;
  evidence?: Array<{ page?: number; bbox?: number[] | null; quote?: string | null }>;
  affected_check_ids?: string[];
  analysis_revision?: string;
  /** R6：当前查看版本实际采用的读数（人工校正后）或原文读数。 */
  effective_value?: string | null;
  effective_source?: string | null;
  effective_correction_id?: string | null;
}

export interface TextCorrectionSubmission {
  targetId: string;
  value: string;
  unit: string;
  subjectCode: string;
  note: string;
}

function basisLabel(target: TextCorrectionTargetRecord): string | null {
  const basis = target.table_basis;
  if (!basis || typeof basis !== "object") return null;
  const page = basis.page ?? basis.physical_page;
  const subject = String(basis.subject_name ?? "").trim();
  const code = String(basis.subject_code ?? "").trim();
  const parts = [
    page ? `第${page}页` : null,
    subject || null,
    code ? `科目${code}` : null,
    basis.quote ? `原表${String(basis.quote)}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

export function TextCorrectionDialog({
  targets,
  initialTargetId,
  problemTitle,
  busy = false,
  onSubmit,
  onCancel,
}: {
  targets: TextCorrectionTargetRecord[];
  initialTargetId?: string | null;
  problemTitle?: string;
  busy?: boolean;
  onSubmit: (payload: TextCorrectionSubmission) => void;
  onCancel: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string>(
    initialTargetId && targets.some((target) => target.target_id === initialTargetId)
      ? initialTargetId
      : targets.length === 1 ? targets[0].target_id : "",
  );
  const selected = useMemo(
    () => targets.find((target) => target.target_id === selectedId) ?? null,
    [selectedId, targets],
  );
  const [value, setValue] = useState<string>(selected?.amount_raw ?? "");
  const [unit, setUnit] = useState<string>(
    selected?.unit_current || selected?.suggested_unit || "万元",
  );
  const [subjectCode, setSubjectCode] = useState<string>(
    String(selected?.subject?.code ?? ""),
  );
  const [note, setNote] = useState<string>("");

  useEffect(() => {
    setValue(selected?.amount_raw ?? "");
    setUnit(selected?.unit_current || selected?.suggested_unit || "万元");
    setSubjectCode(String(selected?.subject?.code ?? ""));
  }, [selected]);

  const amountValid = /^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$/.test(value.trim());
  const canSubmit = Boolean(selected && amountValid && !busy);
  const basis = selected ? basisLabel(selected) : null;
  // R3：原文读数本身异常（"54,65.41"、"5,465.41.00"）时完整展示并禁止提交，
  // 复核人必须对照原页显式输入合法读数后才能重算；截断前缀不再出现。
  const rawInvalid = Boolean(
    selected?.amount_raw && !/^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$/.test(String(selected.amount_raw).trim()),
  );
  const editing = value.trim() !== String(selected?.amount_raw ?? "").trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-xl rounded-lg bg-white p-4 shadow-xl" data-testid="gbc-text-correction-dialog">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900">校正正文金额</h2>
            <p className="mt-1 text-xs text-slate-500">
              {problemTitle ? `${problemTitle} · ` : ""}
              校正只改变本条的核算读数，原文披露与旧版本仍保留可查。
            </p>
          </div>
          <button type="button" className="text-sm text-slate-500" onClick={onCancel} disabled={busy}>
            关闭
          </button>
        </div>

        {targets.length > 1 ? (
          <div className="mb-3 space-y-2 rounded border border-border p-2" data-testid="gbc-text-correction-targets">
            <p className="text-xs font-medium text-slate-700">本页有多处正文金额，请选择要校正的原句：</p>
            {targets.map((target, index) => (
              <label key={target.target_id} className="flex cursor-pointer items-start gap-2 text-xs text-slate-700">
                <input
                  type="radio"
                  name="text-correction-target"
                  value={target.target_id}
                  checked={selectedId === target.target_id}
                  onChange={() => setSelectedId(target.target_id)}
                  data-testid={`gbc-text-correction-target-${index}`}
                />
                <span>
                  第 {target.page ?? "?"} 页 · {target.span || target.amount_raw || target.target_id}
                  {target.amount_raw ? `（当前读数 ${target.amount_raw}）` : ""}
                  {target.effective_source === "manual_correction" && target.effective_value
                    ? `（该版采用 ${target.effective_value}）`
                    : null}
                </span>
              </label>
            ))}
          </div>
        ) : null}

        {selected ? (
          <div className="space-y-3 text-sm">
            <div className="rounded bg-surface-50 px-3 py-2 text-xs leading-relaxed text-slate-700">
              <p className="font-medium text-slate-800">第 {selected.page ?? "?"} 页原句</p>
              <p className="mt-1" data-testid="gbc-text-correction-span">{selected.span || selected.amount_raw}</p>
              {selected.effective_value ? (
                <p className="mt-1 text-slate-600" data-testid="gbc-text-correction-effective">
                  该版实际采用读数：{selected.effective_value}
                  {selected.effective_source === "manual_correction"
                    ? `（人工校正${selected.effective_correction_id ? ` · ${selected.effective_correction_id}` : ""}）`
                    : "（原文读数）"}
                </p>
              ) : null}
              {basis ? (
                <p className="mt-1 text-slate-500" data-testid="gbc-text-correction-basis">
                  单位/科目依据：{basis}
                </p>
              ) : null}
            </div>

            <label className="block text-xs font-medium text-slate-700">
              有效读数
              <input
                value={value}
                onChange={(event) => setValue(event.target.value)}
                className="mt-1 w-full rounded border border-border px-2 py-1.5 text-sm"
                data-testid="gbc-text-correction-value"
              />
            </label>
            {rawInvalid ? (
              <p className="rounded bg-warning-50 px-2 py-1 text-xs text-warning-700" data-testid="gbc-text-correction-raw-invalid">
                原文读数「{selected?.amount_raw}」不是合法金额，系统保留原样且不会按它重算；
                请对照原页输入有效读数后才能提交。
              </p>
            ) : null}
            <label className="block text-xs font-medium text-slate-700">
              原文单位
              <select
                value={unit}
                onChange={(event) => setUnit(event.target.value)}
                className="mt-1 w-full rounded border border-border px-2 py-1.5 text-sm"
                data-testid="gbc-text-correction-unit"
              >
                <option value="万元">万元</option>
                <option value="元">元</option>
                <option value="亿元">亿元</option>
              </select>
            </label>
            <label className="block text-xs font-medium text-slate-700">
              对应科目编码（可留空）
              <input
                value={subjectCode}
                onChange={(event) => setSubjectCode(event.target.value)}
                className="mt-1 w-full rounded border border-border px-2 py-1.5 text-sm"
                placeholder="例如 2160250"
                data-testid="gbc-text-correction-subject"
              />
            </label>
            <label className="block text-xs font-medium text-slate-700">
              校正依据
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={2}
                className="mt-1 w-full rounded border border-border px-2 py-1.5 text-sm"
                placeholder="写明对照的原表页码、科目或人工确认依据"
                data-testid="gbc-text-correction-note"
              />
            </label>
            {!amountValid && value.trim() ? (
              <p className="text-xs text-warning-700">
                {rawInvalid && !editing
                  ? "原文读数异常，未输入有效读数前不能提交。"
                  : "读数需为合法金额（千分位使用半角逗号，最多一个小数点）。"}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-slate-600">请先选择要校正的原句。</p>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-border px-3 py-1.5 text-xs text-slate-700"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => {
              if (!selected) return;
              onSubmit({
                targetId: selected.target_id,
                value: value.trim(),
                unit,
                subjectCode: subjectCode.trim(),
                note: note.trim(),
              });
            }}
            className="rounded bg-primary-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="gbc-text-correction-submit"
          >
            {busy ? "提交中…" : "提交校正并重算"}
          </button>
        </div>
      </div>
    </div>
  );
}
