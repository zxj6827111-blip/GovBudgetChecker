"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { MaterialDimensionsPanel } from "./MaterialDimensionsPanel";
import { Button } from "@/components/ui";

type Scope = "unit_self" | "department_summary" | "unknown";
interface ScopeState {
  document_version_id: number; file_hash: string; analysis_basis_token: string; analysis_completed: boolean;
  suggestion: Scope | null;
  decision: { material_scope: Scope; revision: number; actor: string; note: string; evidence_reference: string; dispatch_status: string; dispatch_error?: string | null } | null;
  evidence: Array<{ dimension: string; value: string; text: string; source: string; decisive: boolean }>;
}
const labels = { unit_self: "单位材料", department_summary: "部门材料（含所属单位的汇总范围）", unknown: "暂不确定" };

function LegacyDocumentScopePanel({ jobId, onQueued }: { jobId: string; onQueued: () => void }) {
  const formId = useId();
  const [state, setState] = useState<ScopeState | null>(null);
  const [scope, setScope] = useState<Scope | "">("");
  const [note, setNote] = useState("");
  const [evidence, setEvidence] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requestId, setRequestId] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/jobs/${encodeURIComponent(jobId)}/document-scope`, { signal: controller.signal, cache: "no-store" })
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail?.message || data.detail || "口径信息暂时不可用");
        setState(data); setScope(data.decision?.material_scope ?? ""); setRequestId(crypto.randomUUID());
      }).catch(e => { if (e.name !== "AbortError") setError(String(e.message)); });
    return () => controller.abort();
  }, [jobId]);
  const conflicts = state?.evidence.filter(row => row.decisive && row.dimension === "subject_level"
    && scope !== "unknown" && scope !== "" && row.value !== (scope === "unit_self" ? "unit" : "department")) ?? [];
  // 与后端按 Unicode 字符计数，且最少长度不包含首尾空白。
  const noteLength = Array.from(note.trim()).length;
  const evidenceLength = Array.from(evidence.trim()).length;
  const validationMessages = [
    ...(!scope ? ["请选择已核实的材料口径"] : []),
    ...(noteLength < 3 ? [`确认依据至少3个字，当前${noteLength}个字`] : []),
    ...(evidenceLength < 3 ? [`证据位置或引用至少3个字，当前${evidenceLength}个字`] : []),
    ...(Array.from(note).length > 2000 ? ["确认依据不能超过2000个字符"] : []),
    ...(Array.from(evidence).length > 2000 ? ["证据位置或引用不能超过2000个字符"] : []),
    ...(conflicts.length > 0 && !acknowledge ? ["请核对并勾选与原文识别依据不同的确认项"] : []),
  ];
  async function submit() {
    if (!state || !scope || busy || validationMessages.length > 0) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/document-scope`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: requestId, document_version_id: state.document_version_id,
          file_hash: state.file_hash, analysis_basis_token: state.analysis_basis_token,
          expected_decision_revision: state.decision?.revision ?? 0, material_scope: scope,
          note, evidence_reference: evidence, acknowledge_conflicts: acknowledge }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail?.message || result.detail || "确认未保存，请刷新后重试");
      setState({ ...state, analysis_completed: false, decision: result.decision }); onQueued();
    } catch (e) { setError(e instanceof Error ? e.message : "确认失败，请稍后重试"); }
    finally { setBusy(false); }
  }
  function changed() { setRequestId(crypto.randomUUID()); }
  return <div className="shrink-0 border-b border-border bg-amber-50/50 px-4 py-3 text-sm" data-testid="gbc-document-scope-panel">
    <div className="font-medium text-slate-800">确认材料口径，自动重查相关检查</div>
    <p className="mt-1 text-xs text-slate-600">归档到哪个组织与材料的检查口径分别记录。确认一次后将完整重新分析，并保留旧结果与确认记录。</p>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    {state && <>
      <details className="mt-2 text-xs text-slate-600"><summary>查看识别依据{state.suggestion ? ` · 建议：${labels[state.suggestion]}（待确认）` : ""}</summary>
        <ul className="mt-1 list-inside list-disc">{state.evidence.map((row, index) => <li key={index}>{row.text}（{row.decisive ? "原文识别依据" : "仅供参考"}）</li>)}</ul>
        <p className="mt-1">标题体例、组织层级及“本部门／本单位”用语均不能单独代替人工确认。</p>
      </details>
      {state.decision && <p className="mt-2 text-xs">最近确认：{labels[state.decision.material_scope]} · {state.decision.actor} · {state.decision.note}；{state.decision.evidence_reference}</p>}
      {state.decision?.dispatch_error && <p role="alert" className="mt-2 text-xs text-amber-900">重算尚未投递：{state.decision.dispatch_error}。确认记录已保存，系统会重试。</p>}
      {!state.analysis_completed ? <p className="mt-2 text-xs">等待重新分析完成；如果分析失败，可使用“重新分析”重试，确认记录会保留。</p> : <>
        <select aria-label="材料口径" data-testid="gbc-document-scope-choice" value={scope} disabled={busy}
          className="mt-2 h-8 w-full rounded border border-border bg-white px-2 text-xs"
          onChange={event => { setScope(event.target.value as Scope); setAcknowledge(false); changed(); }}>
          <option value="">请选择已核实的材料口径</option>
          {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label htmlFor={`${formId}-note`} className="mt-2 block text-xs text-slate-700">确认依据（必填，至少3个字）</label>
        <input id={`${formId}-note`} aria-label="口径确认依据" aria-describedby={`${formId}-note-help`}
          aria-invalid={note.length > 0 && (noteLength < 3 || Array.from(note).length > 2000)}
          placeholder="说明你如何核实材料口径" value={note} disabled={busy}
          className="mt-1 h-8 w-full rounded border border-border px-2 text-xs"
          onChange={event => { setNote(event.target.value); changed(); }} />
        <p id={`${formId}-note-help`} className="mt-1 text-xs text-slate-600">填写实际核实依据；只填“无”不足以确认材料口径。</p>
        <label htmlFor={`${formId}-evidence`} className="mt-2 block text-xs text-slate-700">证据位置或引用（必填，至少3个字）</label>
        <input id={`${formId}-evidence`} aria-label="口径证据位置" aria-describedby={`${formId}-evidence-help`}
          aria-invalid={evidence.length > 0 && (evidenceLength < 3 || Array.from(evidence).length > 2000)}
          placeholder="填写实际页码、章节或报送说明" value={evidence} disabled={busy}
          className="mt-1 h-8 w-full rounded border border-border px-2 text-xs"
          onChange={event => { setEvidence(event.target.value); changed(); }} />
        <p id={`${formId}-evidence-help`} className="mt-1 text-xs text-slate-600">请填写已核对的原文位置或来源；没有依据时可选择“暂不确定”，并说明无法确认的原因。</p>
        {conflicts.length > 0 && <label className="mt-2 flex gap-2 text-xs text-amber-900"><input type="checkbox" checked={acknowledge}
          onChange={event => { setAcknowledge(event.target.checked); changed(); }} />选择与已有原文识别依据不同；我已核对并在确认依据中说明原因。</label>}
        <Button variant="secondary" className="mt-2" data-testid="gbc-document-scope-submit"
          aria-describedby={validationMessages.length > 0 ? `${formId}-validation` : undefined}
          disabled={busy || validationMessages.length > 0}
          onClick={() => void submit()}>{busy ? "正在提交…" : "确认口径并完整重新分析"}</Button>
        {validationMessages.length > 0 && <p id={`${formId}-validation`} role="status" aria-live="polite"
          className="mt-2 text-xs text-amber-900" data-testid="gbc-document-scope-validation">
          暂不能确认：{validationMessages.join("；")}。
        </p>}
      </>}
    </>}
  </div>;
}


export function DocumentScopePanel(props: {jobId:string;onQueued:()=>void}) {
  const [budget,setBudget]=useState(false);
  const onBudget=useCallback(()=>setBudget(true),[]);
  return budget ? <LegacyDocumentScopePanel {...props}/> : <MaterialDimensionsPanel {...props} onBudget={onBudget}/>;
}
