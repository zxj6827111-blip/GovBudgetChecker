"use client";
import { useCallback, useEffect, useState } from "react";

type Capabilities = { enabled: boolean; default_use_ai: boolean; active_profile_id: string | null; model?: string; profiles: { id: string; name: string; model: string }[] };

export function useAIAnalysisOptions() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey(old => old + 1), []);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setError("");
    fetch("/api/ai/capabilities", { cache: "no-store", signal: controller.signal }).then(async res => {
      if (!res.ok) throw new Error();
      const data = await res.json() as Capabilities;
      if (cancelled) return;
      setCapabilities(data); setEnabled(data.enabled && data.default_use_ai); setProfileId(data.active_profile_id || "");
    }).catch(() => { if (!cancelled) { setError("无法读取模型配置，请重试。"); setEnabled(false); setCapabilities(null); } });
    return () => { cancelled = true; controller.abort(); };
  }, [reloadKey]);
  return { capabilities, enabled, setEnabled, profileId, setProfileId, error, reload };
}

export function AIAnalysisOptions({ options, disabled = false }: { options: ReturnType<typeof useAIAnalysisOptions>; disabled?: boolean }) {
  const { capabilities, enabled, setEnabled, profileId, setProfileId, error, reload } = options;
  return <section className="space-y-2 rounded-md border border-slate-200 bg-white p-4" data-testid="upload-ai-options">
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <strong className="text-slate-700">本地规则检查已启用</strong>
      <label className="flex items-center gap-2"><input type="checkbox" checked={enabled} disabled={disabled || !capabilities?.enabled} onChange={e => setEnabled(e.target.checked)} />大模型检查</label>
      {enabled && capabilities?.profiles.length ? <select aria-label="本次检查模型" value={profileId} disabled={disabled} onChange={e => setProfileId(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1">{capabilities.profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select> : enabled && capabilities?.model ? <span>模型：{capabilities.model}</span> : null}
    </div>
    <p className="text-xs text-slate-500">{error || (capabilities ? capabilities.enabled ? "大模型会检查材料文本，并分别显示本地检查和大模型检查进度。" : "管理员可在系统设置中添加并启用大模型。" : "正在读取大模型配置…")}</p>
    <div className="flex gap-4 text-xs"><a className="text-blue-600 underline" href="/settings?section=settings" target="_blank" rel="noreferrer">系统设置</a><button type="button" disabled={disabled} onClick={reload} className="text-blue-600">刷新模型配置</button></div>
  </section>;
}
