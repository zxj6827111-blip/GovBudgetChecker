"use client";
import { useState } from "react";

type Preview = { valid: boolean; row_count: number; preview_sha256: string; errors: { line: number; reason: string }[] };
export function ExpectedMaterialImport({ onImported }: { onImported: () => void }) {
  const [csv, setCsv] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(action: "preview" | "import") {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/materials/expected/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv_text: csv, preview_sha256: preview?.preview_sha256 ?? "" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : result.detail?.message ?? result.detail?.error ?? "导入失败，请重新预览并检查数据");
      if (action === "preview") setPreview(result);
      else { setMessage(`已导入 ${result.row_count} 条应收材料${result.replayed ? "（重复提交，未新增槽位）" : ""}。`); setPreview(null); onImported(); }
    } catch (error) { setMessage(error instanceof Error ? error.message : "请求失败"); }
    finally { setBusy(false); }
  }
  return <details className="mt-4 rounded-card border border-border bg-white p-4" data-testid="gbc-expected-import">
    <summary className="cursor-pointer text-sm font-semibold text-slate-900">导入应收材料 CSV</summary>
    <p className="mt-3 text-sm text-slate-600">下载模板并填写组织 ID、财政年度、文种、材料范围、口径和带时区的截止时间。先预览，全部通过后整批导入；部分导入不代表全区应收基线完整。</p>
    <a href="/api/materials/expected/template" download="expected_materials.csv" className="mt-2 inline-block text-sm text-primary-600" data-testid="gbc-expected-template">下载 CSV 模板</a>
    <label className="mt-3 block text-sm text-slate-600">选择 UTF-8 CSV 文件
      <input type="file" accept=".csv,text/csv" disabled={busy} data-testid="gbc-expected-file" className="ml-3" onChange={async (event) => {
        const file = event.target.files?.[0]; setPreview(null); setMessage(""); setCsv("");
        if (!file) return;
        if (file.size > 1000000) { setMessage("CSV 超过 1 MB"); return; }
        try { setCsv(await file.text()); } catch { setMessage("无法读取文件"); }
      }} />
    </label>
    <div className="mt-3 flex gap-3">
      <button type="button" disabled={busy || !csv} onClick={() => submit("preview")} className="rounded-md border border-border px-3 py-2 text-sm disabled:opacity-50" data-testid="gbc-expected-preview">校验并预览</button>
      <button type="button" disabled={busy || !preview?.valid} onClick={() => submit("import")} className="rounded-md bg-primary-600 px-3 py-2 text-sm text-white disabled:opacity-50" data-testid="gbc-expected-confirm">确认导入</button>
    </div>
    {preview ? <div className="mt-3 text-sm text-slate-700" data-testid="gbc-expected-result">
      <p>可导入 {preview.row_count} 行，错误 {preview.errors.length} 行。</p>
      {preview.errors.length ? <ul className="mt-2 list-disc pl-5">{preview.errors.map((error) => <li key={error.line}>第 {error.line} 行：{error.reason}</li>)}</ul> : null}
    </div> : null}
    {message ? <p role="status" className="mt-3 text-sm text-slate-700">{message}</p> : null}
  </details>;
}
