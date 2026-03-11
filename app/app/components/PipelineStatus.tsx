import React from 'react';

type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

interface PipelineStage {
    id: string;
    label: string;
    status: StageStatus;
    detail?: string;
}

interface PipelineStatusProps {
    stages: {
        parse: StageStatus;
        materialize: StageStatus;
        qc: StageStatus;
        report: StageStatus;
    };
    currentStage?: string;
}

export default function PipelineStatus({ stages, currentStage }: PipelineStatusProps) {
    const pipelineConfig: PipelineStage[] = [
        { id: 'parse', label: '文档解析', status: stages.parse, detail: 'PDF提取' },
        { id: 'materialize', label: '数据识别', status: stages.materialize, detail: '表格结构化' },
        { id: 'qc', label: '智能审查', status: stages.qc, detail: '核心规则校验' },
        { id: 'report', label: '报告生成', status: stages.report, detail: '审查结论' },
    ];

    const getStatusState = (status: StageStatus, isCurrent: boolean) => {
        if (status === 'failed') return { fg: 'text-red-600', bg: 'bg-red-100', dot: 'bg-red-500', rings: 'ring-red-200' };
        if (status === 'completed') return { fg: 'text-emerald-600', bg: 'bg-emerald-50', dot: 'bg-emerald-500', rings: 'ring-emerald-100' };
        if (status === 'running' || isCurrent) return { fg: 'text-indigo-600', bg: 'bg-indigo-50 animate-pulse', dot: 'bg-indigo-500', rings: 'ring-indigo-100 ring-4' };
        if (status === 'skipped') return { fg: 'text-slate-400', bg: 'bg-slate-50', dot: 'bg-slate-300', rings: 'ring-slate-100' };
        return { fg: 'text-gray-300', bg: 'bg-gray-50', dot: 'bg-gray-300', rings: '' };
    };

    const getIcon = (status: StageStatus) => {
        switch (status) {
            case 'completed': return <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />;
            case 'failed': return <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />;
            case 'running': return (
                <>
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </>
            );
            default: return null;
        }
    };

    return (
        <div className="w-full py-8">
            <div className="flex items-center justify-between relative max-w-2xl mx-auto px-4">
                {/* Clean minimal background connecting line */}
                <div className="absolute left-[10%] right-[10%] top-[24px] transform -translate-y-1/2 h-[2px] bg-gray-100 rounded-full -z-10" />

                {pipelineConfig.map((stage) => {
                    const isCurrent = currentStage === stage.id;
                    const style = getStatusState(stage.status, isCurrent);
                    const isActive = isCurrent || stage.status === 'completed' || stage.status === 'failed';

                    return (
                        <div key={stage.id} className="flex flex-col items-center group relative cursor-default w-24">
                            <div
                                className={`w-12 h-12 rounded-2xl flex items-center justify-center ${style.bg} ${style.fg} shadow-sm ring-1 ${style.rings || 'ring-gray-200'} transition-all duration-300 z-10 bg-white`}
                            >
                                {stage.status === 'pending' || stage.status === 'skipped' ? (
                                    <div className={`w-2 h-2 rounded-full ${style.dot}`} />
                                ) : (
                                    <svg className={`w-5 h-5 ${stage.status === 'running' ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        {getIcon(stage.status)}
                                    </svg>
                                )}
                            </div>
                            <div className="mt-4 text-center">
                                <p className={`text-[13px] font-bold tracking-tight ${isActive ? 'text-gray-900' : 'text-gray-400'}`}>
                                    {stage.label}
                                </p>
                                <p className={`text-[10px] mt-1 font-medium tracking-wide ${isActive ? 'text-gray-500' : 'text-gray-300'}`}>
                                    {stage.detail}
                                </p>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
