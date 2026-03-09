"use client";

import { useCallback, useEffect, useState } from "react";

interface Organization {
    id: string;
    name: string;
    level: string;
    level_name: string;
}

interface AssociateDialogProps {
    isOpen: boolean;
    jobId: string;
    filename: string;
    suggestions?: Array<{ organization: Organization; confidence: number }>;
    onClose: () => void;
    onAssociate: (orgId: string) => void;
}

interface CurrentMatch {
    organization: Organization;
    match_type: string;
    confidence: number;
}

export default function AssociateDialog({
    isOpen,
    jobId,
    filename,
    suggestions = [],
    onClose,
    onAssociate
}: AssociateDialogProps) {
    const [organizations, setOrganizations] = useState<Organization[]>([]);
    const [searchQuery, setSearchQuery] = useState("");
    const [loading, setLoading] = useState(false);
    const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
    const [suggestionsLoading, setSuggestionsLoading] = useState(false);
    const [dynamicSuggestions, setDynamicSuggestions] = useState<Array<{ organization: Organization; confidence: number }>>([]);
    const [currentMatch, setCurrentMatch] = useState<CurrentMatch | null>(null);

    const fetchOrganizations = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/organizations/list");
            const data = await res.json();
            setOrganizations(data.organizations || []);
        } catch (e) {
            console.error("Failed to fetch organizations", e);
        } finally {
            setLoading(false);
        }
    }, []);

    const fetchSuggestions = useCallback(async () => {
        if (!jobId) return;
        setSuggestionsLoading(true);
        try {
            const res = await fetch(`/api/jobs/${jobId}/org-suggestions?top_n=5`, { cache: "no-store" });
            const data = await res.json();
            const nextSuggestions = Array.isArray(data?.suggestions) ? data.suggestions : [];
            const nextCurrent = data?.current && typeof data.current === "object" ? data.current : null;
            setDynamicSuggestions(nextSuggestions);
            setCurrentMatch(nextCurrent);
            const preferredId =
                nextCurrent?.organization?.id ||
                nextSuggestions[0]?.organization?.id ||
                null;
            if (preferredId) {
                setSelectedOrgId(preferredId);
            }
        } catch (e) {
            console.error("Failed to fetch organization suggestions", e);
            setDynamicSuggestions([]);
            setCurrentMatch(null);
        } finally {
            setSuggestionsLoading(false);
        }
    }, [jobId]);

    useEffect(() => {
        if (isOpen) {
            setSearchQuery("");
            setSelectedOrgId(null);
            fetchOrganizations();
            fetchSuggestions();
        }
    }, [fetchOrganizations, fetchSuggestions, isOpen]);

    const effectiveSuggestions = dynamicSuggestions.length > 0 ? dynamicSuggestions : suggestions;

    const filteredOrgs = organizations.filter(org =>
        org.name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const handleConfirm = () => {
        if (selectedOrgId) {
            onAssociate(selectedOrgId);
        }
    };

    if (!isOpen) return null;

    const levelColors: Record<string, string> = {
        city: "bg-blue-100 text-blue-800",
        district: "bg-green-100 text-green-800",
        department: "bg-purple-100 text-purple-800",
        unit: "bg-gray-100 text-gray-800"
    };

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl w-[500px] max-h-[80vh] flex flex-col">
                {/* 标题 */}
                <div className="p-4 border-b">
                    <h3 className="text-lg font-semibold">关联到组织</h3>
                    <p className="text-sm text-gray-500 mt-1">
                        文件：<span className="font-medium">{filename}</span>
                    </p>
                    {currentMatch ? (
                        <div className="mt-2 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs text-indigo-700">
                            当前已关联：
                            <span className="ml-1 font-medium">{currentMatch.organization.name}</span>
                            <span className="ml-2 rounded bg-white/80 px-1.5 py-0.5">
                                {currentMatch.match_type === "manual" ? "人工绑定" : "自动匹配"}
                            </span>
                            <span className="ml-2 text-indigo-500">
                                置信度 {(Number(currentMatch.confidence || 0) * 100).toFixed(0)}%
                            </span>
                        </div>
                    ) : (
                        <p className="text-xs text-amber-600 mt-2">
                            ⚠️ 暂未匹配到组织，请手动选择该文档所属的部门/单位
                        </p>
                    )}
                </div>

                {/* 建议匹配 */}
                {(effectiveSuggestions.length > 0 || suggestionsLoading) && (
                    <div className="p-4 bg-blue-50 border-b">
                        <p className="text-sm font-medium text-blue-800 mb-2">可能的匹配：</p>
                        {suggestionsLoading ? (
                            <div className="text-sm text-blue-600">正在分析候选单位...</div>
                        ) : (
                            <div className="space-y-2">
                                {effectiveSuggestions.slice(0, 3).map(({ organization, confidence }) => (
                                    <button
                                        key={organization.id}
                                        onClick={() => setSelectedOrgId(organization.id)}
                                        className={`w-full text-left p-2 rounded border ${selectedOrgId === organization.id
                                                ? "border-indigo-500 bg-indigo-50"
                                                : "border-gray-200 bg-white hover:bg-gray-50"
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-medium">{organization.name}</span>
                                            <span className="text-xs text-gray-500">
                                                置信度: {(confidence * 100).toFixed(0)}%
                                            </span>
                                    </div>
                                    <span className={`text-xs px-1.5 py-0.5 rounded ${levelColors[organization.level]}`}>
                                        {organization.level_name || organization.level}
                                    </span>
                                </button>
                            ))}
                        </div>
                        )}
                    </div>
                )}

                {/* 搜索 */}
                <div className="p-4 border-b">
                    <input
                        type="text"
                        placeholder="搜索组织名称..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                    />
                </div>

                {/* 组织列表 */}
                <div className="flex-1 overflow-auto p-4">
                    {loading ? (
                        <div className="text-center py-8 text-gray-400">加载中...</div>
                    ) : filteredOrgs.length === 0 ? (
                        <div className="text-center py-8 text-gray-400">
                            {searchQuery ? "未找到匹配的组织" : "暂无组织数据，请先导入"}
                        </div>
                    ) : (
                        <div className="space-y-1">
                            {filteredOrgs.map(org => (
                                <button
                                    key={org.id}
                                    onClick={() => setSelectedOrgId(org.id)}
                                    className={`w-full text-left p-2 rounded flex items-center justify-between ${selectedOrgId === org.id
                                            ? "bg-indigo-100 border border-indigo-500"
                                            : "hover:bg-gray-50 border border-transparent"
                                        }`}
                                >
                                    <span className="text-sm">{org.name}</span>
                                    <span className={`text-xs px-1.5 py-0.5 rounded ${levelColors[org.level]}`}>
                                        {org.level_name || org.level}
                                    </span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* 按钮 */}
                <div className="p-4 border-t flex justify-end space-x-2">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
                    >
                        稍后关联
                    </button>
                    <button
                        onClick={handleConfirm}
                        disabled={!selectedOrgId}
                        className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        确认关联
                    </button>
                </div>
            </div>
        </div>
    );
}
