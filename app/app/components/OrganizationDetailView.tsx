"use client";

import { useState, useEffect } from "react";
import { format } from "date-fns";
import { zhCN } from "date-fns/locale";

interface JobSummary {
    job_id: string;
    filename: string;
    status: "queued" | "processing" | "done" | "error" | "unknown";
    progress: number;
    ts: number;
    stage?: string; // Current processing stage
    issues?: number; // Placeholder for future issue count
}

interface OrganizationDetailViewProps {
    orgId: string;
    orgName: string;
    onSelectJob: (jobId: string) => void;
    onUpload: () => void;
    refreshKey?: number;
    onJobDeleted?: () => void;
}

export default function OrganizationDetailView({ orgId, orgName, onSelectJob, onUpload, refreshKey, onJobDeleted }: OrganizationDetailViewProps) {
    const [jobs, setJobs] = useState<JobSummary[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchJobs = async () => {
            setLoading(true);
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000);
            try {
                // Add timestamp to prevent caching
                const res = await fetch(`/api/organizations/${orgId}/jobs?t=${Date.now()}`, {
                    signal: controller.signal
                });
                if (res.ok) {
                    const data = await res.json();
                    setJobs(data.jobs || []);
                }
            } catch (e) {
                console.error("Failed to fetch jobs", e);
            } finally {
                clearTimeout(timeoutId);
                setLoading(false);
            }
        };

        if (orgId) {
            fetchJobs();
        }
    }, [orgId, refreshKey]);

    // 定时轮询：如果有处理中的任务，每2秒刷新一次
    useEffect(() => {
        const hasProcessing = jobs.some(j => j.status === 'processing' || j.status === 'queued');
        if (!hasProcessing) return;

        const interval = setInterval(async () => {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000);
            try {
                const res = await fetch(`/api/organizations/${orgId}/jobs?t=${Date.now()}`, {
                    signal: controller.signal
                });
                if (res.ok) {
                    const data = await res.json();
                    setJobs(data.jobs || []);
                }
            } catch (e) {
                console.error("Auto-refresh failed", e);
            } finally {
                clearTimeout(timeoutId);
            }
        }, 2000); // 每2秒刷新

        return () => clearInterval(interval);
    }, [jobs, orgId]);

    const getStatusBadge = (job: JobSummary) => {
        const { status, stage } = job;

        // 根据stage显示更详细的状态
        if (status === "processing" && stage) {
            if (stage.includes("规则") || stage.includes("执行")) {
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300 border border-yellow-200 dark:border-yellow-800/50 backdrop-blur-sm animate-pulse">规则分析中</span>;
            } else if (stage.includes("AI") || stage.includes("双模式")) {
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300 border border-purple-200 dark:border-purple-800/50 backdrop-blur-sm animate-pulse">AI分析中</span>;
            }
        }

        switch (status) {
            case "done":
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300 border border-green-200 dark:border-green-800/50 backdrop-blur-sm">已完成</span>;
            case "processing":
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-200 dark:border-blue-800/50 backdrop-blur-sm animate-pulse">分析中</span>;
            case "queued":
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700/60 dark:text-gray-300 border border-gray-200 dark:border-gray-600 backdrop-blur-sm">排队中</span>;
            case "error":
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300 border border-red-200 dark:border-red-800/50 backdrop-blur-sm">异常</span>;
            default:
                return <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">未知</span>;
        }
    };

    const handleDelete = async (jobId: string, e: React.MouseEvent) => {
        e.stopPropagation(); // Prevent row click
        if (!confirm('确定要删除这个任务吗？此操作不可恢复。')) return;

        try {
            const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
            if (res.ok) {
                setJobs(prev => prev.filter(j => j.job_id !== jobId));
                onJobDeleted?.();
            } else {
                alert('删除失败');
            }
        } catch (e) {
            console.error('Delete failed', e);
            alert('删除失败');
        }
    };

    return (
        <div className="flex flex-col h-full bg-transparent overflow-hidden">
            {/* Header / Sci-Fi Glass Card */}
            <div className="flex-none p-8 pb-4">
                <div className="relative rounded-2xl bg-white/70 dark:bg-slate-900/60 backdrop-blur-xl border border-white/20 dark:border-gray-700/50 shadow-xl overflow-hidden p-8 flex justify-between items-center group transition-all duration-300 hover:shadow-2xl">
                    {/* Decorative Sci-Fi Glow */}
                    <div className="absolute top-0 right-0 -mr-16 -mt-16 w-64 h-64 bg-indigo-500/20 rounded-full blur-3xl pointer-events-none group-hover:bg-indigo-500/30 transition-all duration-700"></div>

                    <div className="relative z-10">
                        <h1 className="text-4xl font-bold text-gray-900 dark:text-white mb-2 tracking-tight">
                            {orgName}
                        </h1>
                        <div className="flex items-center space-x-4 text-sm text-gray-500 dark:text-gray-400">
                            <span className="flex items-center">
                                <svg className="w-4 h-4 mr-1.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                                {jobs.length} 个文档
                            </span>
                            <span className="flex items-center">
                                <svg className="w-4 h-4 mr-1.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                                0 个待处理问题
                            </span>
                        </div>
                    </div>

                    <div className="relative z-10">
                        <button
                            onClick={onUpload}
                            className="group relative inline-flex items-center justify-center px-6 py-3 text-base font-medium text-white transition-all duration-200 bg-indigo-600 rounded-xl shadow-lg hover:bg-indigo-700 hover:shadow-indigo-500/30 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-600 dark:focus:ring-offset-gray-900 overflow-hidden"
                        >
                            <span className="absolute inset-0 w-full h-full -mt-1 rounded-lg opacity-30 bg-gradient-to-b from-transparent via-transparent to-black"></span>
                            <span className="relative flex items-center gap-2">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
                                上传新文档
                            </span>
                        </button>
                    </div>
                </div>
            </div>

            {/* Content List Area */}
            <div className="flex-1 overflow-auto px-8 pb-8">
                {loading ? (
                    <div className="flex items-center justify-center h-64 text-gray-400">
                        <div className="w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full animate-spin mr-3"></div>
                        加载中...
                    </div>
                ) : jobs.length === 0 ? (
                    <div className="text-center py-20 bg-white/40 dark:bg-slate-800/40 backdrop-blur-md rounded-2xl border border-gray-200/50 dark:border-gray-700/30 border-dashed">
                        <p className="text-gray-500 dark:text-gray-400 text-lg">暂无文档数据</p>
                        <p className="text-sm text-gray-400 mt-2">点击右上方按钮上传该组织的决算报告</p>
                    </div>
                ) : (
                    <div className="grid gap-4">
                        <div className="bg-white/40 dark:bg-slate-900/40 backdrop-blur-md rounded-2xl border border-white/20 dark:border-gray-700/30 shadow-sm overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-200/50 dark:divide-gray-700/50">
                                <thead className="bg-gray-50/50 dark:bg-slate-800/50">
                                    <tr>
                                        <th scope="col" className="px-6 py-4 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">文件名称</th>
                                        <th scope="col" className="px-6 py-4 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">上传时间</th>
                                        <th scope="col" className="px-6 py-4 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">状态</th>
                                        <th scope="col" className="px-6 py-4 text-right text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider w-32">操作</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-200/50 dark:divide-gray-700/50 bg-transparent">
                                    {jobs.map((job) => (
                                        <tr
                                            key={job.job_id}
                                            className="group hover:bg-white/60 dark:hover:bg-slate-800/60 transition-colors duration-150 cursor-pointer"
                                            onClick={() => onSelectJob(job.job_id)}
                                        >
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <div className="flex items-center">
                                                    <div className="flex-shrink-0 h-10 w-10 flex items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400">
                                                        <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                                                    </div>
                                                    <div className="ml-4">
                                                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors">
                                                            {job.filename}
                                                        </div>
                                                        <div className="text-xs text-gray-500 dark:text-gray-500 font-mono">
                                                            ID: {job.job_id.slice(0, 8)}
                                                        </div>
                                                    </div>
                                                </div>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <div className="text-sm text-gray-500 dark:text-gray-400 tabular-nums">
                                                    {format(new Date(job.ts * 1000), 'yyyy-MM-dd HH:mm', { locale: zhCN })}
                                                </div>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                {getStatusBadge(job)}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                                                <div className="flex items-center justify-end space-x-3">
                                                    <button
                                                        onClick={(e) => handleDelete(job.job_id, e)}
                                                        className="flex items-center text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 transition-colors px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 mr-2"
                                                        title="删除任务"
                                                    >
                                                        <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                                                        删除
                                                    </button>
                                                    <button
                                                        onClick={() => onSelectJob(job.job_id)}
                                                        className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-900 dark:hover:text-indigo-300 text-xs"
                                                    >
                                                        查看 →
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
