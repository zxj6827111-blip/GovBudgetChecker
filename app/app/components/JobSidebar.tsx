import { useState } from 'react';

type JobSummary = {
    job_id: string;
    filename: string;
    status: "queued" | "processing" | "done" | "error" | "unknown";
    progress: number;
    ts: number;
    mode: string;
};

interface JobSidebarProps {
    jobs: JobSummary[];
    activeJobId: string | null;
    onSelectJob: (job: JobSummary) => void;
    onCreateTask: () => void;
    onDeleteJob?: (jobId: string) => void;
}

export default function JobSidebar({ jobs, activeJobId, onSelectJob, onCreateTask, onDeleteJob }: JobSidebarProps) {
    const [deletingJobId, setDeletingJobId] = useState<string | null>(null);

    const handleDelete = async (e: React.MouseEvent, jobId: string) => {
        e.stopPropagation();
        if (!confirm('确定要删除这个任务吗？此操作不可撤销。')) return;

        setDeletingJobId(jobId);
        try {
            const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
            if (res.ok) {
                onDeleteJob?.(jobId);
            } else {
                alert('删除失败');
            }
        } catch (e) {
            alert('删除失败');
        } finally {
            setDeletingJobId(null);
        }
    };

    return (
        <div className="w-72 bg-gray-50/50 flex flex-col h-full border-r border-gray-200">
            <div className="p-4 border-b border-gray-200/60 bg-white">
                <button
                    onClick={onCreateTask}
                    className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2.5 px-4 rounded-xl transition-all shadow-sm shadow-indigo-200 flex items-center justify-center gap-2"
                >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" /></svg>
                    新建审查任务
                </button>
            </div>

            <div className="flex-1 overflow-y-auto overflow-x-hidden">
                {jobs.length === 0 ? (
                    <div className="p-8 text-center text-gray-400 text-sm">
                        <svg className="w-12 h-12 mx-auto mb-3 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        暂无任务记录
                    </div>
                ) : (
                    <ul className="flex flex-col p-2 gap-1">
                        {jobs.map((job) => {
                            const isActive = job.job_id === activeJobId;
                            const isDeleting = deletingJobId === job.job_id;
                            return (
                                <li key={job.job_id} className="relative group">
                                    <button
                                        onClick={() => onSelectJob(job)}
                                        disabled={isDeleting}
                                        className={`w-full text-left p-3 rounded-xl transition-all border ${isActive 
                                            ? 'bg-white border-indigo-200 shadow-sm ring-1 ring-indigo-500/10' 
                                            : 'border-transparent hover:bg-white hover:border-gray-200 hover:shadow-sm'
                                            } ${isDeleting ? 'opacity-50' : ''}`}
                                    >
                                        <div className="flex justify-between items-start mb-1.5 gap-2">
                                            <div className={`font-semibold text-[13px] line-clamp-2 leading-snug ${isActive ? 'text-indigo-900' : 'text-gray-700 group-hover:text-gray-900'}`} title={job.filename}>
                                                {job.filename || "未命名文档"}
                                            </div>
                                        </div>
                                        <div className="flex items-center justify-between mt-2">
                                            <StatusBadge status={job.status} />
                                            <div className="text-[10px] text-gray-400 font-mono tracking-tight flex items-center gap-1.5">
                                                <span>{new Date(job.ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
                                            </div>
                                        </div>
                                    </button>
                                    
                                    {/* Delete Button - Appears on hover */}
                                    <button
                                        onClick={(e) => handleDelete(e, job.job_id)}
                                        disabled={isDeleting}
                                        className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded-md hover:bg-red-50 text-red-400 hover:text-red-600 focus:opacity-100"
                                        title="删除任务"
                                    >
                                        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </div>
        </div>
    );
}

function StatusBadge({ status }: { status: string }) {
    const styles = {
        queued: "bg-slate-100 text-slate-600 border-slate-200",
        processing: "bg-indigo-50 text-indigo-600 border-indigo-100 animate-pulse",
        done: "bg-emerald-50 text-emerald-600 border-emerald-100",
        error: "bg-red-50 text-red-600 border-red-100",
        unknown: "bg-gray-100 text-gray-500 border-gray-200"
    };

    const labels = {
        queued: "排队中",
        processing: "正在审查",
        done: "审查完毕",
        error: "分析失败",
        unknown: "状态未知"
    };

    const s = status as keyof typeof styles || "unknown";

    return (
        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold tracking-widest ${styles[s]}`}>
            {labels[s]}
        </span>
    );
}
