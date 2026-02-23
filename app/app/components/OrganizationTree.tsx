"use client";

import { useState, useEffect } from "react";

interface Organization {
    id: string;
    name: string;
    level: string;
    level_name: string;
    parent_id: string | null;
    children: Organization[];
    job_count: number;
    issue_count: number;
}

interface OrganizationTreeProps {
    onSelect: (org: Organization | null) => void;
    selectedOrgId?: string | null;
    refreshKey?: number;
}

export default function OrganizationTree({ onSelect, selectedOrgId, refreshKey }: OrganizationTreeProps) {
    const [tree, setTree] = useState<Organization[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [showImporter, setShowImporter] = useState(false);

    const fetchTree = async () => {
        setLoading(true);
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000);
        try {
            const res = await fetch(`/api/organizations?t=${Date.now()}`, {
                signal: controller.signal
            });
            if (!res.ok) throw new Error("API response not ok");

            const data = await res.json();

            // Validate data structure
            const treeData = data.tree ? data.tree : (Array.isArray(data) ? data : []);
            setTree(treeData);
            setError(null);

            if (expanded.size === 0) {
                const rootIds = new Set<string>();
                treeData.forEach((node: Organization) => {
                    rootIds.add(node.id);
                });
                setExpanded(rootIds);
            }
        } catch (e) {
            console.error(e);
            setError("加载组织架构失败");
            setTree([]);
        } finally {
            clearTimeout(timeoutId);
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchTree();
    }, [refreshKey]);

    const toggleExpand = (id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    const handleImport = async (file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("clear_existing", "true");

        try {
            const res = await fetch("/api/organizations/import", {
                method: "POST",
                body: formData
            });
            const result = await res.json();
            if (result.success) {
                alert(`导入成功！共导入 ${result.imported} 个组织`);
                fetchTree();
                setShowImporter(false);
            } else {
                const limit = result.limit || 0;
                const msg = result.detail || result.error || result.message || result.errors?.join(", ") || "未知错误";
                alert(`导入失败: ${msg}`);
            }
        } catch (e) {
            alert(`导入失败: ${e}`);
        }
    };

    const renderNode = (node: Organization, depth: number = 0) => {
        const hasChildren = node.children && node.children.length > 0;
        const isExpanded = expanded.has(node.id);
        const isSelected = selectedOrgId === node.id;

        const levelColors: Record<string, string> = {
            city: "text-blue-600",
            district: "text-green-600",
            department: "text-purple-600",
            unit: "text-gray-600"
        };

        return (
            <div key={node.id}>
                <div
                    className={`flex items-center py-2 px-3 m-1 rounded-md cursor-pointer transition-all duration-200 group ${isSelected
                        ? "bg-indigo-600 shadow-md shadow-indigo-500/20 text-white font-medium"
                        : "hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300"
                        }`}
                    style={{ paddingLeft: `${depth * 16 + 12}px` }}
                    onClick={() => onSelect(node)}
                    title={node.name}
                >
                    {/* 展开/折叠按钮 */}
                    <span
                        className={`mr-2 transition-transform duration-200 ${isExpanded ? "rotate-90" : ""} ${hasChildren ? "opacity-100" : "opacity-0"}`}
                        onClick={(e) => hasChildren && toggleExpand(node.id, e)}
                    >
                        <svg className={`w-3 h-3 ${isSelected ? "text-white/80" : "text-gray-400"}`} fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" /></svg>
                    </span>

                    {/* 节点名称 */}
                    <span className="truncate flex-1 text-sm tracking-tight">{node.name}</span>

                    {/* 层级标签 (仅在未选中时显示，保持选中状态清洁) */}
                    {!isSelected && (
                        <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded border ${node.level === 'city' ? 'bg-red-50 text-red-600 border-red-100 dark:bg-red-900/20 dark:border-red-800' :
                            node.level === 'district' ? 'bg-orange-50 text-orange-600 border-orange-100 dark:bg-orange-900/20 dark:border-orange-800' :
                                node.level === 'department' ? 'bg-blue-50 text-blue-600 border-blue-100 dark:bg-blue-900/20 dark:border-blue-800' :
                                    'bg-gray-50 text-gray-500 border-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700'
                            }`}>
                            {node.level_name}
                        </span>
                    )}

                    {/* 统计 */}
                    {node.job_count > 0 && (
                        <span className="text-xs text-gray-400 ml-2">
                            {node.job_count}任务
                        </span>
                    )}
                </div>

                {/* 子节点 */}
                {hasChildren && isExpanded && (
                    <div>
                        {node.children.map(child => renderNode(child, depth + 1))}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="h-full flex flex-col">
            {/* 标题栏 */}
            <div className="p-3 border-b border-gray-200 flex items-center justify-between">
                <h3 className="font-semibold text-gray-700">组织架构</h3>
                <div className="flex space-x-2">
                    <button
                        onClick={() => onSelect(null)}
                        className="text-xs text-gray-500 hover:text-gray-700"
                    >
                        全部
                    </button>
                    <button
                        onClick={() => setShowImporter(true)}
                        className="text-xs text-indigo-600 hover:text-indigo-800"
                    >
                        导入
                    </button>
                </div>
            </div>

            {/* 组织树 */}
            <div className="flex-1 overflow-auto p-2">
                {loading ? (
                    <div className="text-center py-8 text-gray-400">
                        <div className="w-6 h-6 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin mx-auto mb-2"></div>
                        加载中...
                    </div>
                ) : error ? (
                    <div className="text-center py-8 text-red-500">{error}</div>
                ) : tree.length === 0 ? (
                    <div className="text-center py-8 text-gray-400">
                        <p className="mb-2">暂无组织数据</p>
                        <button
                            onClick={() => setShowImporter(true)}
                            className="text-indigo-600 underline"
                        >
                            点击导入
                        </button>
                    </div>
                ) : (
                    tree.map(node => renderNode(node, 0))
                )}
            </div>

            {/* 导入弹窗 */}
            {showImporter && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-lg p-6 w-96 shadow-xl">
                        <h3 className="text-lg font-semibold mb-4">导入组织架构</h3>
                        <p className="text-sm text-gray-500 mb-4">
                            支持 Excel (.xlsx) 或 CSV 文件。表格需包含以下列：
                            <br />
                            <code className="text-xs bg-gray-100 px-1">名称/name</code>、
                            <code className="text-xs bg-gray-100 px-1">层级/level</code>、
                            <code className="text-xs bg-gray-100 px-1">上级/parent</code>
                        </p>
                        <input
                            type="file"
                            accept=".xlsx,.csv"
                            onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) handleImport(file);
                            }}
                            className="w-full border rounded p-2 mb-4"
                        />
                        <div className="flex justify-end space-x-2">
                            <button
                                onClick={() => setShowImporter(false)}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
                            >
                                取消
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
