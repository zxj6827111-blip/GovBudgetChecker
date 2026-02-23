import React, { useState, useMemo } from 'react';

// Consistent with backend Finding structure
interface QCFinding {
    rule_key: string;
    status: 'pass' | 'fail' | 'warn' | 'skip';
    lhs_value: string;
    rhs_value: string;
    diff: number;
    message: string;
    severity: string; // 'high' | 'medium' | 'low'
    skip_reason?: string;
    evidence_cells?: any[];
}

interface QCResultViewProps {
    findings: QCFinding[];
    isLoading?: boolean;
}

export default function QCResultView({ findings, isLoading }: QCResultViewProps) {
    const [filter, setFilter] = useState<'all' | 'fail' | 'warn' | 'pass' | 'skip'>('all');

    const stats = useMemo(() => {
        return {
            total: findings.length,
            fail: findings.filter(f => f.status === 'fail').length,
            warn: findings.filter(f => f.status === 'warn').length,
            pass: findings.filter(f => f.status === 'pass').length,
            skip: findings.filter(f => f.status === 'skip').length,
        };
    }, [findings]);

    const filteredFindings = useMemo(() => {
        if (filter === 'all') return findings;
        return findings.filter(f => f.status === filter);
    }, [findings, filter]);

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'pass': return 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50';
            case 'fail': return 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/50';
            case 'warn': return 'bg-yellow-100 text-yellow-700 border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-300 dark:border-yellow-800/50';
            case 'skip': return 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700/50';
            default: return 'bg-gray-100 text-gray-600';
        }
    };

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'pass':
                return <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>;
            case 'fail':
                return <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>;
            case 'warn':
                return <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>;
            case 'skip':
                return <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>;
            default: return null;
        }
    };

    if (isLoading) {
        return (
            <div className="p-8 text-center text-gray-500 animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-1/3 mx-auto mb-4"></div>
                <div className="h-32 bg-gray-100 rounded w-full"></div>
            </div>
        );
    }

    if (!findings || findings.length === 0) {
        return (
            <div className="p-12 text-center border-2 border-dashed border-gray-200 rounded-2xl">
                <p className="text-gray-500">暂无QC检查结果</p>
            </div>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in duration-500">

            {/* Stats Cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <button onClick={() => setFilter('all')} className={`p-3 rounded-xl border transition-all ${filter === 'all' ? 'bg-white shadow-md border-indigo-200 ring-2 ring-indigo-500/20' : 'bg-white/50 border-transparent hover:bg-white'} flex flex-col items-center justify-center`}>
                    <span className="text-2xl font-bold text-gray-800">{stats.total}</span>
                    <span className="text-xs text-gray-500 uppercase font-medium">全部规则</span>
                </button>
                <button onClick={() => setFilter('fail')} className={`p-3 rounded-xl border transition-all ${filter === 'fail' ? 'bg-red-50 shadow-md border-red-200 ring-2 ring-red-500/20' : 'bg-red-50/30 border-transparent hover:bg-red-50'} flex flex-col items-center justify-center`}>
                    <span className="text-2xl font-bold text-red-600">{stats.fail}</span>
                    <span className="text-xs text-red-600 uppercase font-medium">失败</span>
                </button>
                <button onClick={() => setFilter('warn')} className={`p-3 rounded-xl border transition-all ${filter === 'warn' ? 'bg-yellow-50 shadow-md border-yellow-200 ring-2 ring-yellow-500/20' : 'bg-yellow-50/30 border-transparent hover:bg-yellow-50'} flex flex-col items-center justify-center`}>
                    <span className="text-2xl font-bold text-yellow-600">{stats.warn}</span>
                    <span className="text-xs text-yellow-600 uppercase font-medium">警告</span>
                </button>
                <button onClick={() => setFilter('pass')} className={`p-3 rounded-xl border transition-all ${filter === 'pass' ? 'bg-green-50 shadow-md border-green-200 ring-2 ring-green-500/20' : 'bg-green-50/30 border-transparent hover:bg-green-50'} flex flex-col items-center justify-center`}>
                    <span className="text-2xl font-bold text-green-600">{stats.pass}</span>
                    <span className="text-xs text-green-600 uppercase font-medium">通过</span>
                </button>
                <button onClick={() => setFilter('skip')} className={`p-3 rounded-xl border transition-all ${filter === 'skip' ? 'bg-gray-50 shadow-md border-gray-200 ring-2 ring-gray-500/20' : 'bg-gray-50/30 border-transparent hover:bg-gray-50'} flex flex-col items-center justify-center`}>
                    <span className="text-2xl font-bold text-gray-500">{stats.skip}</span>
                    <span className="text-xs text-gray-500 uppercase font-medium">跳过</span>
                </button>
            </div>

            {/* Findings List */}
            <div className="space-y-3">
                {filteredFindings.map((finding) => (
                    <div
                        key={finding.rule_key}
                        className="group bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700/50 rounded-xl p-4 shadow-sm hover:shadow-md transition-all duration-200 relative overflow-hidden"
                    >
                        {/* Status Indicator Bar */}
                        <div className={`absolute left-0 top-0 bottom-0 w-1.5 ${finding.status === 'pass' ? 'bg-green-500' :
                                finding.status === 'fail' ? 'bg-red-500' :
                                    finding.status === 'warn' ? 'bg-yellow-500' : 'bg-gray-300'
                            }`}></div>

                        <div className="flex items-start justify-between pl-4">
                            <div className="flex-1">
                                <div className="flex items-center space-x-2 mb-1">
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wide ${getStatusColor(finding.status)}`}>
                                        {getStatusIcon(finding.status)}
                                        <span className="ml-1.5">{finding.status}</span>
                                    </span>
                                    <span className="text-xs font-mono text-gray-400 bg-gray-50 dark:bg-gray-700 px-1.5 py-0.5 rounded">{finding.rule_key}</span>
                                </div>

                                <h4 className="text-base font-medium text-gray-900 dark:text-gray-100 mt-2 mb-1">
                                    {finding.message}
                                </h4>

                                {finding.status !== 'pass' && finding.status !== 'skip' && (
                                    <div className="mt-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 text-sm grid grid-cols-2 gap-4 border border-gray-100 dark:border-gray-700/30">
                                        <div>
                                            <span className="block text-xs text-gray-400 mb-0.5">左值 (LHS)</span>
                                            <span className="font-mono font-medium text-gray-700 dark:text-gray-300">{finding.lhs_value || '-'}</span>
                                        </div>
                                        <div>
                                            <span className="block text-xs text-gray-400 mb-0.5">右值 (RHS)</span>
                                            <span className="font-mono font-medium text-gray-700 dark:text-gray-300">{finding.rhs_value || '-'}</span>
                                        </div>
                                        {Math.abs(finding.diff) > 0 && (
                                            <div className="col-span-2 border-t border-gray-100 dark:border-gray-700/30 pt-2 mt-1">
                                                <span className="text-xs text-gray-400">差异: </span>
                                                <span className="font-mono text-red-500 font-bold">{finding.diff}</span>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {finding.status === 'skip' && finding.skip_reason && (
                                    <p className="text-sm text-gray-500 italic mt-1">
                                        原因: {finding.skip_reason}
                                    </p>
                                )}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
