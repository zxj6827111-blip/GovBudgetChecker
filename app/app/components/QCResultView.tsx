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

    const getStatusColor = (status: string, border: boolean = false) => {
        switch (status) {
            case 'pass': return border ? 'border-emerald-200' : 'text-emerald-700 bg-emerald-50';
            case 'fail': return border ? 'border-red-200' : 'text-red-700 bg-red-50';
            case 'warn': return border ? 'border-amber-200' : 'text-amber-700 bg-amber-50';
            case 'skip': return border ? 'border-slate-200' : 'text-slate-600 bg-slate-50';
            default: return border ? 'border-gray-200' : 'text-gray-600 bg-gray-50';
        }
    };

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'pass':
                return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" /></svg>;
            case 'fail':
                return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" /></svg>;
            case 'warn':
                return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>;
            case 'skip':
                return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>;
            default: return null;
        }
    };

    if (isLoading) {
        return (
            <div className="p-8 text-center text-gray-400 animate-pulse">
                <div className="h-4 bg-gray-100 rounded w-1/3 mx-auto mb-4"></div>
                <div className="h-32 bg-gray-50 rounded w-full"></div>
            </div>
        );
    }

    if (!findings || findings.length === 0) {
        return (
            <div className="p-12 text-center border border-dashed border-gray-200 rounded-xl bg-gray-50/50">
                <p className="text-sm text-gray-500 font-medium">暂无 QC 检查结果</p>
                <p className="text-xs text-gray-400 mt-1">报告生成暂未包含审查数据</p>
            </div>
        );
    }

    const statCards = [
        { id: 'all', label: '全部规则', value: stats.total, colorClass: filter === 'all' ? 'border-indigo-400 bg-indigo-50/50 text-indigo-700 shadow-sm ring-1 ring-indigo-400' : 'border-gray-200/80 hover:border-gray-300 text-gray-500 bg-white' },
        { id: 'fail', label: '失败', value: stats.fail, colorClass: filter === 'fail' ? 'border-red-400 bg-red-50/50 text-red-700 shadow-sm ring-1 ring-red-400' : 'border-gray-200/80 hover:border-red-300 text-gray-500 bg-white' },
        { id: 'warn', label: '警告', value: stats.warn, colorClass: filter === 'warn' ? 'border-amber-400 bg-amber-50/50 text-amber-700 shadow-sm ring-1 ring-amber-400' : 'border-gray-200/80 hover:border-amber-300 text-gray-500 bg-white' },
        { id: 'pass', label: '通过', value: stats.pass, colorClass: filter === 'pass' ? 'border-emerald-400 bg-emerald-50/50 text-emerald-700 shadow-sm ring-1 ring-emerald-400' : 'border-gray-200/80 hover:border-emerald-300 text-gray-500 bg-white' },
        { id: 'skip', label: '跳过', value: stats.skip, colorClass: filter === 'skip' ? 'border-slate-400 bg-slate-50/50 text-slate-700 shadow-sm ring-1 ring-slate-400' : 'border-gray-200/80 hover:border-slate-300 text-gray-500 bg-white' },
    ];

    return (
        <div className="space-y-6 animate-in fade-in duration-500 pb-20">
            {/* Minimalist Stats Cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {statCards.map(sc => (
                    <button 
                        key={sc.id}
                        onClick={() => setFilter(sc.id as any)} 
                        className={`p-3 rounded-xl border transition-all flex flex-col items-center justify-center ${sc.colorClass}`}
                    >
                        <span className="text-xl font-bold font-mono tracking-tight">{sc.value}</span>
                        <span className="text-[11px] font-medium mt-0.5 opacity-80 uppercase tracking-widest">{sc.label}</span>
                    </button>
                ))}
            </div>

            {/* Structured Findings List */}
            <div className="space-y-3">
                {filteredFindings.map((finding) => (
                    <div
                        key={finding.rule_key}
                        className={`bg-white border rounded-xl overflow-hidden hover:shadow-md transition-all shadow-sm ${getStatusColor(finding.status, true)}`}
                    >
                        <div className="px-5 py-4 flex items-start gap-4">
                            <div className={`mt-1 inline-flex items-center justify-center p-2 rounded-lg ${getStatusColor(finding.status)}`}>
                                {getStatusIcon(finding.status)}
                            </div>
                            
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 mb-1.5">
                                    <span className="text-xs font-mono font-bold text-gray-600 tracking-tight">{finding.rule_key}</span>
                                    <span className="w-1 h-1 rounded-full bg-gray-300"></span>
                                    <span className={`text-[10px] uppercase font-bold tracking-widest px-1.5 py-0.5 rounded-sm ${getStatusColor(finding.status)}`}>
                                        {finding.status}
                                    </span>
                                </div>
                                <h4 className="text-sm font-semibold text-gray-900 leading-snug mb-3 pr-4">
                                    {finding.message}
                                </h4>

                                {finding.status !== 'pass' && finding.status !== 'skip' && (
                                    <div className="bg-gray-50/80 rounded-lg p-3.5 grid grid-cols-2 gap-x-6 gap-y-3 border border-gray-100/80">
                                        <div className="flex flex-col">
                                            <span className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold mb-1">系统填报值 (LHS)</span>
                                            <span className="font-mono text-[13px] font-medium text-gray-800 break-all">{finding.lhs_value || '-'}</span>
                                        </div>
                                        <div className="flex flex-col">
                                            <span className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold mb-1">比对计算值 (RHS)</span>
                                            <span className="font-mono text-[13px] font-medium text-gray-800 break-all">{finding.rhs_value || '-'}</span>
                                        </div>
                                        {Math.abs(finding.diff) > 0 && (
                                            <div className="col-span-2 flex items-center gap-2 mt-1 pt-3 border-t border-gray-200/60">
                                                <span className="text-[11px] font-semibold text-gray-500">检测差异: </span>
                                                <span className="font-mono text-xs text-red-600 font-bold bg-red-50 px-1.5 py-0.5 rounded border border-red-100">
                                                    {finding.diff}
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {finding.status === 'skip' && finding.skip_reason && (
                                    <div className="mt-2 text-[11px] text-slate-500 bg-slate-50 px-3 py-2.5 rounded-lg border border-slate-100 flex items-start gap-2 max-w-fit">
                                        <svg className="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                                        <span className="leading-snug">自动跳过原因: {finding.skip_reason}</span>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
