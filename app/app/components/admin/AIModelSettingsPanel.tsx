"use client";

import { useEffect, useState } from "react";

type Profile = {
  id: string; name: string; provider_type: string; base_url: string; model: string;
  api_key?: string; has_api_key?: boolean; timeout_s: number; max_tokens: number;
};
type ModelSettings = {
  revision: number; enabled: boolean; default_use_ai: boolean;
  active_profile_id: string | null; profiles: Profile[];
};
const fieldClass = "mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900";

export default function AIModelSettingsPanel() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  async function load() {
    setBusy(true);
    try {
      const res = await fetch("/api/admin/ai-models", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "无法读取模型配置");
      // 200 也可能是别的 JSON：结构不符就按"读不到配置"处理。
      // 直接 setSettings 会让下面的 settings.profiles.map 抛错，把整个设置区
      // （包括同区块的其他 ConfigPanel）一起渲染崩掉。
      if (!data || !Array.isArray(data.profiles)) throw new Error("模型配置结构不符合预期");
      setSettings(data); setDirty(false); setNotice(""); setTestResults({});
    } catch (e) { setNotice(e instanceof Error ? e.message : "无法读取模型配置"); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  function change(values: Partial<ModelSettings>) {
    setSettings(old => old ? { ...old, ...values } : old); setDirty(true);
  }
  function edit(id: string, values: Partial<Profile>) {
    if (!settings) return;
    change({ profiles: settings.profiles.map(p => p.id === id ? { ...p, ...values } : p) });
    setTestResults(old => { const next = { ...old }; delete next[id]; return next; });
  }
  const payload = (p: Profile) => {
    const { has_api_key, ...data } = p;
    return data;
  };
  async function save() {
    if (!settings) return;
    setBusy(true); setNotice("");
    try {
      const res = await fetch("/api/admin/ai-models", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: settings.revision, enabled: settings.enabled, default_use_ai: settings.default_use_ai, active_profile_id: settings.active_profile_id, profiles: settings.profiles.map(payload) }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      // 服务端只返回脱敏配置；不要继续在界面内保存明文密钥。
      const { updated_at, ...saved } = data;
      setSettings(saved); setDirty(false); setNotice("已保存。新开始的检查会使用此配置，无需重启服务。");
    } catch (e) { setNotice(e instanceof Error ? e.message : "保存失败"); }
    finally { setBusy(false); }
  }
  async function test(p: Profile) {
    setTesting(p.id);
    try {
      const res = await fetch("/api/admin/ai-models/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(p)) });
      const data = await res.json();
      setTestResults(old => ({ ...old, [p.id]: res.ok && data.ok ? `${data.message} · ${data.latency_ms} ms` : data.detail || data.message || "连接失败" }));
    } catch { setTestResults(old => ({ ...old, [p.id]: "连接失败，请检查服务和网络" })); }
    finally { setTesting(null); }
  }
  if (!settings) return <section className="rounded-md border bg-white p-5"><h3 className="font-bold">大模型配置</h3><p className="mt-3 text-sm">{notice || "正在读取配置…"}</p>{!busy && <button onClick={() => void load()}>重新加载</button>}</section>;
  const disabled = busy || testing !== null;
  return <section className="space-y-4 rounded-md border border-slate-200 bg-white p-5" data-testid="ai-model-settings">
    <div><h3 className="text-lg font-bold text-slate-900">大模型配置</h3><p className="mt-1 text-sm text-slate-500">添加多个模型，并选择默认模型。已开始的检查继续使用开始时的配置。</p></div>
    <fieldset disabled={disabled} className="space-y-4 disabled:opacity-60">
      <div className="flex flex-wrap gap-5 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={settings.enabled} onChange={e => change({ enabled: e.target.checked })} />启用大模型检查</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={settings.default_use_ai} onChange={e => change({ default_use_ai: e.target.checked })} />上传时默认启用大模型检查</label>
      </div>
      <label className="block text-sm font-medium">默认模型
        <select className={fieldClass} value={settings.active_profile_id || ""} onChange={e => change({ active_profile_id: e.target.value || null })}>
          <option value="">请选择模型</option>{settings.profiles.map(p => <option key={p.id} value={p.id}>{p.name || "未命名模型"} · {p.model || "待填写"}</option>)}
        </select>
      </label>
      {settings.profiles.map(p => <div key={p.id} className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4" data-testid={`ai-profile-${p.id}`}>
        <div className="flex justify-between"><strong className="text-sm">{p.name || "新模型"}</strong><button type="button" className="text-sm text-red-600" onClick={() => change({ profiles: settings.profiles.filter(item => item.id !== p.id), active_profile_id: settings.active_profile_id === p.id ? null : settings.active_profile_id })}>删除</button></div>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm">配置名称<input className={fieldClass} value={p.name} onChange={e => edit(p.id, { name: e.target.value })} placeholder="例如：预算审校模型" /></label>
          <label className="text-sm">接口类型<select className={fieldClass} value={p.provider_type} onChange={e => edit(p.id, { provider_type: e.target.value })}><option value="openai_compat">OpenAI 兼容接口</option><option value="zhipu">智谱</option><option value="doubao">豆包 / 火山方舟</option></select></label>
          <label className="text-sm md:col-span-2">API 基础地址<input className={fieldClass} value={p.base_url} onChange={e => edit(p.id, { base_url: e.target.value })} placeholder="https://服务地址/v1" /><span className="mt-1 block text-xs text-slate-500">填写服务商提供的基础地址，通常以 /v1 结尾；不包含 /chat/completions。</span></label>
          <label className="text-sm">模型名称<input className={fieldClass} value={p.model} onChange={e => edit(p.id, { model: e.target.value })} placeholder="服务商提供的模型 ID" /></label>
          <label className="text-sm">API 密钥<input type="password" autoComplete="new-password" className={fieldClass} value={p.api_key || ""} onChange={e => edit(p.id, { api_key: e.target.value })} placeholder={p.has_api_key ? "已保存，留空保留现有密钥" : "请输入 API 密钥"} /></label>
          <label className="text-sm">单次请求超时（秒）<input type="number" min={10} max={300} className={fieldClass} value={p.timeout_s} onChange={e => edit(p.id, { timeout_s: Number(e.target.value) })} /></label>
          <label className="text-sm">最大输出 Token 数<input type="number" min={512} max={64000} className={fieldClass} value={p.max_tokens} onChange={e => edit(p.id, { max_tokens: Number(e.target.value) })} /></label>
        </div>
        <div className="flex flex-wrap items-center gap-3"><button type="button" className="rounded-md border bg-white px-3 py-2 text-sm" onClick={() => void test(p)}>{testing === p.id ? "正在测试…" : "测试连接"}</button><span className="text-xs text-slate-500">测试会发送一条简短模型请求。</span></div>
        {testResults[p.id] && <p role="status" className="text-sm text-slate-700">{testResults[p.id]}</p>}
      </div>)}
      <button type="button" className="rounded-md border px-3 py-2 text-sm" disabled={settings.profiles.length >= 20} onClick={() => {
        const id = `model-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        change({ profiles: [...settings.profiles, { id, name: "", provider_type: "openai_compat", base_url: "", model: "", timeout_s: 90, max_tokens: 4000 }], active_profile_id: settings.active_profile_id || id });
      }}>添加模型</button>
    </fieldset>
    <div className="flex items-center gap-3"><button disabled={disabled || !dirty} onClick={() => void save()} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{busy ? "保存中…" : "保存配置"}</button><button disabled={disabled} onClick={() => void load()} className="text-sm text-slate-600">重新加载</button>{dirty && <span className="text-xs text-amber-700">有未保存的修改</span>}</div>
    {notice && <p role="status" className="text-sm text-slate-700">{notice}</p>}
  </section>;
}
