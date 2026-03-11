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
        { id: 'parse', label: '文档解析', status: stages.parse, detail: 'PDF解析与数据提取' },
        { id: 'materialize', label: '数据识别', status: stages.materialize, detail: '财政表格结构化' },
        { id: 'qc', label: '智能审查', status: stages.qc, detail: '15项核心规则校验' },
        { id: 'report', label: '报告生成', status: stages.report, detail: '生成HTML/PDF凭证' },
    ];

    const getStatusColor = (status: StageStatus, isCurrent: boolean) => {
        if (status === 'failed') return 'bg-red-500 text-white border-red-600 shadow-red-500/30';
        if (status === 'completed') return 'bg-green-500 text-white border-green-600 shadow-green-500/30';
        if (status === 'running' || isCurrent) return 'bg-blue-500 text-white border-blue-600 shadow-blue-500/30 animate-pulse';
        if (status === 'skipped') return 'bg-gray-300 text-gray-500 border-gray-400';
        return 'bg-white dark:bg-gray-800 text-gray-400 border-gray-200 dark:border-gray-700';
    };

    const getIcon = (status: StageStatus) => {
        switch (status) {
            case 'completed':
                return (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                );
            case 'failed':
                return (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                );
            case 'running':
                return (
                    <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                );
            default:
                return (
                    <span className="w-2.5 h-2.5 rounded-full bg-current opacity-50" />
                );
        }
    };

    return (
        <div className="w-full py-6">
            <div className="flex items-center justify-between relative">
                {/* Background Line */}
                <div className="absolute left-0 top-1/2 transform -translate-y-1/2 w-full h-1 bg-gray-100 dark:bg-gray-800 rounded-full -z-10" />

                {/* Active Progress Line */}
                {/* Simplified logic: if stage 2 is done, 50% width, etc. This is tricky with discrete states, 
            so we might skip the progress bar or make it smarter later. For now, static background is fine. */}

                {pipelineConfig.map((stage, index) => {
                    const isCurrent = currentStage === stage.id;
                    return (
                        <div key={stage.id} className="flex flex-col items-center group relative cursor-default">
                            <div
                                className={`w-10 h-10 rounded-full flex items-center justify-center border-2 transition-all duration-300 z-10 ${getStatusColor(stage.status, isCurrent)}`}
                            >
                                {getIcon(stage.status)}
                            </div>
                            <div className="mt-3 text-center">
                                <p className={`text-sm font-medium ${isCurrent || stage.status === 'completed' ? 'text-gray-900 dark:text-gray-100' : 'text-gray-500'}`}>
                                    {stage.label}
                                </p>
                                <p className="text-xs text-gray-400 hidden md:block max-w-[100px] mt-0.5">
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
