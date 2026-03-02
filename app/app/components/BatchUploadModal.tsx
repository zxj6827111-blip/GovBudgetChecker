"use client";

import { useState, useRef, useCallback, useEffect } from "react";

// ───────────── Types ─────────────

interface FileItem {
    id: string;
    file: File;
    filename: string;
    year: string;
    docType: "dept_final" | "dept_budget";
    orgId?: string;
    orgName?: string;
    orgLevel?: string;
    status: "pending" | "uploading" | "triggering" | "success" | "failed" | "skipped";
    message: string;
}

interface BatchUploadModalProps {
    orgUnitId?: string; // 已选单位（局部模式），如果未传则是全区模式
    defaultDocType: "dept_final" | "dept_budget";
    onClose: () => void;
    onComplete: () => void;
}

const MAX_FILES = 50;

// ───────────── Helpers ─────────────

function detectDocType(filename: string, fallback: "dept_final" | "dept_budget"): "dept_final" | "dept_budget" {
    const lower = filename.toLowerCase();
    if (filename.includes("决算") || lower.includes("final") || lower.includes("settlement") || lower.includes("accounts")) {
        return "dept_final";
    }
    if (filename.includes("预算") || lower.includes("budget")) {
        return "dept_budget";
    }
    return fallback;
}

function detectFiscalYear(filename: string): string {
    const match4 = filename.match(/(20\d{2})/);
    if (match4) return match4[1];
    const match2 = filename.match(/(?:^|[^\d])(\d{2})(?=\s*(?:年|年度|预算|决算))/);
    if (match2) {
        const year = Number(match2[1]);
        if (year >= 0 && year <= 99) return String(2000 + year);
    }
    return "";
}

// ───────────── Component ─────────────

