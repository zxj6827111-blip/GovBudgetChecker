"use client";

/**
 * 年度公开时限：把客户/财政通知里给的预算、决算公开截止日录进来。
 *
 * 公开时限每年随人大批准、财政批复日期变化，区里通知又常只写"编报"时间，
 * 所以系统不做任何默认推断：某一年某一层级没填，下游（采集清单、逾期判定）
 * 必须按"未知"处理，不能凭空造出逾期结论。
 */
import { useCallback, useEffect, useState } from "react";

import { Button, Card } from "@/components/ui";

type Entry = { deadline: string; source: string; note: string };
type ByYear = Record<string, Record<string, Record<string, Entry>>>;

const KINDS = ["预算", "决算"] as const;
const LEVELS = ["部门", "单位"] as const;
const LEVEL_LABEL: Record<string, string> = { 部门: "主管部门", 单位: "下属预算单位" };

function emptyEntry(): Entry {
  return { deadline: "", source: "", note: "" };
}

export function DisclosureCalendarPanel() {
  const [byYear, setByYear] = useState<ByYear>({});
  const [year, setYear] = useState<string>("");
  const [form, setForm] = useState<Record<string, Record<string, Entry>>>({});
  const [source, setSource] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "failed">("loading");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/disclosure-calendar", { cache: "no-store" });
      if (!res.ok) {
        setStatus("failed");
        return;
      }
      const data = (await res.json()) as { by_year?: ByYear };
      const stored = data.by_year ?? {};
      setByYear(stored);
      // 默认落在最近一次录入的年度（刚填过就能直接看到），没有记录才回当年。
      setYear((prev) => prev || Object.keys(stored).sort().reverse()[0] || String(new Date().getFullYear()));
      setStatus("ready");
    } catch {
      setStatus("failed");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 换年度：丢弃上一个年度的编辑态与提示，按该年度存量回填。
  useEffect(() => {
    setDirty(false);
    setNotice("");
    setError("");
  }, [year]);

  // 未在编辑时用存量回填（含首次加载与保存后的服务端回读）。
  // 不能在这里清 notice，否则"已保存 2/4"会在保存后被抹掉，用户看不到回执。
  const yearDataKey = JSON.stringify(byYear[year] ?? {});
  useEffect(() => {
    if (dirty) {
      return;
    }
    const stored = byYear[year] ?? {};
    const next: Record<string, Record<string, Entry>> = {};
    const sources: string[] = [];
    const notes: string[] = [];
    for (const kind of KINDS) {
      next[kind] = {};
      for (const level of LEVELS) {
        const row = stored[kind]?.[level];
        next[kind][level] = row ? { ...emptyEntry(), ...row } : emptyEntry();
        if (row?.source) sources.push(row.source);
        if (row?.note) notes.push(row.note);
      }
    }
    setForm(next);
    setSource(Array.from(new Set(sources))[0] || "");
    setNote(Array.from(new Set(notes))[0] || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, yearDataKey, dirty]);

  const setCell = (kind: string, level: string, patch: Partial<Entry>) => {
    setDirty(true);
    setForm((prev) => ({
      ...prev,
      [kind]: { ...prev[kind], [level]: { ...(prev[kind]?.[level] ?? emptyEntry()), ...patch } },
    }));
  };

  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    const entries = KINDS.flatMap((kind) =>
      LEVELS.map((level) => ({
        year: Number(year),
        kind,
        level,
        deadline: form[kind]?.[level]?.deadline ?? "",
        source: source.trim(),
        note: note.trim(),
      }))
    );
    try {
      const res = await fetch("/api/disclosure-calendar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(String((data as any)?.detail ?? `保存失败（${res.status}）`));
        return;
      }
      await load();
      const filled = entries.filter((e) => e.deadline).length;
      setNotice(`${year} 年度已保存：${filled}/4 项有截止日，其余按“未知”处理。`);
    } catch {
      setError("保存请求没能送到后端，请确认服务在线后重试。");
    } finally {
      setBusy(false);
    }
  }

  const years = Array.from(new Set([year, ...Object.keys(byYear)])).sort().reverse();

  return (
    <Card
      title="年度公开时限"
      desc="记录客户或财政通知下发的预算、决算公开截止日。未填的年度一律按未知处理，系统不猜测、不套用往年日期。"
      bodyClassName="space-y-4"
      data-testid="gbc-disclosure-calendar"
    >
      {status === "failed" ? (
        <div className="rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800">
          时限读取失败，稍后可点“重新读取”。
          <button className="ml-3 underline" onClick={() => void load()} type="button">重新读取</button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <label className="text-sm font-medium text-slate-700" htmlFor="gbc-cal-year">年度</label>
        <input
          id="gbc-cal-year"
          className="w-28 rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-3 py-1.5"
          inputMode="numeric"
          value={year}
          onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
        />
        {years.map((y) => (
          <button
            key={y}
            type="button"
            onClick={() => setYear(y)}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              y === year ? "bg-primary-600 text-white" : "bg-slate-100 text-slate-600"
            }`}
          >
            {y}
            {Object.keys(byYear[y] ?? {}).length ? "" : "（未录）"}
          </button>
        ))}
      </div>

      <table className="w-full border-collapse text-sm" data-testid="gbc-cal-table">
        <thead>
          <tr className="bg-surface-100 text-slate-600">
            <th className="border border-border px-3 py-2 text-left font-medium">类别</th>
            {LEVELS.map((level) => (
              <th key={level} className="border border-border px-3 py-2 text-left font-medium">
                {LEVEL_LABEL[level]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {KINDS.map((kind) => (
            <tr key={kind}>
              <th className="border border-border bg-surface-100 px-3 py-2 text-left font-medium">{kind}</th>
              {LEVELS.map((level) => (
                <td key={level} className="border border-border px-3 py-2">
                  <input
                    type="date"
                    className="w-full rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-2 py-1.5"
                    aria-label={`${year}年${kind}${LEVEL_LABEL[level]}公开截止日`}
                    value={form[kind]?.[level]?.deadline ?? ""}
                    onChange={(e) => setCell(kind, level, { deadline: e.target.value })}
                  />
                  <div className="mt-1 text-xs text-slate-500">
                    {form[kind]?.[level]?.deadline ? "已填" : "未填 → 按未知，不判逾期"}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm text-slate-600">
          依据（通知文号或名称）
          <input
            className="mt-1 w-full rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-3 py-1.5"
            placeholder="如：沪普财〔2026〕12号"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              KINDS.forEach((k) => LEVELS.forEach((l) => setCell(k, l, { source: e.target.value })));
            }}
          />
        </label>
        <label className="text-sm text-slate-600">
          备注
          <input
            className="mt-1 w-full rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-3 py-1.5"
            placeholder="如：以当年通知为准"
            value={note}
            onChange={(e) => {
              setNote(e.target.value);
              KINDS.forEach((k) => LEVELS.forEach((l) => setCell(k, l, { note: e.target.value })));
            }}
          />
        </label>
      </div>

      {error ? (
        <div className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700" data-testid="gbc-cal-error">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-md border border-success-200 bg-success-50 px-3 py-2 text-sm text-success-700" data-testid="gbc-cal-notice">
          {notice}
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          onClick={() => void save()}
          disabled={busy || year.length !== 4}
          data-testid="gbc-cal-save"
        >
          {busy ? "保存中…" : `保存 ${year} 年度`}
        </Button>
        <span className="text-xs text-slate-500">清空某一格并保存，即撤销该层级的确认。</span>
      </div>
    </Card>
  );
}

export default DisclosureCalendarPanel;
