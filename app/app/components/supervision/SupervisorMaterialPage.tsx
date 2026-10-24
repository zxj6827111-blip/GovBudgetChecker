"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui";
import { displayCount, labelReview, severityLabel, verificationCounts, type MaterialDetail, type ReviewSnapshot } from "./supervisionAdapters";

export function SupervisorMaterialPage({ slotId }: { slotId: string }) {
  const search = useSearchParams(), pathname = usePathname(), router = useRouter();
  const queryString = search.toString();
  const [data, setData] = useState<MaterialDetail | null>(null), [error, setError] = useState<string | null>(null), [refresh, setRefresh] = useState(0);
  const [tab, setTab] = useState<"issues" | "history">(search.get("issue_id")?"issues":"history"), [note, setNote] = useState(""), [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState<Record<string, string>>({}), [verificationNote, setVerificationNote] = useState("");
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError(null); setChoices({});
    fetch(`/api/supervision/materials/${encodeURIComponent(slotId)}`, { cache: "no-store", signal: controller.signal }).then(async response => {
      if (response.status === 401) { router.replace(`/login?next=${encodeURIComponent(pathname + "?" + queryString)}` as Route); throw new Error("登录已失效，请重新登录"); }
      const payload = await response.json(); if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "材料记录读取失败"); return payload.data as MaterialDetail;
    }).then(value => { if (!controller.signal.aborted) setData(value); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [slotId, refresh, router, pathname, queryString]);
  async function submit(path: string, body: unknown) {
    setBusy(true); setError(null);
    try {
      const response = await fetch(`/api/supervision/materials/${encodeURIComponent(slotId)}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json(); if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "操作未完成");
      setNote(""); setVerificationNote(""); setRefresh(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); } finally { setBusy(false); }
  }
  const material = data?.material, latestRound = data?.rounds.at(-1);
  const currentReview = data?.reviews.find(r => r.session_id === material?.review_session_id);
  const previousReview = data?.reviews.find(r => r.session_id === latestRound?.feedback_session_id);
  const comparison = verificationCounts(previousReview?.findings || [], currentReview?.findings || [], choices);
  const priorFeedback = data?.reviews.filter(r => r.document_version_id !== material?.document_version_id && r.confirmed_count > 0).at(-1);
  const inputClass = "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700";
  const reviewLabel = (review: ReviewSnapshot) => review.version_number ? `第 ${review.version_number} 版文件` : "历史文件版本";
  return <div className="mx-auto max-w-[1400px] px-4 py-4 sm:px-6" data-testid="gbc-supervisor-material">
    <Link href={(/^\/(?:supervision(?:\?|\/(?:materials|issues|remediation)\?)|materials\?)/.test(search.get("returnTo") || "") ? search.get("returnTo")! : `/supervision/remediation?${search}`) as Route} className="text-sm text-primary-700">← 返回报送明细</Link>
    {material&&search.get("document_version_id")&&String(material.document_version_id)!==search.get("document_version_id")?<p className="my-4 rounded bg-warning-50 p-3 text-sm text-warning-800">文件版本已变化，原列表问题不再代表当前版本；请在历史复核记录中核对原文件结论。</p>:null}
    {error ? <div role="alert" className="my-4 rounded-card border border-danger-200 bg-danger-50 p-4 text-sm text-danger-700">{error}<Button variant="secondary" className="ml-3" onClick={() => setRefresh(n => n + 1)}>重新读取</Button></div> : null}
    {!data ? <p className="py-12 text-center text-slate-400">{error ? "材料暂不可读取" : "正在读取材料…"}</p> : material ? <>
      <header className="my-4 flex flex-wrap justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-900">{material.organization_name} · {material.fiscal_year} 年{material.report_kind === "budget" ? "预算" : "决算"}</h1><p className="mt-1 text-sm text-slate-500">{material.department_name} · {material.filename || "未上传文件"}</p></div><div className="flex flex-wrap items-center gap-2">{material.job_id ? <a href={`/api/files/${encodeURIComponent(material.job_id)}/source`} className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700">下载当前原文</a> : null}{!data.is_read_only && material.job_id ? <Link href={`/review?job=${encodeURIComponent(material.job_id)}` as Route} className="rounded-md border border-border bg-white px-3 py-2 text-sm text-primary-700">打开工作人员复核</Link> : null}</div></header>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">{[["当前确认问题", displayCount(material.confirmed_count)], ["待复核线索", displayCount(material.pending_count)], ["正式登记整改", `${material.rounds_count} 轮`], ["文件版本数", `${material.version_count} 版`]].map(([label, value]) => <div key={label} className="rounded-card border border-border bg-white p-4"><div className="text-xs text-slate-500">{label}</div><strong className="mt-2 block text-2xl font-semibold text-slate-900">{value}</strong></div>)}</div>
      <div className="mb-4 text-xs text-slate-500">{labelReview(material.review_status)} · 未完成补核 {displayCount(material.pending_checks)} 项 · 最近复核人 {material.reviewer || "未记录"} · {material.reviewed_at ? new Date(material.reviewed_at).toLocaleString("zh-CN") : "完成时间未记录"}</div>
      {material.history_incomplete ? <p className="mb-4 rounded-md bg-warning-50 p-3 text-xs text-warning-800">存在多个文件版本，但历史整改未正式登记。上传次数和分析次数不计作整改轮次，旧问题缺少身份快照时不倒推已修复数量。</p> : null}
      <div className="mb-4 flex gap-2"><Button variant={tab === "history" ? "primary" : "secondary"} onClick={() => setTab("history")}>整改与复核历程</Button><Button variant={tab === "issues" ? "primary" : "secondary"} onClick={() => setTab("issues")}>当前确认问题</Button></div>
      {tab === "issues" ? <section className="space-y-3 rounded-card border border-border bg-white p-4">{data.findings.map(f => <div key={f.issue_id} className={`border-b border-border pb-3 ${search.get("issue_id")===f.issue_id?"bg-warning-50 p-3":""}`}><div className="flex flex-wrap justify-between gap-2"><h2 className="text-sm font-medium text-slate-900">{f.title}</h2><span className="text-xs text-danger-600">{severityLabel(f.severity)}</span></div>{f.evidence_reference ? <p className="mt-2 text-xs text-slate-500">人工证据依据：{f.evidence_reference}</p> : <p className="mt-2 text-xs text-slate-400">请结合当前原文核对问题；此记录未提供经过验证的页码定位。</p>}</div>)}{!data.findings.length ? <p className="py-5 text-center text-sm text-slate-400">{material.confirmed_count ? "历史记录保留了问题计数，问题明细快照不完整" : material.analyzed ? "当前暂无人工确认问题，不代表所有检查已经完成" : "尚无可判断的当前分析结果"}</p> : null}</section> : <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,1fr)]">
        <section className="rounded-card border border-border bg-white p-4"><h2 className="mb-4 text-sm font-semibold text-slate-900">材料历程</h2>{data.reviews.map(review => {
          const round = data.rounds.find(r => r.document_version_id === review.document_version_id);
          const verified = round?.verification;
          const effective = verified?.basis_token === review.basis_token && verified?.review_session_id === review.session_id;
          return <div key={review.session_id} className="mb-5 border-l-2 border-border pl-4"><div className="flex flex-wrap justify-between gap-2"><h3 className="text-sm font-medium text-slate-900">{round ? `第 ${round.round_number} 轮整改复验` : "人工复核记录"} · {reviewLabel(review)}</h3><span className="text-xs text-slate-400">{new Date(review.reviewed_at).toLocaleString("zh-CN")}</span></div><p className="mt-2 text-sm text-slate-700">确认问题 <strong className="text-danger-600">{review.confirmed_count}</strong> 条 · 复核人 {review.reviewer}</p>{verified && effective ? <p className="mt-2 text-xs text-slate-500">人工核验已修复 {verified.fixed} 条 · 仍存在 {verified.remaining} 条 · 新增 {verified.new} 条<span className="mt-1 block">依据：{verified.note}</span></p> : round ? <p className="mt-2 text-xs text-warning-700">{verified ? "本次分析代际变化，旧复验结论仅作历史记录" : "尚未逐条确认修复结果，不以问题数量下降推断已修复"}</p> : null}{!review.snapshot_complete ? <p className="mt-2 text-xs text-slate-400">历史快照仅有计数，问题身份明细不完整。</p> : null}</div>;
        })}{data.rounds.filter(r => !data.reviews.some(s => s.document_version_id === r.document_version_id)).map(round => <div key={round.id} className="mb-4 border-l-2 border-warning-300 pl-4"><h3 className="text-sm text-slate-900">第 {round.round_number} 轮整改已提交，等待复验</h3><p className="mt-2 text-xs text-slate-500">{round.submitted_by} · {new Date(round.submitted_at).toLocaleString("zh-CN")}<span className="block">{round.note}</span></p></div>)}{!data.reviews.length && !data.rounds.length ? <p className="py-6 text-center text-sm text-slate-400">尚无人工复核或正式整改记录</p> : null}</section>
        <section className="rounded-card border border-border bg-white p-4"><h2 className="mb-3 text-sm font-semibold text-slate-900">整改记录</h2>{data.rounds.map(round => <div key={round.id} className="mb-4 border-b border-border pb-3 text-xs text-slate-500"><strong className="block text-sm font-medium text-slate-900">第 {round.round_number} 轮</strong><span className="mt-1 block">提交人：{round.submitted_by}</span><span className="block">{new Date(round.submitted_at).toLocaleString("zh-CN")}</span><p className="mt-2">{round.note}</p></div>)}<p className="text-xs text-slate-400">每轮均以原复核反馈、新版文件和正式整改登记关联。原文件重新分析不增加轮次。</p></section>
      </div>}
      {!data.is_read_only ? <section className="mt-4 rounded-card border border-border bg-white p-4"><h2 className="mb-3 text-sm font-semibold text-slate-900">工作人员整改登记</h2>{priorFeedback && !data.rounds.some(r => r.document_version_id === material.document_version_id) ? <><p className="mb-3 text-xs text-slate-500">确认当前新版文件是针对上一版复核反馈提交的整改材料。文件版本本身不会自动计为整改轮次。</p><textarea aria-label="整改提交依据" className={`${inputClass} block w-full`} value={note} onChange={e => setNote(e.target.value)} placeholder="填写正式整改提交说明或反馈依据" /><Button className="mt-3" variant="secondary" disabled={busy || note.trim().length < 4} onClick={() => submit("rounds", { document_version_id: material.document_version_id, note: note.trim() })}>登记当前版本为整改提交</Button></> : <p className="text-xs text-slate-500">{material.uploaded ? "当前版本已登记，或尚无上一版人工确认问题反馈。" : "请先上传材料。"}</p>}
        {latestRound && !latestRound.verification && latestRound.document_version_id === material.document_version_id && currentReview?.snapshot_complete && previousReview?.snapshot_complete && material.review_status === "completed" ? <div className="mt-5 border-t border-border pt-4"><h3 className="mb-3 text-sm font-medium text-slate-900">逐条核验本轮整改结果</h3>{previousReview.findings?.map(issue => <label key={issue.issue_id} className="mb-3 grid gap-2 text-xs text-slate-600 md:grid-cols-2"><span>{issue.title}</span><select className={inputClass} aria-label={`复验结论：${issue.title}`} value={choices[issue.issue_id] || ""} onChange={e => setChoices(c => ({ ...c, [issue.issue_id]: e.target.value }))}><option value="">待核验</option><option value="fixed">经原文核验，确认已修复</option>{currentReview.findings?.map(f => <option key={f.issue_id} value={f.issue_id}>仍存在：{f.title}</option>)}</select></label>)}<textarea aria-label="逐条复验的原文依据" className={`${inputClass} block w-full`} value={verificationNote} onChange={e => setVerificationNote(e.target.value)} placeholder="填写核验依据，包括新版原文、表名或章节等证据指引。" /><p className="my-3 text-xs text-slate-500">记录已修复 {comparison.fixed} 条；完成旧问题关联后，未关联的本轮确认问题标记为新增。请确认不存在漏配。</p><Button variant="secondary" disabled={busy || !comparison.complete || verificationNote.trim().length < 8} onClick={() => submit(`rounds/${latestRound.id}/verify`, { review_session_id: currentReview.session_id, comparisons: comparison.comparisons, note: verificationNote.trim() })}>保存逐条复验结论</Button></div> : null}
      </section> : null}
    </> : null}
  </div>;
}