export default function BatchUploadModal({
    orgUnitId,
    defaultDocType,
    onClose,
    onComplete,
}: BatchUploadModalProps) {
    const [files, setFiles] = useState<FileItem[]>([]);
    const [orgs, setOrgs] = useState<any[]>([]);
    const [isProcessing, setIsProcessing] = useState(false);
    const [currentIndex, setCurrentIndex] = useState(-1);
    const [isDragging, setIsDragging] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // 拉取全区组织用于匹配和下拉选择
    useEffect(() => {
        fetch("/api/organizations/list")
            .then(res => res.json())
            .then(data => {
                if (data.organizations) {
                    setOrgs(data.organizations);
                }
            })
            .catch(console.error);
    }, []);

    // ── File Selection ──

    const handleFilesSelect = useCallback(
        (fileList: FileList) => {
            const existingCount = files.length;
            const newFileArray = Array.from(fileList).filter(
                (f) => f.name.toLowerCase().endsWith(".pdf")
            );

            if (newFileArray.length === 0) return;

            if (existingCount + newFileArray.length > MAX_FILES) {
                alert(`最多支持${MAX_FILES}个文件，当前已有${existingCount}个`);
                return;
            }

            const newFiles: FileItem[] = newFileArray.map((file) => {
                let matchedOrgId = orgUnitId;
                let matchedOrgName = "";
                let matchedOrgLevel = "";

                // 如果是全局模式没有传 orgUnitId，尝试通过文件名自动匹配
                if (!orgUnitId && orgs.length > 0) {
                    // 从最长的名称开始匹配，以防止部分重叠（如：区民政局 vs 区民政局本级）
                    const sortedOrgs = [...orgs].sort((a, b) => b.name.length - a.name.length);
                    let match = sortedOrgs.find(o => file.name.includes(o.name));

                    if (match) {
                        // 针对部门和单位的智能推导
                        const isUnitIntended = file.name.includes("单位") || file.name.includes("本级");
                        const isDeptIntended = file.name.includes("部门");

                        if (match.level === "department" && isUnitIntended) {
                            // filename 有"单位"或"本级"，但匹配到的是部门，尝试找这个部门下的本级单位
                            const childUnit = orgs.find(o => o.parent_id === match?.id && (o.name.includes("本级") || o.name.includes("单位")));
                            if (childUnit) match = childUnit;
                        } else if (match.level === "unit" && isDeptIntended) {
                            // filename 带有"部门"，但匹配到了单位，尝试找它所在的部门
                            const parentDept = orgs.find(o => o.id === match?.parent_id);
                            if (parentDept) match = parentDept;
                        }

                        matchedOrgId = match.id;
                        matchedOrgName = match.name;
                        matchedOrgLevel = match.level;
                    }
                } else if (orgUnitId) {
                    const o = orgs.find(o => o.id === orgUnitId);
                    matchedOrgName = o?.name || "";
                    matchedOrgLevel = o?.level || "";
                }

                return {
                    id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
                    file,
                    filename: file.name,
                    year: detectFiscalYear(file.name),
                    docType: detectDocType(file.name, defaultDocType),
                    orgId: matchedOrgId,
                    orgName: matchedOrgName,
                    orgLevel: matchedOrgLevel,
                    status: "pending",
                    message: "",
                };
            });

            setFiles((prev) => [...prev, ...newFiles]);
        },
        [files.length, defaultDocType, orgUnitId, orgs]
    );

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };
    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
    };
    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        if (e.dataTransfer.files) handleFilesSelect(e.dataTransfer.files);
    };
    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) handleFilesSelect(e.target.files);
        e.target.value = "";
    };

    // ── File Management ──

    const removeFile = (id: string) => {
        setFiles((prev) => prev.filter((f) => f.id !== id));
    };

    const updateFile = (id: string, updates: Partial<FileItem>) => {
        setFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...updates } : f)));
    };

    // ── Upload Logic ──

    const uploadSingleFile = async (fileItem: FileItem): Promise<void> => {
        // Step 1: update status to uploading
        setFiles((prev) =>
            prev.map((f) =>
                f.id === fileItem.id ? { ...f, status: "uploading" as const, message: "上传中..." } : f
            )
        );

        // Step 1: upload file
        if (!fileItem.orgId) {
            throw new Error("请先选择该文件所属的组织/单位");
        }

        const formData = new FormData();
        formData.set("file", fileItem.file);
        formData.set("org_unit_id", fileItem.orgId);
        if (fileItem.year) {
            formData.append("fiscal_year", fileItem.year);
        }
        formData.append("doc_type", fileItem.docType);

        const uploadRes = await fetch("/api/documents/upload", {
            method: "POST",
            body: formData,
        });

        let versionId: string | undefined;

        if (!uploadRes.ok) {
            // Fallback to v2 API
            if (uploadRes.status !== 503) {
                throw new Error(await uploadRes.text() || `HTTP ${uploadRes.status}`);
            }

            const v2Form = new FormData();
            v2Form.set("file", fileItem.file);
            v2Form.set("org_id", fileItem.orgId);
            const v2Res = await fetch("/api/upload", { method: "POST", body: v2Form });
            if (!v2Res.ok) {
                throw new Error(await v2Res.text() || `HTTP ${v2Res.status}`);
            }

            let v2Data: any = {};
            try {
                v2Data = await v2Res.json();
            } catch {
                v2Data = {};
            }
            versionId = v2Data?.id || v2Data?.job_id;
        } else {
            let uploadData: any = {};
            try {
                uploadData = await uploadRes.json();
            } catch {
                uploadData = {};
            }
            versionId = uploadData?.id || uploadData?.job_id;
        }

        if (!versionId) {
            throw new Error("上传成功但未返回任务ID，无法启动检查");
        }

        // Step 2: Trigger local rules + AI check
        setFiles((prev) =>
            prev.map((f) =>
                f.id === fileItem.id
                    ? { ...f, status: "triggering" as const, message: "触发检查中..." }
                    : f
            )
        );

        const runPayload: Record<string, unknown> = {
            mode: "legacy",
            doc_type: fileItem.docType,
        };
        if (fileItem.year) {
            runPayload.fiscal_year = fileItem.year;
            runPayload.report_year = Number(fileItem.year);
        }

        const runRes = await fetch(`/api/documents/${versionId}/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(runPayload),
        });
        if (!runRes.ok) {
            throw new Error(await runRes.text() || `HTTP ${runRes.status}`);
        }
    };

    const startProcessing = async () => {
        const pendingFiles = files.filter((f) => f.status === "pending");
        if (pendingFiles.length === 0) {
            alert("没有待处理的文件");
            return;
        }

        setIsProcessing(true);

        for (let i = 0; i < files.length; i++) {
            const fileItem = files[i];
            if (fileItem.status !== "pending") continue;

            setCurrentIndex(i);

            try {
                await uploadSingleFile(fileItem);

                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === fileItem.id
                            ? { ...f, status: "success" as const, message: "✅ 上传成功，检查已启动" }
                            : f
                    )
                );
            } catch (error: any) {
                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === fileItem.id
                            ? {
                                ...f,
                                status: "failed" as const,
                                message: error?.message || "上传失败",
                            }
                            : f
                    )
                );
            }

            // Brief delay between uploads
            await new Promise((r) => setTimeout(r, 300));
        }

        setIsProcessing(false);
        setCurrentIndex(-1);
        onComplete();
    };

    // ── Stats ──

    const getProgress = () => {
        const completed = files.filter((f) =>
            ["success", "failed", "skipped"].includes(f.status)
        ).length;
        return {
            completed,
            total: files.length,
            percent: files.length ? Math.round((completed / files.length) * 100) : 0,
        };
    };

    const getStats = () => ({
        pending: files.filter((f) => f.status === "pending").length,
        success: files.filter((f) => f.status === "success").length,
        failed: files.filter((f) => f.status === "failed").length,
        skipped: files.filter((f) => f.status === "skipped").length,
    });

    const progress = getProgress();
    const stats = getStats();

    // ── Status Icon ──

    const getStatusIcon = (status: FileItem["status"]) => {
        switch (status) {
            case "pending":
                return (
                    <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                );
            case "uploading":
            case "triggering":
                return (
                    <svg className="w-5 h-5 text-indigo-500 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                );
            case "success":
                return (
                    <svg className="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                );
            case "failed":
                return (
                    <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                );
            case "skipped":
                return (
                    <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                    </svg>
                );
        }
    };

    // ── Render ──

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm">
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-3xl w-full max-h-[90vh] flex flex-col border border-white/20 overflow-hidden animate-in zoom-in-95 duration-300">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200/50 dark:border-gray-700/50 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-gray-800 dark:to-gray-800">
                    <div className="flex items-center space-x-3">
                        <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center text-white shadow-lg shadow-indigo-500/30">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                            </svg>
                        </div>
                        <h2 className="text-xl font-bold text-gray-900 dark:text-white">批量上传文档</h2>
                        {files.length > 0 && (
                            <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 rounded-full text-xs font-medium">
                                {files.length} 个文件
                            </span>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        disabled={isProcessing}
                        className="p-2 rounded-lg hover:bg-gray-200/50 dark:hover:bg-gray-700/50 text-gray-400 hover:text-gray-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Drop Zone */}
                <div className="px-6 pt-4">
                    <div
                        className={`border-2 border-dashed rounded-xl p-8 text-center transition-all duration-300 cursor-pointer ${isDragging
                            ? "border-indigo-500 bg-indigo-50/50 dark:bg-indigo-900/20 scale-[1.02]"
                            : "border-gray-300 dark:border-gray-600 hover:border-indigo-400 hover:bg-gray-50/50 dark:hover:bg-gray-700/30"
                            }`}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        onClick={() => !isProcessing && fileInputRef.current?.click()}
                    >
                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileInputChange}
                            accept=".pdf"
                            multiple
                            style={{ display: "none" }}
                            disabled={isProcessing}
                        />
                        <div className="flex flex-col items-center">
                            <div className={`mb-3 transition-transform duration-300 ${isDragging ? "-translate-y-1" : ""}`}>
                                <svg className="w-10 h-10 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                                </svg>
                            </div>
                            <p className="text-sm text-gray-700 dark:text-gray-300">
                                <strong>点击选择多个文件</strong> 或 <strong>拖拽文件至此</strong>
                            </p>
                            <p className="text-xs text-gray-500 mt-1">
                                仅支持 PDF 文件，最多 {MAX_FILES} 个 · 系统自动识别年份和类型
                            </p>
                        </div>
                    </div>
                </div>

                {/* File List */}
                {files.length > 0 && (
                    <div className="flex-1 overflow-hidden flex flex-col px-6 pt-4 min-h-0">
                        <div className="flex items-center justify-between mb-2">
                            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center">
                                <svg className="w-4 h-4 mr-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                文件列表 ({files.length}个)
                            </h3>
                            {!isProcessing && (
                                <button
                                    onClick={() => setFiles([])}
                                    className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                                >
                                    清空全部
                                </button>
                            )}
                        </div>

                        <div className="flex-1 overflow-y-auto border border-gray-200/50 dark:border-gray-700/50 rounded-xl bg-gray-50/50 dark:bg-gray-900/30">
                            {files.map((fileItem, index) => (
                                <div
                                    key={fileItem.id}
                                    className={`flex items-center gap-3 px-4 py-3 border-b border-gray-100 dark:border-gray-800 last:border-b-0 transition-colors ${currentIndex === index ? "bg-indigo-50/50 dark:bg-indigo-900/10" : ""
                                        } ${fileItem.status === "success" ? "bg-green-50/30 dark:bg-green-900/10" : ""} ${fileItem.status === "failed" ? "bg-red-50/30 dark:bg-red-900/10" : ""
                                        }`}
                                >
                                    {/* Status Icon */}
                                    <div className="flex-shrink-0">{getStatusIcon(fileItem.status)}</div>

                                    {/* File Info */}
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" title={fileItem.filename}>
                                            {fileItem.filename}
                                        </div>

                                        {editingId === fileItem.id ? (
                                            /* Edit Mode */
                                            <div className="flex items-center gap-2 mt-1.5">
                                                <label className="text-xs text-gray-500">年份:</label>
                                                <input
                                                    type="text"
                                                    value={fileItem.year}
                                                    onChange={(e) => updateFile(fileItem.id, { year: e.target.value })}
                                                    className="w-16 px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                    placeholder="年份"
                                                />
                                                <label className="text-xs text-gray-500">类型:</label>
                                                <select
                                                    value={fileItem.docType}
                                                    onChange={(e) =>
                                                        updateFile(fileItem.id, { docType: e.target.value as "dept_final" | "dept_budget" })
                                                    }
                                                    className="px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                >
                                                    <option value="dept_final">决算</option>
                                                    <option value="dept_budget">预算</option>
                                                </select>
                                                <label className="text-xs text-gray-500">单位:</label>
                                                <select
                                                    value={fileItem.orgId || ""}
                                                    onChange={(e) => {
                                                        const selectedOrg = orgs.find(o => o.id === e.target.value);
                                                        updateFile(fileItem.id, {
                                                            orgId: e.target.value,
                                                            orgName: selectedOrg ? selectedOrg.name : "",
                                                            orgLevel: selectedOrg ? selectedOrg.level : ""
                                                        });
                                                    }}
                                                    className="w-40 px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                >
                                                    <option value="" disabled>请选择所属单位</option>
                                                    {orgs.map(org => (
                                                        <option key={org.id} value={org.id}>
                                                            {org.name} ({org.level === "department" ? "部门" : "单位"})
                                                        </option>
                                                    ))}
                                                </select>
                                                <button
                                                    onClick={() => setEditingId(null)}
                                                    className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                                                >
                                                    完成
                                                </button>
                                            </div>
                                        ) : (
                                            /* Display Mode */
                                            <div className="flex items-center gap-2 mt-0.5">
                                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400">
                                                    {fileItem.year ? `${fileItem.year}年` : "年份未识别"}
                                                </span>
                                                <span
                                                    className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] ${fileItem.docType === "dept_budget"
                                                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                                        : "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400"
                                                        }`}
                                                >
                                                    {fileItem.docType === "dept_budget" ? "预算" : "决算"}
                                                </span>
                                                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] ${fileItem.orgId ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"}`}>
                                                    {fileItem.orgName ? `${fileItem.orgName} (${fileItem.orgLevel === "department" ? "部门" : "单位"})` : "未识别所属单位 (需编辑补充)"}
                                                </span>
                                                {fileItem.message && (
                                                    <span className="text-xs text-gray-500 dark:text-gray-400 truncate">
                                                        {fileItem.message}
                                                    </span>
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    {/* Actions */}
                                    {fileItem.status === "pending" && !isProcessing && (
                                        <div className="flex items-center gap-1 flex-shrink-0">
                                            <button
                                                onClick={() => setEditingId(editingId === fileItem.id ? null : fileItem.id)}
                                                className="p-1.5 rounded-lg hover:bg-gray-200/50 dark:hover:bg-gray-700/50 text-gray-400 hover:text-indigo-600 transition-colors"
                                                title="编辑"
                                            >
                                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                </svg>
                                            </button>
                                            <button
                                                onClick={() => removeFile(fileItem.id)}
                                                className="p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 transition-colors"
                                                title="删除"
                                            >
                                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                </svg>
                                            </button>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Progress Bar */}
                {isProcessing && (
                    <div className="px-6 pt-3">
                        <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500 ease-out"
                                style={{ width: `${progress.percent}%` }}
                            />
                        </div>
                        <p className="text-xs text-gray-500 text-center mt-1.5">
                            进度: {progress.completed}/{progress.total} ({progress.percent}%)
                        </p>
                    </div>
                )}

                {/* Stats */}
                {!isProcessing && progress.completed > 0 && (
                    <div className="px-6 pt-3 flex items-center justify-center gap-6 text-sm">
                        <span className="flex items-center gap-1 text-green-600">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                            成功: {stats.success}
                        </span>
                        <span className="flex items-center gap-1 text-red-500">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                            </svg>
                            失败: {stats.failed}
                        </span>
                        <span className="flex items-center gap-1 text-gray-400">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                            </svg>
                            跳过: {stats.skipped}
                        </span>
                    </div>
                )}

                {/* Footer Actions */}
                <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200/50 dark:border-gray-700/50 mt-2">
                    <button
                        onClick={onClose}
                        disabled={isProcessing}
                        className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {progress.completed > 0 && !isProcessing ? "关闭" : "取消"}
                    </button>

                    {!isProcessing ? (
                        <button
                            onClick={startProcessing}
                            disabled={files.filter((f) => f.status === "pending").length === 0 || files.some((f) => !f.orgId)}
                            title={files.some((f) => !f.orgId) ? "有文件未指定所属单位" : ""}
                            className="px-6 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 rounded-xl shadow-lg shadow-indigo-500/30 hover:shadow-xl hover:shadow-indigo-500/40 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none flex items-center gap-2"
                        >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            开始批量上传 ({files.filter((f) => f.status === "pending").length})
                        </button>
                    ) : (
                        <div className="flex items-center gap-2 text-sm text-indigo-600 dark:text-indigo-400 font-medium">
                            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            正在处理...
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
