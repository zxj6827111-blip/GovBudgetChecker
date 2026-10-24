"use client";
import Link from "next/link";
import type {Route} from "next";
import {usePathname,useRouter,useSearchParams} from "next/navigation";
import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {ReportingTaskRoster} from "./ReportingTaskRoster";
import {ReportingIdentityPanel} from "./ReportingIdentityPanel";
import {reportingCount as count,reportingRatio,reportingYear,reportingRequest,reportingLink,type ReportingRegion,type ReportingRow,type ReportingStatistics,type ReportingView,type ReportingMetrics} from "./reportingTaskAdapters";

const input = "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700";
const panel = "rounded-card border border-border bg-white p-4 shadow-ui";
const labels:Record<ReportingView,string> = {overview:"区域总览",departments:"主管部门",subjects:"部门与单位明细",issues:"人工确认问题",leads:"待复核线索",extras:"清单外材料",roster:"应交名单管理",remediation:"整改追踪"};

function Metric({label,value,context,onClick}:{label:string;value:number|null|undefined;context?:string;onClick:()=>void}) {
  return <button className={`${panel} text-left hover:border-primary-400`} onClick={onClick}><span className="block text-xs text-slate-500">{label}</span><strong className="my-2 block text-3xl font-semibold tabular-nums text-slate-900">{count(value)}</strong>{context?<span className="text-xs text-slate-500">{context}</span>:null}</button>;
}

