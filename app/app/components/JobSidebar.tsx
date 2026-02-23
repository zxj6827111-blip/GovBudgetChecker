// import { format } from 'date-fns'; // You might need to install date-fns or use native Intl
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
        <div className="w-64 bg-white dark:bg-gray-800 flex flex-col h-full border-r border-gray-100 dark:border-gray-700/50">
            <div className="p-4">
                <button
                    onClick={onCreateTask}
                    className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-md transition-colors flex items-center justify-center gap-2"
                >
                    <span>+</span> 新建任务
                </button>
            </div>

            <div className="flex-1 overflow-y-auto">
                {jobs.length === 0 ? (
                    <div className="p-4 text-center text-gray-500 text-sm">
                        暂无任务记录
                    </div>
                ) : (
                    <ul className="divide-y divide-gray-100 dark:divide-gray-700">
                        {jobs.map((job) => {
                            const isActive = job.job_id === activeJobId;
                            const isDeleting = deletingJobId === job.job_id;
                            return (
                                <li key={job.job_id} className="relative group">
                                    <button
                                        onClick={() => onSelectJob(job)}
                                        disabled={isDeleting}
                                        className={`w-full text-left p-3 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${isActive ? 'bg-blue-50 dark:bg-blue-900/20 border-l-4 border-blue-500' : 'border-l-4 border-transparent'
                                            } ${isDeleting ? 'opacity-50' : ''}`}
                                    >
                                        <div className="flex justify-between items-start mb-1">
                                            <div className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate pr-2" title={job.filename}>
                                                {job.filename || "未命名文档"}
                                            </div>
                                            <StatusBadge status={job.status} />
                                        </div>
                                        <div className="flex justify-between items-center text-xs text-gray-500 dark:text-gray-400">
                                            <span>{new Date(job.ts * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                                            <span>#{job.job_id.slice(0, 6)}</span>
                                        </div>
                                    </button>
                                    {/* Delete Button */}
                                    <button
                                        onClick={(e) => handleDelete(e, job.job_id)}
                                        disabled={isDeleting}
                                        className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-500"
                                        title="删除任务"
                                    >
                                        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
        queued: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
        processing: "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300",
        done: "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300",
        error: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300",
        unknown: "bg-gray-100 text-gray-800"
    };

    const labels = {
        queued: "排队",
        processing: "分析中",
        done: "完成",
        error: "失败",
        unknown: "未知"
    };

    const s = status as keyof typeof styles || "unknown";

    return (
        <span className={`px-2 py-0.5 rounded text-[10px] font-medium whitespace-nowrap ${styles[s]}`}>
            {labels[s]}
        </span>
    );
}
