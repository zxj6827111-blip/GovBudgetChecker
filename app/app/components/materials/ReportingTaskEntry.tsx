"use client";
import {useEffect, useState, type ReactNode} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {ReportingTaskPage} from "./ReportingTaskPage";
import type {ReportingView} from "./reportingTaskAdapters";

export function ReportingTaskEntry({legacy, supervisor = false, view = "overview"}: {legacy:ReactNode; supervisor?:boolean; view?:ReportingView}) {
  const search = useSearchParams();
  const historical = search.get("legacy") === "1";
  const [capability,setCapability] = useState<{enabled:boolean; can_manage:boolean} | null>(null);
  const [error,setError] = useState("");
  useEffect(()=>{
    const controller = new AbortController();
    fetch("/api/reporting-tasks/capabilities",{cache:"no-store",signal:controller.signal}).then(async response=>{
      if (response.status === 404) return {enabled:false,can_manage:false};
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "年度报送服务暂不可用");
      return body;
    }).then(value=>{if(!controller.signal.aborted)setCapability(value)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});
    return ()=>controller.abort();
  },[]);
  if (historical || capability?.enabled === false) return <>{!historical ? <p role="status" className="mx-6 mt-4 rounded border border-warning-200 bg-warning-50 p-3 text-sm text-warning-800">年度报送功能未启用，当前为历史材料查询；应交与缺交仍需确认名册。后端更新后重新打开即可进入年度任务。</p> : <div className="px-6 pt-4"><Link href={supervisor?"/supervision":"/materials"} className="text-sm text-primary-700">返回区域年度报送 →</Link></div>}{legacy}</>;
  if (error) return <div role="alert" className="p-6 text-danger-700">{error}。<a href="?legacy=1" className="ml-3 underline">打开历史材料查询</a></div>;
  if (!capability) return <p className="p-6 text-slate-500">正在读取年度报送功能…</p>;
  return <ReportingTaskPage supervisor={supervisor} initialView={view} canManage={capability.can_manage}/>;
}