export function ReportingTaskPage({supervisor=false,initialView="overview",canManage=false}:{supervisor?:boolean;initialView?:ReportingView;canManage?:boolean}) {
  const search=useSearchParams(), pathname=usePathname(), router=useRouter();
  const query=search.toString(), latest=useRef(query);
  useEffect(()=>{latest.current=query},[query]);
  const year=reportingYear(search.get("reporting_year"));
  const [regions,setRegions]=useState<ReportingRegion[]>([]),[regionKey,setRegionKey]=useState("");
  const [error,setError]=useState(""),[loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0),[busy,setBusy]=useState(false);
  const [stored,setStored]=useState<ReportingStatistics|null>(null),[loadedKey,setLoadedKey]=useState("");
  const [keyword,setKeyword]=useState(search.get("keyword")||"");
  const [identityOpen,setIdentityOpen]=useState(false);
  const update=useCallback((fields:Record<string,string>)=>{
    const link=reportingLink(pathname,new URLSearchParams(latest.current),fields);
    latest.current=link.split("?")[1]||""; router.replace(link as Route);
  },[pathname,router]);
  useEffect(()=>{setKeyword(search.get("keyword")||"")},[query,search]);
  useEffect(()=>{
    const controller=new AbortController(); setError("");setLoading(true);
    reportingRequest<{regions:ReportingRegion[]}>(`/api/reporting-tasks?reporting_year=${year}`,{signal:controller.signal}).then(body=>{
      if(!controller.signal.aborted){setRegions(body.regions);setRegionKey(String(year));setLoading(false)}
    }).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false)}});
    return()=>controller.abort();
  },[year,refresh]);
  const available=regionKey===String(year)?regions:[];
  const requested=search.get("jurisdiction_id"), requestedTask=search.get("task_id");
  const region=available.find(r=>r.id===requested) || available.find(r=>r.task?.id===requestedTask) || available[0];
  const task=region?.task, regionId=region?.id;
  useEffect(()=>{
    if(regionId && (!requested || requested!==regionId || (task?.id||"")!==(requestedTask||""))) update({jurisdiction_id:regionId,task_id:task?.id||"",reporting_year:String(year)});
  },[regionId,task?.id,requested,requestedTask,year,update]);
  const chosenView=(search.get("view")||initialView) as ReportingView;
  const view=Object.hasOwn(labels,chosenView)?chosenView:initialView;
  const component=["budget","final"].includes(search.get("component")||"")?search.get("component")!:"all";
  const statsQuery=useMemo(()=>{
    const p=new URLSearchParams();p.set("component",component);
    p.set("view",view==="overview"?"departments":view==="roster"?"subjects":view);
    for(const key of ["status","keyword","department_id","subject_id","page"])if(search.get(key))p.set(key,search.get(key)!);
    p.set("page_size","20");return p.toString();
  },[component,view,search]);
  const statsUrl=task?`/api/reporting-tasks/${task.id}/statistics?${statsQuery}`:"";
  useEffect(()=>{
    if(!statsUrl){setStored(null);return}
    const controller=new AbortController();setStored(null);setError("");setLoading(true);
    reportingRequest<ReportingStatistics>(statsUrl,{signal:controller.signal}).then(body=>{
      if(!controller.signal.aborted){setStored(body);setLoadedKey(statsUrl);setLoading(false)}
    }).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false)}});
    return()=>controller.abort();
  },[statsUrl,refresh]);
  useEffect(()=>{
    if(view==="roster")return;
    const tick=()=>setRefresh(n=>n+1), timer=window.setInterval(tick,60000);
    window.addEventListener("focus",tick);return()=>{window.clearInterval(timer);window.removeEventListener("focus",tick)};
  },[view]);
  const data=loadedKey===statsUrl?stored:null, summary=data?.summary;
  const baselineReady=data?.meta.baseline_ready ?? (task?.active_revision!=null);
  const directoryBased=data?.meta.data_basis==="organization_catalog";
  const viewLabels={...labels,extras:baselineReady?"清单外材料":"已上传待核对"};
  const setView=(next:ReportingView,fields:Record<string,string>={})=>update({view:next,status:"",department_id:"",subject_id:"",keyword:"",...fields});
  const changeRegion=(id:string)=>update({jurisdiction_id:id,task_id:available.find(r=>r.id===id)?.task?.id||"",department_id:"",subject_id:"",status:"",keyword:""});
  async function createTask(){
    if(!region)return;setBusy(true);setError("");
    try{await reportingRequest("/api/reporting-tasks",{method:"POST",body:JSON.stringify({jurisdiction_org_id:region.id,reporting_year:year})});setRefresh(n=>n+1)}catch(e){setError(e instanceof Error?e.message:"创建失败")}finally{setBusy(false)}
  }
  const detail=(row:ReportingRow)=>{
    if(!row.slot_id)return null;
    const p=new URLSearchParams(query);p.set("returnTo",pathname+"?"+query);
    if(row.issue_id)p.set("issue_id",row.issue_id);
    if(row.document_version_id)p.set("document_version_id",String(row.document_version_id));
    if(view==="leads")p.set("tab","findings");
    return `${supervisor||view==="issues"?"/supervision/materials":"/materials/slots"}/${encodeURIComponent(row.slot_id)}?${p}`;
  };
  const tabs:ReportingView[]=["overview","departments","subjects","issues","extras",...(supervisor?["remediation" as const]:["roster" as const])];
  return <div className="mx-auto max-w-[1600px] p-4 sm:p-6" data-testid="gbc-reporting-task-page">
    <header className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold text-slate-900">{supervisor?"区域报送驾驶舱":"区域报送台账"}</h1><p className="mt-1 text-sm text-slate-500">按年度任务、地区、部门与单位核对报送和当前版本问题。</p></div><div className="flex flex-wrap gap-2"><button className={input} onClick={()=>setRefresh(n=>n+1)}>刷新</button><Link className={input} href={reportingLink(supervisor?"/materials":"/supervision",new URLSearchParams(query),{view:"overview"}) as Route}>{supervisor?"打开工作人员台账":"主管驾驶舱"}</Link><Link className={input} href={`${pathname}?legacy=1` as Route}>历史材料查询</Link></div></header>
    <div className={`${panel} mb-4 flex flex-wrap items-end gap-3`}>
      <label className="grid gap-1 text-xs text-slate-500">年度报送任务<select name="reporting_year" aria-label="年度报送任务" className={input} value={year} onChange={e=>update({reporting_year:e.target.value,jurisdiction_id:"",task_id:"",department_id:"",subject_id:"",keyword:"",status:""})}>{Array.from({length:new Date().getFullYear()-1999},(_,i)=>new Date().getFullYear()+1-i).map(y=><option key={y} value={y}>{y}年度报送任务</option>)}</select></label>
      <label className="grid gap-1 text-xs text-slate-500">地区<select name="jurisdiction_id" aria-label="报送地区" className={`${input} min-w-[170px]`} value={region?.id||""} onChange={e=>changeRegion(e.target.value)}>{available.map(r=><option key={r.id} value={r.id}>{r.name}{r.partial_scope?"（授权范围内）":""}</option>)}</select></label>
      <label className="grid gap-1 text-xs text-slate-500">材料范围<select name="component" aria-label="任务材料范围" className={input} value={component} onChange={e=>update({component:e.target.value})}><option value="all">全部任务材料</option>{(task?.components||[{report_kind:"budget",fiscal_year:year},{report_kind:"final",fiscal_year:year-1}]).map(p=><option key={p.report_kind} value={p.report_kind}>{p.fiscal_year}年{p.report_kind==="budget"?"预算":"决算"}</option>)}</select></label>
      <p className="text-xs text-slate-500">上海市本级与各区分别统计，财政年度保留材料真实年份。</p>
    </div>
    {error?<p role="alert" className="mb-4 rounded border border-danger-200 bg-danger-50 p-4 text-danger-700">{error}</p>:null}
    {!available.length&&!loading&&!error?<p className={panel}>当前账号没有可查看的地区，请联系管理员配置组织授权。</p>:null}
    {region&&!task&&!loading?<div className={panel}><h2 className="font-semibold">{region.name} · {year}年度任务尚未建立</h2><p className="my-3 text-sm text-slate-500">地区入口始终保留。建立任务后，按本地区组织目录中的部门及下属单位计算应交、未交和完整率。</p>{canManage?<button className={input} disabled={busy} onClick={createTask}>创建本地区{year}年度任务</button>:<p className="text-sm text-slate-500">请联系管理员建立本地区年度任务。</p>}</div>:null}
    {task?<nav className="mb-4 flex flex-wrap gap-2" aria-label="区域报送页面">{tabs.map(t=><button key={t} aria-pressed={view===t} className={`${input} ${view===t?"border-primary-400 bg-primary-50 text-primary-700":""}`} onClick={()=>setView(t)}>{viewLabels[t]}</button>)}</nav>:null}
    {loading&&task?<p role="status" className="py-6 text-slate-500">正在读取本地区年度任务…</p>:null}
    {data&&summary?<>
      <p className={`mb-4 rounded border p-3 text-sm ${data.meta.baseline_ready?"border-border bg-white text-slate-600":"border-warning-200 bg-warning-50 text-warning-800"}`}>{directoryBased?`按本区域组织目录统计：${count(summary.department_count)}个部门＋${count(summary.unit_count)}个下属单位，每个文种各应交一份；未交＝应交－已上传。${component==="all"?"全年合计包含预算和决算两类材料。":""}`:data.meta.baseline_ready?`应交名单第${data.meta.roster_revision}版，由${data.meta.baseline_by}确认。${summary.expected===0?"本期无应交材料。":""}`:"应交名单未确认：应交、缺交和完整率保持未知；已上传、已分析、已复核及问题数量按当前有效文件展示。"}{data.meta.partial_scope?" 当前仅统计账号授权范围。":""}{data.task.state==="archived"?" 本任务已归档，仅供查询。":""}<span className="ml-2 text-xs">统计时间：{new Date(data.meta.generated_at).toLocaleString("zh-CN")}</span></p>
      {data.meta.catalog_changed||summary.missing_references?<p className="mb-4 rounded bg-warning-50 p-3 text-sm text-warning-800">{data.meta.catalog_changed?"组织目录与已确认名单存在变化，请管理员核对并发布修订。 ":""}{summary.missing_references?`${summary.missing_references}项应交引用待核查；保留原应交分母，没有静默删除。`:""}</p>:null}
      <div className="mb-4 flex items-center justify-between"><h2 className="font-semibold text-slate-900">区域总计</h2><span className="text-xs text-slate-500">下方关键词和状态筛选只改变明细，不改变本区总计。</span></div>
      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="应交材料" value={summary.expected} context={baselineReady?`${component==="all"?"预算＋决算合计 · ":""}已交 ${count(summary.uploaded)} · 未交 ${count(summary.missing)}`:"名单未发布，应交范围待确认"} onClick={()=>setView("subjects")}/>
        <Metric label={baselineReady?"已交材料":"已上传材料"} value={summary.uploaded} context={`报送完整率 ${reportingRatio(summary.coverage_rate)}`} onClick={()=>setView("subjects",{status:"received"})}/>
        <Metric label="未交材料" value={summary.missing} context="尚未匹配到当前有效文件" onClick={()=>setView("subjects",{status:"missing"})}/>
        <Metric label="逾期未交" value={summary.overdue_missing} context={`未交且截止时间未知 ${count(summary.due_unknown_missing)}`} onClick={()=>setView("subjects",{status:"overdue"})}/>
        {baselineReady&&!directoryBased?<Metric label="完全未交部门" value={summary.empty_departments} onClick={()=>setView("departments",{status:"empty"})}/>:<Metric label="已分析材料" value={summary.analyzed} context="当前有效文件的分析结果" onClick={()=>setView("subjects",{status:"analyzed"})}/>}
        {baselineReady&&!directoryBased?<Metric label="部分未交部门" value={summary.partial_departments} context={`已交齐单位 ${count(summary.complete_units)}`} onClick={()=>setView("departments",{status:"partial"})}/>:<Metric label="已复核材料" value={summary.reviewed} context="当前文件版本已完成的人工复核" onClick={()=>setView("subjects",{status:"reviewed"})}/>}
        <Metric label="人工确认问题" value={summary.confirmed} context={`已分析 ${summary.analyzed} · 已复核 ${summary.reviewed} · 尚无法判断 ${summary.unknown_analysis}`} onClick={()=>setView("issues")}/>
        <Metric label="待复核线索" value={summary.pending} context={`另有补核待办 ${count(summary.pending_checks)} 项`} onClick={()=>setView("leads")}/>
      </div>
      <div className={`${panel} mb-4 grid gap-3 md:grid-cols-2`}>{data.components.map(p=><button key={p.report_kind} className="text-left text-sm" onClick={()=>update({component:p.report_kind})}><strong>{p.fiscal_year}年{p.report_kind==="budget"?"预算":"决算"}</strong><span className="ml-3 text-slate-500">应交{count(p.summary.expected)} · {baselineReady?"已交":"已上传"}{count(p.summary.uploaded)} · 未交{count(p.summary.missing)} · {reportingRatio(p.summary.coverage_rate)}</span></button>)}</div>
      <div className="mb-4 flex flex-wrap gap-4 text-xs text-slate-500"><span>本地区已匹配文件 {summary.observed_uploaded}</span><button className="text-primary-700" onClick={()=>setView("extras")}>{viewLabels.extras} {baselineReady?summary.extra_uploaded:summary.observed_uploaded}</button><span>任务财政年度内授权可见的归属待核查材料 {summary.unassigned_materials}；未归入本区的文件不参与本区已交与问题统计。</span>{!supervisor?<button className="text-primary-700" onClick={()=>setIdentityOpen(v=>!v)}>核查材料归属</button>:null}</div>
      {identityOpen&&task&&!supervisor?<ReportingIdentityPanel taskId={task.id} canManage={canManage} onChanged={()=>setRefresh(n=>n+1)}/>:null}
      {view==="roster"&&task?<ReportingTaskRoster task={task} canManage={canManage&&!supervisor} onChanged={()=>setRefresh(n=>n+1)}/>:<section className={panel}>
        <div className="mb-3 flex flex-wrap items-center gap-2"><h2 className="mr-auto font-semibold">{viewLabels[view]} · 命中{data.list.total}条</h2><a className={input} href={`${statsUrl}&format=csv`}>导出当前明细</a></div>
        <form className="mb-4 flex flex-wrap gap-2" onSubmit={e=>{e.preventDefault();update({keyword})}}><input name="keyword" aria-label="搜索报送主体" className={`${input} min-w-[200px]`} placeholder="搜索部门或单位" value={keyword} onChange={e=>setKeyword(e.target.value)}/><button className={input}>搜索</button><select name="status" aria-label="报送状态筛选" className={input} value={search.get("status")||"all"} onChange={e=>update({status:e.target.value})}>{[["all","全部报送状态"],["empty","完全未交部门"],["partial","部分未交部门"],["complete","已交齐部门"],["received","已交材料"],["unit_complete","已交齐单位"],["missing","未交材料"],["overdue","逾期未交"],["issues","有确认问题"],["waiting","待完成复核"],["analyzed","已分析材料"],["reviewed","已复核材料"]].map(([v,l])=><option key={v} value={v} disabled={!baselineReady&&["empty","partial","complete","unit_complete","missing","overdue"].includes(v)}>{v==="received"&&!baselineReady?"已上传材料":l}</option>)}</select><select name="department_id" aria-label="主管部门筛选" className={`${input} max-w-[300px]`} value={search.get("department_id")||""} onChange={e=>update({department_id:e.target.value,subject_id:""})}><option value="">全部主管部门</option>{data.departments.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></form>
        <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left text-sm"><thead className="bg-surface-100 text-xs text-slate-500"><tr>{view==="overview"||view==="departments"?(baselineReady?["主管部门","应交","已交","未交","逾期","确认问题","待复核线索","操作"]:["主管部门","已上传","已分析","已复核","确认问题","待复核线索","操作"]).map(t=><th className="p-3" key={t}>{t}</th>):(view==="issues"||view==="leads")?["主体 / 部门","财政年度 / 文种",view==="leads"?"待复核线索":"人工确认问题","严重度","操作"].map(t=><th className="p-3" key={t}>{t}</th>):["主体 / 部门","财政年度 / 文种","报送","确认问题","待复核线索","分析 / 复核","截止时间","操作"].map(t=><th className="p-3" key={t}>{t}</th>)}</tr></thead><tbody>{data.list.items.map((r,i)=>{
          const link=detail(r);
          if(view==="overview"||view==="departments")return <tr key={r.id} className="border-b border-border"><td className="p-3">{r.name}</td>{(baselineReady?[r.expected,typeof r.uploaded==="number"?r.uploaded:null,r.missing,r.overdue_missing,r.confirmed,r.pending]:[typeof r.uploaded==="number"?r.uploaded:null,r.analyzed,r.reviewed,r.confirmed,r.pending]).map((v,j)=><td className="p-3 tabular-nums" key={j}>{count(v)}</td>)}<td className="p-3"><button className="text-primary-700" onClick={()=>update({view:"subjects",department_id:r.id||""})}>查看部门与单位</button></td></tr>;
          return <tr key={`${r.slot_id||r.organization_id||i}:${r.issue_id||r.report_kind}`} className="border-b border-border"><td className="p-3"><span className="block text-xs text-slate-500">{r.department_name}</span><button className="text-primary-700" onClick={()=>update({view:"subjects",subject_id:r.organization_id||""})}>{r.organization_name}</button><span className="block text-xs text-slate-400">{({department_summary:"部门汇总",head_unit:"本级单位",subordinate_unit:"所属单位",relationship_unknown:"关系待核查"} as Record<string,string>)[r.relationship||""]}</span></td><td className="p-3 whitespace-nowrap">{r.fiscal_year}年{r.report_kind==="budget"?"预算":"决算"}</td>{(view==="issues"||view==="leads")?<><td className="p-3">{r.title}</td><td className="p-3">{({critical:"严重",high:"高",medium:"中",low:"低",manual_review:"人工补核"} as Record<string,string>)[r.severity||""]||"待确认"}</td></>:<><td className="p-3 whitespace-nowrap">{r.uploaded?(baselineReady?"已交":"已上传"):r.overdue?"逾期未交":"未交"}</td><td className="p-3">{count(r.confirmed_count)}</td><td className="p-3">{count(r.pending_count)}</td><td className="p-3">{r.status==="failed"?"分析失败":({completed:"复核完成",in_progress:"复核中",pending:"待复核",waiting_analysis:r.uploaded?"待分析/处理中":"尚无有效文件"} as Record<string,string>)[r.review_status||""]||"待确认"}{view==="remediation"?<span className="block text-xs">整改登记{r.rounds_count||0}轮</span>:null}</td><td className="p-3 text-xs">{r.due_at?new Date(r.due_at).toLocaleString("zh-CN"):"截止时间未知"}</td></>}<td className="p-3">{link?<Link href={link as Route} className="text-primary-700">{(view==="issues"||view==="leads")?"查看问题与原文":"查看材料"}</Link>:"尚无材料"}</td></tr>;
        })}</tbody></table></div>
        {!data.list.items.length?<p className="py-8 text-center text-sm text-slate-500">{data.meta.baseline_ready?"当前筛选没有明细。":"当前筛选没有已上传材料；应交范围仍待名单发布确认。"}</p>:null}
        <div className="mt-4 flex items-center justify-between text-xs text-slate-500"><span>第{data.list.total_pages?data.list.page:0}/{data.list.total_pages}页 · {data.list.total}条</span><div className="flex gap-2"><button className={input} disabled={data.list.page<=1} onClick={()=>update({page:String(data.list.page-1)})}>上一页</button><button className={input} disabled={data.list.page>=data.list.total_pages} onClick={()=>update({page:String(data.list.page+1)})}>下一页</button></div></div>
      </section>}
    </>:null}
  </div>;
}
