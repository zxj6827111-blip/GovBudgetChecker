"use client";

import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { isHeadUnit, formatUnitOption } from "../../lib/unitMatch";

// ───────────── Types ─────────────

interface FileItem {
    id: string;
    file: File;
    filename: string;
    year: string;
    docType: "dept_final" | "dept_budget";
    departmentId?: string;
    departmentName?: string;
    subjectLevel?: UploadSubjectLevel;
    orgId?: string;
    orgName?: string;
    orgLevel?: string;
    matchSource?: "manual" | "auto" | "default" | "remembered";
    matchConfidence?: "high" | "medium" | "low";
    matchHint?: string;
    /** 首页识别缺失或与初始值冲突时，必须由操作员显式确认。 */
    metadataNeedsConfirmation?: boolean;
    metadataConfirmed?: boolean;
    metadataHint?: string;
    /** 低置信度组织匹配不能直接提交，确认动作会被单独记录在前端状态中。 */
    organizationConfirmed?: boolean;
    versionId?: string;
    isDetecting?: boolean;
    status: "pending" | "uploading" | "triggering" | "success" | "failed" | "skipped";
    message: string;
}

interface DocumentPreflightMatch {
    organization_id: string;
    organization_name: string;
    level: string;
    department_id?: string | null;
    department_name?: string | null;
    confidence: number;
    match_basis: string;
}

interface DocumentPreflightResponse {
    filename: string;
    report_year?: number | null;
    fiscal_year?: string;
    doc_type?: string | null;
    report_kind?: string;
    cover_title?: string;
    cover_org_name?: string;
    cover_org_label?: string;
    scope_hint?: string;
    current?: DocumentPreflightMatch | null;
    suggestions?: DocumentPreflightMatch[];
}

interface BatchUploadModalProps {
    orgUnitId?: string; // 已选单位（局部模式），如果未传则是全区模式
    defaultDocType: "dept_final" | "dept_budget";
    useLocalRules?: boolean;
    useAiAssist?: boolean;
    onClose: () => void;
    /** 成功创建并启动分析的任务 ID，供调用方定位刚上传的材料。 */
    onComplete: (jobIds: string[]) => void | Promise<void>;
}

interface OrganizationOption {
    id: string;
    name: string;
    level: string;
    level_name?: string;
    parent_id?: string | null;
    children?: OrganizationOption[];
}

type UploadSubjectLevel = "department_summary" | "unit";

type WebkitFileSystemEntry = {
    name: string;
    isFile: boolean;
    isDirectory: boolean;
};

type WebkitFileSystemFileEntry = WebkitFileSystemEntry & {
    file: (
        successCallback: (file: File) => void,
        errorCallback?: (error: DOMException) => void
    ) => void;
};

type WebkitFileSystemDirectoryReader = {
    readEntries: (
        successCallback: (entries: WebkitFileSystemEntry[]) => void,
        errorCallback?: (error: DOMException) => void
    ) => void;
};

type WebkitFileSystemDirectoryEntry = WebkitFileSystemEntry & {
    createReader: () => WebkitFileSystemDirectoryReader;
};

type DataTransferItemWithEntry = DataTransferItem & {
    webkitGetAsEntry?: () => unknown;
};

type BrowserFileHandle = {
    kind: "file";
    name: string;
    getFile: () => Promise<File>;
};

type BrowserDirectoryHandle = {
    kind: "directory";
    name: string;
    values: () => AsyncIterator<BrowserFileHandle | BrowserDirectoryHandle>;
};

type WindowWithDirectoryPicker = Window & {
    showDirectoryPicker?: (options?: { mode?: "read" }) => Promise<BrowserDirectoryHandle>;
};

function isWebkitFileSystemEntry(entry: unknown): entry is WebkitFileSystemEntry {
    if (!entry || typeof entry !== "object") {
        return false;
    }
    const candidate = entry as Partial<WebkitFileSystemEntry>;
    return typeof candidate.name === "string" && typeof candidate.isFile === "boolean" && typeof candidate.isDirectory === "boolean";
}

const MAX_FILES = 200;
const PREFLIGHT_BATCH_THRESHOLD = 20;
const PREFLIGHT_CONCURRENCY = 1;
const PREFLIGHT_RETRY_LIMIT = 2;
const PREFLIGHT_RETRY_DELAY_MS = 2500;
const REQUEST_RATE_LIMIT_DELAY_MS = 750;
const UPLOAD_PREFS_KEY = "gbc_batch_upload_prefs_v1";
const REQUEST_TIMEOUT_MS = 180000;
const DIRECTORY_INPUT_PROPS = { webkitdirectory: "", directory: "" };

// ───────────── Helpers ─────────────

function detectDocType(filename: string, fallback: "dept_final" | "dept_budget"): "dept_final" | "dept_budget" {
    return detectDocTypeFromFilename(filename) || fallback;
}

function detectDocTypeFromFilename(filename: string): "dept_final" | "dept_budget" | undefined {
    const lower = filename.toLowerCase();
    if (filename.includes("决算") || lower.includes("final") || lower.includes("settlement") || lower.includes("accounts")) {
        return "dept_final";
    }
    if (filename.includes("预算") || lower.includes("budget")) {
        return "dept_budget";
    }
    return undefined;
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

function normalizePreflightDocType(value?: string | null): "dept_final" | "dept_budget" | undefined {
    if (!value) return undefined;
    if (value === "dept_budget" || value === "budget") {
        return "dept_budget";
    }
    if (value === "dept_final" || value === "final" || value === "settlement" || value === "accounts") {
        return "dept_final";
    }
    return undefined;
}

function inferSubjectLevel(org?: OrganizationOption | null): UploadSubjectLevel | undefined {
    if (!org) return undefined;
    return org.level === "unit" ? "unit" : org.level === "department" ? "department_summary" : undefined;
}

function toMatchConfidence(confidence?: number | null): FileItem["matchConfidence"] {
    const value = Number(confidence || 0);
    if (value >= 0.85) return "high";
    if (value >= 0.65) return "medium";
    return "low";
}

function buildPreflightMatchHint(
    payload: DocumentPreflightResponse,
    current: DocumentPreflightMatch
): string {
    const label = payload.cover_org_label?.trim();
    if (current.level === "department") {
        if (label) {
            return `已按首页“${label}”识别到部门：${current.organization_name}`;
        }
        return `已按PDF首页识别到部门：${current.organization_name}`;
    }
    if (label) {
        return `已按首页“${label}”识别到单位：${current.organization_name}`;
    }
    return `已按PDF首页识别到单位：${current.organization_name}`;
}

function buildPreflightPendingHint(payload: DocumentPreflightResponse): string {
    if (payload.cover_org_name) {
        const label = payload.cover_org_label?.trim() || "首页字段";
        return `首页识别到“${label}”：${payload.cover_org_name}，请确认映射`;
    }
    if (payload.cover_title) {
        return `已识别首页标题：${payload.cover_title}`;
    }
    return "已尝试读取PDF首页，未识别到明确的部门或单位";
}

async function postWithTimeout(url: string, body: FormData | string, headers?: HeadersInit) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
        return await fetch(url, {
            method: "POST",
            body,
            headers,
            signal: controller.signal,
        });
    } finally {
        clearTimeout(timer);
    }
}

async function readErrorMessage(response: Response) {
    const text = await response.text();
    try {
        const payload = JSON.parse(text);
        return (
            payload?.detail ||
            payload?.error ||
            payload?.message ||
            text ||
            `HTTP ${response.status}`
        );
    } catch {
        return text || `HTTP ${response.status}`;
    }
}

function isRateLimitMessage(message: string): boolean {
    return /too many requests|rate limit|请求.*频繁|限流|429/i.test(message);
}

function formatRateLimitMessage(context: string): string {
    return `${context}请求过于频繁，系统已自动放慢速度重试。`;
}

function formatRequestFailureMessage(message: string, context: string): string {
    if (isRateLimitMessage(message)) {
        return `${context}请求仍被限流，请稍后点击“仅重试失败项”。`;
    }
    return message || `${context}失败`;
}

function formatPreflightFailureMessage(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error || "");
    if (isRateLimitMessage(message)) {
        return "首页识别请求过于频繁，已保留文件名匹配结果，可稍后重试或直接继续上传。";
    }
    return message || "首页识别失败，已保留文件名识别结果";
}

function getRetryAfterMs(response: Response, fallbackMs: number): number {
    const retryAfter = response.headers.get("Retry-After");
    if (!retryAfter) return fallbackMs;

    const retryAfterSeconds = Number(retryAfter);
    if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds >= 0) {
        return Math.min(Math.max(retryAfterSeconds * 1000, 0), 60_000);
    }

    const retryAfterDate = Date.parse(retryAfter);
    if (Number.isFinite(retryAfterDate)) {
        return Math.min(Math.max(retryAfterDate - Date.now(), 0), 60_000);
    }

    return fallbackMs;
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postWithRateLimitRetry(
    url: string,
    body: FormData | string,
    headers: HeadersInit | undefined,
    options: {
        context: string;
        retryLimit?: number;
        retryDelayMs?: number;
        onRetry?: (attempt: number, delayMs: number) => void;
    }
): Promise<Response> {
    const retryLimit = options.retryLimit ?? PREFLIGHT_RETRY_LIMIT;
    const retryDelayMs = options.retryDelayMs ?? PREFLIGHT_RETRY_DELAY_MS;

    for (let attempt = 0; attempt <= retryLimit; attempt += 1) {
        const response = await postWithTimeout(url, body, headers);
        if (response.status !== 429 || attempt >= retryLimit) {
            return response;
        }

        const delayMs = getRetryAfterMs(response, retryDelayMs * (attempt + 1));
        options.onRetry?.(attempt + 1, delayMs);
        await sleep(delayMs);
    }

    return postWithTimeout(url, body, headers);
}

function sortOrganizationsByName(a: OrganizationOption, b: OrganizationOption): number {
    return a.name.localeCompare(b.name, "zh-CN");
}

function getUploadFileLabel(file: File): string {
    return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

function getFileIdentity(file: File): string {
    return `${getUploadFileLabel(file)}::${file.size}::${file.lastModified}`;
}

function isPdfFile(file: File): boolean {
    return file.name.toLowerCase().endsWith(".pdf") || file.type === "application/pdf";
}

function normalizeOrgMatchText(value: string): string {
    return value
        .replace(/\.[^.]+$/, "")
        .replace(/[^0-9A-Za-z\u3400-\u9fff]/g, "")
        .toLowerCase();
}

function findOrganizationByUploadName(
    uploadName: string,
    organizations: OrganizationOption[]
): OrganizationOption | undefined {
    const normalizedUploadName = normalizeOrgMatchText(uploadName);
    if (!normalizedUploadName) return undefined;

    return organizations
        .map((org) => ({ org, normalizedName: normalizeOrgMatchText(org.name) }))
        .filter(({ normalizedName }) => normalizedName.length >= 2 && normalizedUploadName.includes(normalizedName))
        .sort((left, right) => {
            if (right.normalizedName.length !== left.normalizedName.length) {
                return right.normalizedName.length - left.normalizedName.length;
            }
            if (left.org.level !== right.org.level) {
                return left.org.level === "unit" ? -1 : 1;
            }
            return sortOrganizationsByName(left.org, right.org);
        })[0]?.org;
}

async function readEntryFile(entry: WebkitFileSystemFileEntry): Promise<File[]> {
    return new Promise((resolve) => {
        entry.file(
            (file) => resolve([file]),
            () => resolve([])
        );
    });
}

async function readDirectoryBatch(reader: WebkitFileSystemDirectoryReader): Promise<WebkitFileSystemEntry[]> {
    return new Promise((resolve) => {
        reader.readEntries(
            (entries) => resolve(entries),
            () => resolve([])
        );
    });
}

async function collectFilesFromEntry(entry: WebkitFileSystemEntry): Promise<File[]> {
    if (entry.isFile) {
        return readEntryFile(entry as WebkitFileSystemFileEntry);
    }
    if (!entry.isDirectory) {
        return [];
    }

    const reader = (entry as WebkitFileSystemDirectoryEntry).createReader();
    const files: File[] = [];

    while (true) {
        const entries = await readDirectoryBatch(reader);
        if (entries.length === 0) break;
        const nested = await Promise.all(entries.map((child) => collectFilesFromEntry(child)));
        files.push(...nested.flat());
    }

    return files;
}

async function collectFilesFromDataTransfer(dataTransfer: DataTransfer): Promise<File[]> {
    const items = Array.from(dataTransfer.items || []) as DataTransferItemWithEntry[];
    const rawEntries: unknown[] = items
        .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
        .filter((entry) => entry !== null);
    const entries: WebkitFileSystemEntry[] = rawEntries.filter(isWebkitFileSystemEntry);

    if (entries.length > 0) {
        const nested = await Promise.all(entries.map((entry) => collectFilesFromEntry(entry)));
        return nested.flat();
    }

    return Array.from(dataTransfer.files || []);
}

async function collectFilesFromDirectoryHandle(directory: BrowserDirectoryHandle): Promise<File[]> {
    const files: File[] = [];
    const iterator = directory.values();

    while (true) {
        const next = await iterator.next();
        if (next.done) break;
        const entry = next.value;
        if (entry.kind === "file") {
            files.push(await entry.getFile());
        } else if (entry.kind === "directory") {
            files.push(...await collectFilesFromDirectoryHandle(entry));
        }
    }

    return files;
}

// ───────────── Component ─────────────

export default function BatchUploadModal({
    orgUnitId,
    defaultDocType,
    useLocalRules = true,
    useAiAssist = true,
    onClose,
    onComplete,
}: BatchUploadModalProps) {
    const isScopedUpload = Boolean(orgUnitId);
    const [files, setFiles] = useState<FileItem[]>([]);
    const [orgs, setOrgs] = useState<OrganizationOption[]>([]);
    const [isProcessing, setIsProcessing] = useState(false);
    const [currentIndex, setCurrentIndex] = useState(-1);
    const [isDragging, setIsDragging] = useState(false);
    const [isCollectingDrop, setIsCollectingDrop] = useState(false);
    const [selectionNotice, setSelectionNotice] = useState("");
    const [editingId, setEditingId] = useState<string | null>(null);
    const [bulkDepartmentId, setBulkDepartmentId] = useState("");
    const [bulkSubjectLevel, setBulkSubjectLevel] = useState<UploadSubjectLevel>("department_summary");
    const [bulkUnitId, setBulkUnitId] = useState("");
    const [departmentFilter, setDepartmentFilter] = useState("");
    const [unitFilter, setUnitFilter] = useState("");
    const [rememberedSelection, setRememberedSelection] = useState<{
        departmentId: string;
        subjectLevel: UploadSubjectLevel;
        unitId: string;
        docType: "dept_final" | "dept_budget";
        year: string;
    }>({
        departmentId: "",
        subjectLevel: "department_summary",
        unitId: "",
        docType: defaultDocType,
        year: "",
    });
    const fileInputRef = useRef<HTMLInputElement>(null);
    const folderInputRef = useRef<HTMLInputElement>(null);

    const departments = useMemo(
        () => orgs.filter((org) => org.level === "department").slice().sort(sortOrganizationsByName),
        [orgs]
    );

    const departmentMap = useMemo(
        () => new Map(departments.map((department) => [department.id, department])),
        [departments]
    );

    const orgMap = useMemo(() => new Map(orgs.map((org) => [org.id, org])), [orgs]);

    const unitsByDepartment = useMemo(() => {
        const grouped = new Map<string, OrganizationOption[]>();
        orgs.forEach((org) => {
            if (org.level !== "unit" || !org.parent_id) return;
            const existing = grouped.get(org.parent_id) || [];
            existing.push(org);
            grouped.set(org.parent_id, existing);
        });
        grouped.forEach((items, key) => {
            grouped.set(key, items.slice().sort(sortOrganizationsByName));
        });
        return grouped;
    }, [orgs]);

    const visibleDepartments = useMemo(() => {
        const keyword = departmentFilter.trim().toLowerCase();
        const filtered = !keyword ? departments : departments.filter((department) =>
            department.name.toLowerCase().includes(keyword)
        );
        if (!bulkDepartmentId || filtered.some((department) => department.id === bulkDepartmentId)) {
            return filtered;
        }
        const selectedDepartment = departmentMap.get(bulkDepartmentId);
        return selectedDepartment ? [selectedDepartment, ...filtered] : filtered;
    }, [bulkDepartmentId, departmentFilter, departmentMap, departments]);

    const getVisibleUnits = useCallback(
        (departmentId: string, selectedUnitId = "") => {
            const units = unitsByDepartment.get(departmentId) || [];
            const keyword = unitFilter.trim().toLowerCase();
            const filtered = !keyword ? units : units.filter((unit) => unit.name.toLowerCase().includes(keyword));
            if (!selectedUnitId || filtered.some((unit) => unit.id === selectedUnitId)) {
                return filtered;
            }
            const selectedUnit = units.find((unit) => unit.id === selectedUnitId);
            return selectedUnit ? [selectedUnit, ...filtered] : filtered;
        },
        [unitFilter, unitsByDepartment]
    );

    const getPreferredUnit = useCallback(
        (departmentId: string, filename?: string): OrganizationOption | undefined => {
            const units = unitsByDepartment.get(departmentId) || [];
            if (units.length === 0) return undefined;

            if (filename) {
                const exactMatchedUnit = units.find((unit) => filename.includes(unit.name));
                if (exactMatchedUnit) return exactMatchedUnit;
            }

            const baseUnit = units.find((unit) => unit.name.includes("本级"));
            if (baseUnit) return baseUnit;

            if (units.length === 1) return units[0];

            return undefined;
        },
        [unitsByDepartment]
    );

    const matchUploadName = useCallback(
        (uploadName: string): Partial<FileItem> => {
            if (orgUnitId) {
                const selected = orgs.find((org) => org.id === orgUnitId);
                const selectedDepartment = selected?.level === "unit" && selected.parent_id
                    ? departmentMap.get(selected.parent_id)
                    : selected?.level === "department" ? selected : undefined;

                return {
                    departmentId: selectedDepartment?.id || "",
                    departmentName: selectedDepartment?.name || "",
                    subjectLevel: inferSubjectLevel(selected),
                    orgId: orgUnitId,
                    orgName: selected?.name || "",
                    orgLevel: selected?.level || "",
                    matchSource: "default",
                    matchConfidence: "high",
                    matchHint: "已使用当前选中的单位",
                };
            }
            if (orgs.length === 0) return {};

            let match = findOrganizationByUploadName(uploadName, orgs);
            if (!match) return {};

            const isUnitIntended = uploadName.includes("单位") || uploadName.includes("本级");
            const isDeptIntended = uploadName.includes("部门");
            if (match.level === "unit" && isDeptIntended) {
                match = orgs.find((org) => org.id === match?.parent_id) || match;
            }

            if (match.level === "department") {
                const preferredUnit = isUnitIntended ? getPreferredUnit(match.id, uploadName) : undefined;
                if (preferredUnit) {
                    return {
                        departmentId: match.id,
                        departmentName: match.name,
                        subjectLevel: "unit",
                        orgId: preferredUnit.id,
                        orgName: preferredUnit.name,
                        orgLevel: preferredUnit.level,
                        matchSource: "auto",
                        matchConfidence: isUnitIntended ? "high" : "medium",
                        matchHint: isUnitIntended
                            ? `已按文件名自动匹配到单位：${preferredUnit.name}`
                            : `已匹配到部门 ${match.name}，并自动带出默认单位 ${preferredUnit.name}`,
                    };
                }
                return {
                    departmentId: match.id,
                    departmentName: match.name,
                    subjectLevel: "department_summary",
                    orgId: match.id,
                    orgName: match.name,
                    orgLevel: match.level,
                    matchSource: "auto",
                    matchConfidence: isDeptIntended ? "high" : "medium",
                    matchHint: `已匹配到部门 ${match.name}，未选单位时将按部门级上传。`,
                };
            }

            const parentDepartment =
                match.parent_id && departmentMap.has(match.parent_id)
                    ? departmentMap.get(match.parent_id)
                    : undefined;
            return {
                departmentId: parentDepartment?.id || "",
                departmentName: parentDepartment?.name || "",
                subjectLevel: "unit",
                orgId: match.id,
                orgName: match.name,
                orgLevel: match.level,
                matchSource: "auto",
                matchConfidence: "high",
                matchHint: `已按文件名自动匹配到单位：${match.name}`,
            };
        },
        [departmentMap, getPreferredUnit, orgUnitId, orgs]
    );

    useEffect(() => {
        if (typeof window === "undefined") return;
        try {
            const raw = window.localStorage.getItem(UPLOAD_PREFS_KEY);
            if (!raw) return;
            const parsed = JSON.parse(raw) as Partial<{
                departmentId: string;
                subjectLevel: UploadSubjectLevel;
                unitId: string;
                docType: "dept_final" | "dept_budget";
                year: string;
            }>;
            const next = {
                departmentId: String(parsed.departmentId || "").trim(),
                subjectLevel: parsed.subjectLevel === "unit" ? "unit" as const : "department_summary" as const,
                unitId: String(parsed.unitId || "").trim(),
                docType: parsed.docType === "dept_final" ? "dept_final" : parsed.docType === "dept_budget" ? "dept_budget" : defaultDocType,
                year: String(parsed.year || "").trim(),
            };
            setRememberedSelection(next);
            setBulkDepartmentId(next.departmentId);
            setBulkSubjectLevel(next.subjectLevel);
            setBulkUnitId(next.unitId);
        } catch (error) {
            console.error("Failed to restore batch upload preferences:", error);
        }
    }, [defaultDocType]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const nextDocType = rememberedSelection.docType || defaultDocType;
        const payload = {
            departmentId: bulkDepartmentId || rememberedSelection.departmentId,
            subjectLevel: bulkDepartmentId ? bulkSubjectLevel : rememberedSelection.subjectLevel,
            unitId: bulkDepartmentId ? bulkUnitId : rememberedSelection.unitId,
            docType: nextDocType,
            year: rememberedSelection.year || "",
        };
        try {
            window.localStorage.setItem(UPLOAD_PREFS_KEY, JSON.stringify(payload));
        } catch (error) {
            console.error("Failed to persist batch upload preferences:", error);
        }
    }, [bulkDepartmentId, bulkSubjectLevel, bulkUnitId, defaultDocType, rememberedSelection]);

    // 拉取全区组织用于匹配和下拉选择
    useEffect(() => {
        let alive = true;

        const flattenTree = (nodes: OrganizationOption[]): OrganizationOption[] => {
            const result: OrganizationOption[] = [];
            const walk = (items: OrganizationOption[]) => {
                for (const item of items) {
                    if (!item || !item.id) continue;
                    result.push({
                        id: item.id,
                        name: item.name,
                        level: item.level,
                        level_name: item.level_name,
                        parent_id: item.parent_id ?? null,
                    });
                    if (Array.isArray(item.children) && item.children.length > 0) {
                        walk(item.children);
                    }
                }
            };
            walk(nodes);
            return result;
        };

        const loadOrganizations = async () => {
            try {
                const response = await fetch("/api/organizations/list");
                const data = await response.json().catch(() => ({}));
                const list = Array.isArray(data.organizations) ? data.organizations : [];
                if (list.length > 0) {
                    if (alive) setOrgs(list);
                    return;
                }
            } catch (error) {
                console.error("Failed to load organizations list:", error);
            }

            try {
                const [deptRes, orgTreeRes] = await Promise.all([
                    fetch("/api/departments"),
                    fetch("/api/organizations"),
                ]);

                const deptData = await deptRes.json().catch(() => ({}));
                const orgTreeData = await orgTreeRes.json().catch(() => ({}));

                const departments = Array.isArray(deptData.departments) ? deptData.departments : [];
                const tree = Array.isArray(orgTreeData.tree) ? orgTreeData.tree : [];
                const treeOrgs = flattenTree(tree);

                const merged = new Map<string, OrganizationOption>();
                [...departments, ...treeOrgs].forEach((item: OrganizationOption) => {
                    if (!item || !item.id) return;
                    merged.set(item.id, {
                        id: item.id,
                        name: item.name,
                        level: item.level,
                        level_name:
                            item.level_name ||
                            (item.level === "department" ? "部门" : "单位"),
                        parent_id: item.parent_id ?? null,
                    });
                });

                if (alive) {
                    setOrgs(Array.from(merged.values()));
                }
            } catch (error) {
                console.error("Failed to load organizations from fallback endpoints:", error);
                if (alive) setOrgs([]);
            }
        };

        loadOrganizations();
        return () => {
            alive = false;
        };
    }, []);

    useEffect(() => {
        if (!orgUnitId || orgs.length === 0) return;
        const targetOrg = orgMap.get(orgUnitId);
        if (!targetOrg) return;
        const targetDepartment =
            targetOrg.parent_id && departmentMap.has(targetOrg.parent_id)
                ? departmentMap.get(targetOrg.parent_id)
                : undefined;
        setFiles((prev) =>
            prev.map((item) =>
                item.orgId === orgUnitId && (!item.orgName || !item.orgLevel)
                    ? {
                        ...item,
                        departmentId: targetDepartment?.id || item.departmentId,
                        departmentName: targetDepartment?.name || item.departmentName,
                        orgName: targetOrg.name,
                        orgLevel: targetOrg.level,
                        matchSource: item.matchSource || "default",
                        matchConfidence: item.matchConfidence || "high",
                        matchHint: item.matchHint || "已使用当前选中的单位",
                    }
                    : item
            )
        );
    }, [departmentMap, orgMap, orgUnitId, orgs.length]);

    useEffect(() => {
        if (isScopedUpload || orgs.length === 0) return;
        setFiles((current) =>
            current.map((item) => {
                if (item.orgId || item.matchSource === "manual") return item;
                const assignment = matchUploadName(item.filename);
                return assignment.orgId ? { ...item, ...assignment } : item;
            })
        );
    }, [isScopedUpload, matchUploadName, orgs.length]);

    // ── File Selection ──

    const applyPreflightResult = useCallback(
        (fileId: string, payload: DocumentPreflightResponse) => {
            setFiles((prev) =>
                prev.map((item) => {
                    if (item.id !== fileId) return item;

                    const next: FileItem = {
                        ...item,
                        isDetecting: false,
                        message: "",
                    };
                    const detectedYear = payload.report_year ? String(payload.report_year) : "";
                    const detectedDocType = normalizePreflightDocType(payload.doc_type);
                    const metadataConflict = Boolean(
                        (detectedYear && item.year && detectedYear !== item.year) ||
                        (detectedDocType && item.docType && detectedDocType !== item.docType)
                    );
                    const metadataIncomplete = !detectedYear || !detectedDocType;

                    // 首页识别优先于文件名猜测和默认值。发生冲突时保留识别结果，
                    // 但必须让操作员确认，避免把 2024 年决算沿用为 2025 年预算。
                    if (detectedYear) {
                        next.year = detectedYear;
                    }
                    if (detectedDocType) {
                        next.docType = detectedDocType;
                    }
                    next.metadataNeedsConfirmation = metadataIncomplete || metadataConflict;
                    next.metadataConfirmed = !next.metadataNeedsConfirmation;
                    next.metadataHint = metadataIncomplete
                        ? "未能完整识别年度或材料类型，请核对后确认。"
                        : metadataConflict
                            ? `首页识别为 ${detectedYear} 年${detectedDocType === "dept_final" ? "决算" : "预算"}，与初始值不一致，请确认。`
                            : "首页已确认年度和材料类型。";

                    if (isScopedUpload || item.matchSource === "manual") {
                        return next;
                    }

                    const current = payload.current || undefined;
                    if (current?.organization_id) {
                        const departmentId =
                            current.department_id ||
                            (current.level === "department" ? current.organization_id : "");
                        const departmentName =
                            current.department_name ||
                            (current.level === "department" ? current.organization_name : "");

                        next.departmentId = departmentId || "";
                        next.departmentName = departmentName || "";
                        next.orgId = current.organization_id;
                        next.orgName = current.organization_name;
                        next.orgLevel = current.level;
                        next.matchSource = "auto";
                        next.matchConfidence = toMatchConfidence(current.confidence);
                        next.matchHint = buildPreflightMatchHint(payload, current);
                        next.organizationConfirmed = toMatchConfidence(current.confidence) !== "low";
                        return next;
                    }

                    if (!item.orgId && (payload.cover_org_name || payload.cover_title)) {
                        next.matchConfidence = "low";
                        next.matchHint = buildPreflightPendingHint(payload);
                        next.organizationConfirmed = false;
                    }

                    return next;
                })
            );
        },
        [isScopedUpload]
    );

    const markPreflightFailed = useCallback((fileId: string, message: string) => {
        setFiles((prev) =>
            prev.map((item) =>
                item.id === fileId
                    ? {
                        ...item,
                        isDetecting: false,
                        message,
                        metadataNeedsConfirmation: true,
                        metadataConfirmed: false,
                        metadataHint: "首页识别失败，请核对年度和材料类型后确认。",
                    }
                    : item
            )
        );
    }, []);

    const markPreflightWaiting = useCallback((fileId: string, message: string) => {
        setFiles((prev) =>
            prev.map((item) =>
                item.id === fileId
                    ? {
                        ...item,
                        isDetecting: true,
                        message,
                    }
                    : item
            )
        );
    }, []);

    const preflightSingleFile = useCallback(
        async (fileItem: FileItem) => {
            const formData = new FormData();
            formData.set("file", fileItem.file);
            if (fileItem.year) {
                formData.set("fiscal_year", fileItem.year);
            }
            formData.set("doc_type", fileItem.docType);

            try {
                const response = await postWithRateLimitRetry("/api/documents/preflight", formData, undefined, {
                    context: "首页识别",
                    onRetry: () => {
                        markPreflightWaiting(fileItem.id, formatRateLimitMessage("首页识别"));
                    },
                });
                if (!response.ok) {
                    throw new Error(formatRequestFailureMessage(await readErrorMessage(response), "首页识别"));
                }
                const payload = (await response.json()) as DocumentPreflightResponse;
                applyPreflightResult(fileItem.id, payload);
            } catch (error: any) {
                markPreflightFailed(
                    fileItem.id,
                    formatPreflightFailureMessage(error)
                );
            }
        },
        [applyPreflightResult, markPreflightFailed, markPreflightWaiting]
    );

    const preflightFiles = useCallback(
        async (items: FileItem[]) => {
            const queue = items.slice();
            const workerCount = Math.min(PREFLIGHT_CONCURRENCY, queue.length);
            const workers = Array.from({ length: workerCount }, async () => {
                while (queue.length > 0) {
                    const next = queue.shift();
                    if (!next) {
                        return;
                    }
                    await preflightSingleFile(next);
                    if (queue.length > 0) {
                        await sleep(REQUEST_RATE_LIMIT_DELAY_MS);
                    }
                }
            });
            await Promise.all(workers);
        },
        [preflightSingleFile]
    );

    const handleFilesSelect = useCallback(
        (fileList: FileList | File[]) => {
            const existingCount = files.length;
            const selectedFiles = Array.from(fileList);
            const existingKeys = new Set(files.map((item) => getFileIdentity(item.file)));
            const pdfFiles = selectedFiles.filter(isPdfFile);
            const newFileArray = pdfFiles.filter((file) => !existingKeys.has(getFileIdentity(file)));
            const ignoredCount = selectedFiles.length - pdfFiles.length;
            const duplicateCount = pdfFiles.length - newFileArray.length;

            if (newFileArray.length === 0) {
                const reason = duplicateCount > 0 ? "这些 PDF 已在列表中" : "没有找到 PDF 文件";
                alert(`未加入新文件：${reason}`);
                return;
            }

            if (existingCount + newFileArray.length > MAX_FILES) {
                alert(`最多支持${MAX_FILES}个文件，当前已有${existingCount}个`);
                return;
            }

            const noticeParts = [`已加入 ${newFileArray.length} 个 PDF`];
            if (ignoredCount > 0) noticeParts.push(`忽略 ${ignoredCount} 个非 PDF 文件`);
            if (duplicateCount > 0) noticeParts.push(`跳过 ${duplicateCount} 个重复文件`);
            if (newFileArray.length > PREFLIGHT_BATCH_THRESHOLD) {
                noticeParts.push("大批量已优先使用文件名匹配");
            }
            setSelectionNotice(`${noticeParts.join("，")}。`);

            const newFiles: FileItem[] = newFileArray.map((file) => {
                const uploadName = getUploadFileLabel(file);
                const assignment = matchUploadName(uploadName);

                return {
                    id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
                    file,
                    filename: uploadName,
                    year: detectFiscalYear(uploadName),
                    docType: detectDocType(uploadName, defaultDocType),
                    metadataNeedsConfirmation: !detectFiscalYear(uploadName) || !detectDocTypeFromFilename(uploadName),
                    metadataConfirmed: Boolean(detectFiscalYear(uploadName) && detectDocTypeFromFilename(uploadName)),
                    metadataHint: detectFiscalYear(uploadName) && detectDocTypeFromFilename(uploadName)
                        ? "已按文件名识别年度和材料类型。"
                        : "文件名未完整识别年度或材料类型，等待首页识别。",
                    ...assignment,
                    isDetecting: true,
                    status: "pending",
                    message: "正在识别PDF首页...",
                };
            });

            const normalizedFiles = newFiles.map((item) => {
                const selectedOrg = item.orgId ? orgMap.get(item.orgId) : undefined;

                if (!selectedOrg) {
                    if (!isScopedUpload && rememberedSelection.departmentId && !rememberedSelection.unitId) {
                        const rememberedDepartment =
                            rememberedSelection.departmentId && departmentMap.has(rememberedSelection.departmentId)
                                ? departmentMap.get(rememberedSelection.departmentId)
                                : undefined;
                        if (rememberedDepartment) {
                            return {
                                ...item,
                                departmentId: rememberedDepartment.id,
                                departmentName: rememberedDepartment.name,
                                orgId: rememberedDepartment.id,
                                orgName: rememberedDepartment.name,
                                orgLevel: rememberedDepartment.level,
                                matchSource: "remembered" as const,
                                matchConfidence: "medium" as const,
                                matchHint: `已带入上次选择：${rememberedDepartment.name}（部门级上传）`,
                                year: item.year || rememberedSelection.year || "",
                                docType: item.docType || rememberedSelection.docType || defaultDocType,
                            };
                        }
                    }
                    if (!isScopedUpload && rememberedSelection.unitId) {
                        const rememberedOrg = orgMap.get(rememberedSelection.unitId);
                        const rememberedDepartment =
                            rememberedSelection.departmentId && departmentMap.has(rememberedSelection.departmentId)
                                ? departmentMap.get(rememberedSelection.departmentId)
                                : undefined;
                        if (rememberedOrg && rememberedOrg.level === "unit" && rememberedDepartment) {
                            return {
                                ...item,
                                departmentId: rememberedDepartment.id,
                                departmentName: rememberedDepartment.name,
                                orgId: rememberedOrg.id,
                                orgName: rememberedOrg.name,
                                orgLevel: rememberedOrg.level,
                                matchSource: "remembered" as const,
                                matchConfidence: "medium" as const,
                                matchHint: `已带入上次选择：${rememberedDepartment.name} / ${rememberedOrg.name}`,
                                year: item.year || rememberedSelection.year || "",
                                docType: item.docType || rememberedSelection.docType || defaultDocType,
                            };
                        }
                    }
                    return item;
                }

                if (selectedOrg.level === "department") {
                    return {
                        ...item,
                        departmentId: selectedOrg.id,
                        departmentName: selectedOrg.name,
                        orgId: selectedOrg.id,
                        orgName: selectedOrg.name,
                        orgLevel: selectedOrg.level,
                        matchSource: item.matchSource || "manual",
                        matchConfidence: item.matchConfidence || "high",
                        matchHint: item.matchHint || `已选择部门 ${selectedOrg.name}，未选单位时将按部门级上传。`,
                    };
                }

                const parentDepartment =
                    selectedOrg.parent_id && departmentMap.has(selectedOrg.parent_id)
                        ? departmentMap.get(selectedOrg.parent_id)
                        : undefined;

                return {
                    ...item,
                    departmentId: parentDepartment?.id || item.departmentId,
                    departmentName: parentDepartment?.name || item.departmentName,
                    orgId: selectedOrg.id,
                    orgName: selectedOrg.name,
                    orgLevel: selectedOrg.level,
                    matchSource: item.matchSource || "auto",
                    matchConfidence: item.matchConfidence || "high",
                    matchHint: item.matchHint || `已自动匹配到单位：${selectedOrg.name}`,
                };
            });

            const shouldPreflightItem = (item: FileItem) =>
                normalizedFiles.length <= PREFLIGHT_BATCH_THRESHOLD ||
                !item.year ||
                (!isScopedUpload && !item.orgId) ||
                item.matchConfidence === "low";
            const preflightItems = normalizedFiles.filter(shouldPreflightItem);
            const preflightIds = new Set(preflightItems.map((item) => item.id));
            const displayFiles = normalizedFiles.map((item) =>
                preflightIds.has(item.id)
                    ? item
                    : {
                        ...item,
                        isDetecting: false,
                        message: "已按文件名匹配，跳过首页识别",
                    }
            );

            setFiles((prev) => [...prev, ...displayFiles]);
            if (preflightItems.length > 0) {
                void preflightFiles(preflightItems);
            }
        },
        [defaultDocType, departmentMap, files, isScopedUpload, matchUploadName, orgMap, preflightFiles, rememberedSelection]
    );

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        setIsDragging(true);
    };
    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) {
            return;
        }
        setIsDragging(false);
    };
    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        setIsCollectingDrop(true);
        try {
            const droppedFiles = await collectFilesFromDataTransfer(e.dataTransfer);
            if (droppedFiles.length > 0) {
                handleFilesSelect(droppedFiles);
            }
        } finally {
            setIsCollectingDrop(false);
        }
    };
    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) handleFilesSelect(e.target.files);
        e.target.value = "";
    };
    const handleFolderInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) handleFilesSelect(e.target.files);
        e.target.value = "";
    };
    const handleFolderSelect = useCallback(async () => {
        if (isProcessing || isCollectingDrop) return;

        const picker = (window as WindowWithDirectoryPicker).showDirectoryPicker;
        if (typeof picker !== "function") {
            folderInputRef.current?.click();
            return;
        }

        setIsCollectingDrop(true);
        try {
            const directory = await picker.call(window, { mode: "read" });
            const selectedFiles = await collectFilesFromDirectoryHandle(directory);
            if (selectedFiles.length > 0) {
                handleFilesSelect(selectedFiles);
            }
        } catch (error) {
            const name = error instanceof DOMException ? error.name : "";
            if (name !== "AbortError") {
                folderInputRef.current?.click();
            }
        } finally {
            setIsCollectingDrop(false);
        }
    }, [handleFilesSelect, isCollectingDrop, isProcessing]);

    // ── File Management ──

    const removeFile = (id: string) => {
        setFiles((prev) => prev.filter((f) => f.id !== id));
    };

    const updateFile = (id: string, updates: Partial<FileItem>) => {
        setFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...updates } : f)));
    };

    const persistRememberedSelection = useCallback(
        (updates: Partial<{ departmentId: string; unitId: string; docType: "dept_final" | "dept_budget"; year: string }>) => {
            setRememberedSelection((prev) => ({
                departmentId: updates.departmentId ?? prev.departmentId,
                subjectLevel: prev.subjectLevel,
                unitId: updates.unitId ?? prev.unitId,
                docType: updates.docType ?? prev.docType,
                year: updates.year ?? prev.year,
            }));
        },
        []
    );

    const buildAssignmentUpdates = useCallback(
        (
            departmentId: string,
            unitId: string,
            options?: {
                source?: FileItem["matchSource"];
                confidence?: FileItem["matchConfidence"];
                hint?: string;
            }
        ): Partial<FileItem> => {
            const selectedDepartment = departmentId ? departmentMap.get(departmentId) : undefined;
            const selectedUnit = departmentId
                ? (unitsByDepartment.get(departmentId) || []).find((unit) => unit.id === unitId)
                : undefined;
            const selectedTarget = selectedUnit || selectedDepartment;

            return {
                departmentId: selectedDepartment?.id || "",
                departmentName: selectedDepartment?.name || "",
                orgId: selectedTarget?.id || "",
                orgName: selectedTarget?.name || "",
                orgLevel: selectedTarget?.level || "",
                matchSource: options?.source,
                matchConfidence: options?.confidence,
                matchHint: options?.hint,
                organizationConfirmed: options?.source === "manual" ? true : undefined,
            };
        },
        [departmentMap, unitsByDepartment]
    );

    const resolveDepartmentId = (fileItem: FileItem): string => {
        if (fileItem.departmentId) return fileItem.departmentId;
        if (!fileItem.orgId) return "";
        const selectedOrg = orgMap.get(fileItem.orgId);
        if (!selectedOrg) return "";
        if (selectedOrg.level === "department") return selectedOrg.id;
        return selectedOrg.parent_id || "";
    };

    const resolveDepartmentName = (fileItem: FileItem): string => {
        if (fileItem.departmentName) return fileItem.departmentName;
        const departmentId = resolveDepartmentId(fileItem);
        return departmentId ? departmentMap.get(departmentId)?.name || "" : "";
    };

    const resolveUnitId = (fileItem: FileItem): string => {
        if (!fileItem.orgId) return "";
        const selectedOrg = orgMap.get(fileItem.orgId);
        if (!selectedOrg || selectedOrg.level !== "unit") return "";
        return selectedOrg.id;
    };

    const resolveUploadTargetId = (fileItem: FileItem): string => {
        if (fileItem.orgId) return fileItem.orgId;
        return resolveDepartmentId(fileItem);
    };

    const needsMetadataConfirmation = (fileItem: FileItem): boolean =>
        Boolean(fileItem.metadataNeedsConfirmation && !fileItem.metadataConfirmed);

    const needsOrganizationConfirmation = (fileItem: FileItem): boolean =>
        !isScopedUpload && (
            !resolveUploadTargetId(fileItem) ||
            (fileItem.matchConfidence === "low" && !fileItem.organizationConfirmed)
        );

    const confirmFileMetadata = (fileId: string) => {
        setFiles((prev) => prev.map((file) =>
            file.id === fileId
                ? {
                    ...file,
                    metadataConfirmed: true,
                    metadataHint: "操作员已确认年度和材料类型。",
                }
                : file
        ));
    };

    const confirmFileOrganization = (fileId: string) => {
        setFiles((prev) => prev.map((file) =>
            file.id === fileId
                ? {
                    ...file,
                    organizationConfirmed: true,
                    matchHint: `${file.matchHint || "自动匹配"}（操作员已确认）`,
                }
                : file
        ));
    };

    const isDepartmentLevelAssignment = (fileItem: FileItem): boolean => {
        return Boolean(resolveDepartmentId(fileItem) && !resolveUnitId(fileItem));
    };

    const handleDepartmentChange = (fileId: string, departmentId: string) => {
        const selectedDepartment = departmentId ? departmentMap.get(departmentId) : undefined;
        const preferredUnit =
            selectedDepartment && departmentId ? getPreferredUnit(departmentId) : undefined;
        const hint = preferredUnit
            ? `已自动带出默认单位：${preferredUnit.name}`
            : selectedDepartment
                ? "已选择部门，未选单位时将按部门级上传。"
                : "";
        updateFile(
            fileId,
            buildAssignmentUpdates(departmentId, preferredUnit?.id || "", {
                source: selectedDepartment ? "manual" : undefined,
                confidence: selectedDepartment ? "high" : undefined,
                hint,
            })
        );
        setBulkDepartmentId(departmentId);
        setBulkUnitId(preferredUnit?.id || "");
        persistRememberedSelection({
            departmentId,
            unitId: preferredUnit?.id || "",
        });
    };

    const handleUnitChange = (fileId: string, departmentId: string, unitId: string) => {
        updateFile(
            fileId,
            buildAssignmentUpdates(departmentId, unitId, {
                source: departmentId ? "manual" : undefined,
                confidence: departmentId ? "high" : undefined,
                hint: unitId
                    ? "已手动确认部门和单位"
                    : departmentId
                        ? "已选择部门，未选单位时将按部门级上传。"
                        : "请先选择部门",
            })
        );
        setBulkDepartmentId(departmentId);
        setBulkUnitId(unitId);
        persistRememberedSelection({
            departmentId,
            unitId,
        });
    };

    const handleBulkDepartmentChange = (departmentId: string) => {
        const preferredUnit = departmentId ? getPreferredUnit(departmentId) : undefined;
        const nextUnitId = preferredUnit?.id || "";
        setBulkDepartmentId(departmentId);
        setBulkUnitId(nextUnitId);
        persistRememberedSelection({
            departmentId,
            unitId: nextUnitId,
        });
    };

    const handleBulkUnitChange = (unitId: string) => {
        setBulkUnitId(unitId);
        persistRememberedSelection({
            departmentId: bulkDepartmentId,
            unitId,
        });
    };

    const applySelectionToAllFiles = () => {
        if (!bulkDepartmentId) {
            alert("请先选择部门");
            return;
        }
        const updates = buildAssignmentUpdates(bulkDepartmentId, bulkUnitId, {
            source: "manual",
            confidence: "high",
            hint: bulkUnitId ? "已批量应用部门和单位" : "已批量应用部门设置，将按部门级上传。",
        });
        setFiles((prev) => prev.map((file) => ({ ...file, ...updates })));
        persistRememberedSelection({
            departmentId: bulkDepartmentId,
            unitId: bulkUnitId,
        });
    };

    const retryFailedFiles = () => {
        setFiles((prev) =>
            prev.map((file) =>
                file.status === "failed"
                    ? {
                        ...file,
                        status: "pending",
                        message: file.message || "准备重试",
                    }
                    : file
            )
        );
    };

    // ── Upload Logic ──

    const uploadSingleFile = async (fileItem: FileItem): Promise<string> => {
        // Step 1: update status to uploading
        setFiles((prev) =>
            prev.map((f) =>
                f.id === fileItem.id ? { ...f, status: "uploading" as const, message: "上传中..." } : f
            )
        );

        // Step 1: upload file
        const targetOrgId = orgUnitId || resolveUploadTargetId(fileItem);
        if (!targetOrgId) {
            throw new Error("请先选择该文件所属的部门或单位");
        }

        const formData = new FormData();
        formData.set("file", fileItem.file);
        formData.set("org_unit_id", targetOrgId);
        if (fileItem.year) {
            formData.append("fiscal_year", fileItem.year);
        }
        formData.append("doc_type", fileItem.docType);

        const uploadRes = await postWithRateLimitRetry("/api/documents/upload", formData, undefined, {
            context: "上传",
            retryLimit: 3,
            retryDelayMs: REQUEST_RATE_LIMIT_DELAY_MS,
            onRetry: (_attempt, delayMs) => {
                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === fileItem.id
                            ? {
                                ...f,
                                message: `上传请求过于频繁，等待 ${Math.ceil(delayMs / 1000)} 秒后自动重试...`,
                            }
                            : f
                    )
                );
            },
        });

        let versionId: string | undefined;

        if (!uploadRes.ok) {
            // Fallback to v2 API
            if (uploadRes.status !== 503) {
                throw new Error(formatRequestFailureMessage(await readErrorMessage(uploadRes), "上传"));
            }

            const v2Form = new FormData();
            v2Form.set("file", fileItem.file);
            v2Form.set("org_id", targetOrgId);
            const v2Res = await postWithRateLimitRetry("/api/upload", v2Form, undefined, {
                context: "上传",
                retryLimit: 3,
                retryDelayMs: REQUEST_RATE_LIMIT_DELAY_MS,
                onRetry: (_attempt, delayMs) => {
                    setFiles((prev) =>
                        prev.map((f) =>
                            f.id === fileItem.id
                                ? {
                                    ...f,
                                    message: `上传请求过于频繁，等待 ${Math.ceil(delayMs / 1000)} 秒后自动重试...`,
                                }
                                : f
                        )
                    );
                },
            });
            if (!v2Res.ok) {
                throw new Error(formatRequestFailureMessage(await readErrorMessage(v2Res), "上传"));
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

        return String(versionId);
    };

    const triggerAnalysis = async (fileItem: FileItem, versionId: string): Promise<void> => {
        // Step 2: Trigger local rules + AI check
        setFiles((prev) =>
            prev.map((f) =>
                f.id === fileItem.id
                    ? { ...f, status: "triggering" as const, message: "触发检查中..." }
                    : f
            )
        );

        const runPayload: Record<string, unknown> = {
            mode: "dual",
            use_local_rules: useLocalRules,
            use_ai_assist: useAiAssist,
            doc_type: fileItem.docType,
        };
        if (fileItem.year) {
            runPayload.fiscal_year = fileItem.year;
            runPayload.report_year = Number(fileItem.year);
        }

        const runRes = await postWithRateLimitRetry(
            `/api/documents/${versionId}/run`,
            JSON.stringify(runPayload),
            { "Content-Type": "application/json" },
            {
                context: "启动检查",
                retryLimit: 3,
                retryDelayMs: REQUEST_RATE_LIMIT_DELAY_MS,
                onRetry: (_attempt, delayMs) => {
                    setFiles((prev) =>
                        prev.map((f) =>
                            f.id === fileItem.id
                                ? {
                                    ...f,
                                    message: `启动检查请求过于频繁，等待 ${Math.ceil(delayMs / 1000)} 秒后自动重试...`,
                                }
                                : f
                        )
                    );
                },
            }
        );
        if (!runRes.ok) {
            throw new Error(formatRequestFailureMessage(await readErrorMessage(runRes), "启动检查"));
        }
    };

    const startProcessing = async () => {
        const pendingFiles = files.filter((f) => f.status === "pending");
        if (pendingFiles.length === 0) {
            alert("没有待处理的文件");
            return;
        }
        const filesRequiringConfirmation = pendingFiles.filter(
            (file) => needsMetadataConfirmation(file) || needsOrganizationConfirmation(file)
        );
        if (filesRequiringConfirmation.length > 0) {
            alert(`请先确认 ${filesRequiringConfirmation.length} 份材料的年度、类型或归属信息。`);
            return;
        }

        setIsProcessing(true);
        const fileIndexMap = new Map(files.map((f, idx) => [f.id, idx]));
        const fileById = new Map(files.map((f) => [f.id, f]));
        const triggerQueue: Array<{ fileId: string; versionId: string }> = [];
        const successfulJobIds: string[] = [];

        for (let i = 0; i < files.length; i++) {
            const fileItem = files[i];
            if (fileItem.status !== "pending") continue;

            setCurrentIndex(i);

            try {
                const versionId = fileItem.versionId || await uploadSingleFile(fileItem);
                triggerQueue.push({ fileId: fileItem.id, versionId });

                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === fileItem.id
                            ? {
                                ...f,
                                versionId,
                                status: "triggering" as const,
                                message: "上传成功，等待批量触发检查...",
                            }
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

            await sleep(REQUEST_RATE_LIMIT_DELAY_MS);
        }

        for (const task of triggerQueue) {
            const fileItem = fileById.get(task.fileId);
            if (!fileItem) continue;
            const idx = fileIndexMap.get(task.fileId);
            if (idx !== undefined) {
                setCurrentIndex(idx);
            }
            try {
                await triggerAnalysis(fileItem, task.versionId);
                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === task.fileId
                            ? { ...f, status: "success" as const, message: "✅ 上传成功，检查已启动" }
                            : f
                        )
                );
                successfulJobIds.push(task.versionId);
            } catch (error: any) {
                setFiles((prev) =>
                    prev.map((f) =>
                        f.id === task.fileId
                            ? {
                                ...f,
                                status: "failed" as const,
                                message: error?.message || "触发检查失败",
                            }
                            : f
                    )
                );
            }
            await sleep(REQUEST_RATE_LIMIT_DELAY_MS);
        }

        setIsProcessing(false);
        setCurrentIndex(-1);
        await onComplete(successfulJobIds);
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
    const hasPendingPreflight = files.some((file) => file.status === "pending" && file.isDetecting);
    const pendingConfirmationCount = files.filter(
        (file) => file.status === "pending" && (needsMetadataConfirmation(file) || needsOrganizationConfirmation(file))
    ).length;

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

    const getMatchBadgeClass = (fileItem: FileItem): string => {
        if (isDepartmentLevelAssignment(fileItem) && fileItem.matchSource === "manual") {
            return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
        }
        if (fileItem.matchSource === "manual") {
            return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
        }
        if (fileItem.matchConfidence === "high") {
            return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
        }
        if (fileItem.matchConfidence === "medium") {
            return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
        }
        if (fileItem.matchConfidence === "low") {
            return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400";
        }
        return "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300";
    };

    const getMatchBadgeLabel = (fileItem: FileItem): string => {
        if (isDepartmentLevelAssignment(fileItem)) {
            return "部门级上传";
        }
        switch (fileItem.matchSource) {
            case "manual":
                return "手动确认";
            case "remembered":
                return "沿用上次";
            case "default":
                return "默认带出";
            case "auto":
                return fileItem.matchConfidence === "low" ? "自动匹配待确认" : "自动匹配";
            default:
                return "待确认";
        }
    };

    // ── Render ──

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm" data-testid="batch-upload-modal">
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-3xl w-full max-h-[90vh] flex flex-col border border-white/20 overflow-hidden animate-in zoom-in-95 duration-300">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200/50 dark:border-gray-700/50 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-gray-800 dark:to-gray-800">
                    <div className="flex items-center space-x-3">
                        <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center text-white shadow-lg shadow-indigo-500/30">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                            </svg>
                        </div>
                        <h2 className="text-xl font-bold text-gray-900 dark:text-white">
                            {isScopedUpload ? "上传报告" : "批量上传文档"}
                        </h2>
                        {files.length > 0 && (
                            <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 rounded-full text-xs font-medium">
                                {files.length} 个文件
                            </span>
                        )}
                        {isScopedUpload && (
                            <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full text-xs font-medium">
                                当前组织
                            </span>
                        )}
                    </div>
                    <button
                        data-testid="batch-upload-close"
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
                        data-testid="batch-upload-dropzone"
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
                            data-testid="batch-upload-file-input"
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileInputChange}
                            accept=".pdf"
                            multiple
                            style={{ display: "none" }}
                            disabled={isProcessing}
                        />
                        <input
                            data-testid="batch-upload-folder-input"
                            type="file"
                            ref={folderInputRef}
                            onChange={handleFolderInputChange}
                            accept=".pdf,application/pdf"
                            multiple
                            style={{ display: "none" }}
                            disabled={isProcessing}
                            {...DIRECTORY_INPUT_PROPS}
                        />
                        <div className="flex flex-col items-center">
                            <div className={`mb-3 transition-transform duration-300 ${isDragging ? "-translate-y-1" : ""}`}>
                                <svg className="w-10 h-10 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                                </svg>
                            </div>
                            <p className="text-sm text-gray-700 dark:text-gray-300">
                                <strong>{isCollectingDrop ? "正在读取拖入内容" : "拖拽 PDF 或文件夹至此"}</strong>
                            </p>
                            <p className="text-xs text-gray-500 mt-1">
                                仅支持 PDF，最多 {MAX_FILES} 个 · 文件名和 PDF 首页会自动匹配部门或单位
                            </p>
                            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                                <button
                                    type="button"
                                    data-testid="batch-upload-select-files"
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        if (!isProcessing) fileInputRef.current?.click();
                                    }}
                                    disabled={isProcessing || isCollectingDrop}
                                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                    选择 PDF 文件
                                </button>
                                <button
                                    type="button"
                                    data-testid="batch-upload-select-folder"
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        void handleFolderSelect();
                                    }}
                                    disabled={isProcessing || isCollectingDrop}
                                    className="rounded-lg border border-indigo-200 bg-white px-4 py-2 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                    选择文件夹
                                </button>
                            </div>
                            {selectionNotice ? (
                                <p data-testid="batch-upload-selection-notice" className="mt-3 text-xs font-medium text-emerald-700">
                                    {selectionNotice}
                                </p>
                            ) : null}
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

                        {!isScopedUpload && (
                            <div className="mb-3 rounded-xl border border-indigo-100 bg-indigo-50/60 px-3 py-3">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs font-medium text-indigo-700">统一设置</span>
                                    <input
                                        type="text"
                                        value={departmentFilter}
                                        onChange={(e) => setDepartmentFilter(e.target.value)}
                                        placeholder="筛选部门"
                                        className="w-28 rounded-md border border-indigo-200 bg-white px-2 py-1 text-xs text-gray-700"
                                    />
                                    <select
                                        data-testid="batch-bulk-department"
                                        value={bulkDepartmentId}
                                        onChange={(e) => handleBulkDepartmentChange(e.target.value)}
                                        className="w-40 rounded-md border border-indigo-200 bg-white px-2 py-1 text-xs text-gray-900"
                                    >
                                        <option value="">请选择部门</option>
                                        {visibleDepartments.map((department) => (
                                            <option key={department.id} value={department.id}>
                                                {department.name}
                                            </option>
                                        ))}
                                    </select>
                                    <input
                                        type="text"
                                        value={unitFilter}
                                        onChange={(e) => setUnitFilter(e.target.value)}
                                        placeholder="筛选单位"
                                        className="w-28 rounded-md border border-indigo-200 bg-white px-2 py-1 text-xs text-gray-700"
                                        disabled={!bulkDepartmentId}
                                    />
                                    <select
                                        data-testid="batch-bulk-unit"
                                        value={bulkUnitId}
                                        onChange={(e) => handleBulkUnitChange(e.target.value)}
                                        disabled={!bulkDepartmentId}
                                        className="w-44 rounded-md border border-indigo-200 bg-white px-2 py-1 text-xs text-gray-900 disabled:bg-gray-100"
                                    >
                                        <option value="">
                                            {bulkDepartmentId ? "不选单位则按部门上传" : "请先选择部门"}
                                        </option>
                                        {getVisibleUnits(bulkDepartmentId, bulkUnitId).map((unit) => (
                                            <option key={unit.id} value={unit.id}>
                                                {formatUnitOption(unit, departmentMap.get(bulkDepartmentId))}
                                            </option>
                                        ))}
                                    </select>
                                    <button
                                        data-testid="batch-apply-all"
                                        type="button"
                                        onClick={applySelectionToAllFiles}
                                        disabled={!bulkDepartmentId}
                                        className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                                    >
                                        应用到全部文件
                                    </button>
                                </div>
                                <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-indigo-700/80">
                                    <span>会自动覆盖当前列表中所有文件的部门/单位。</span>
                                    {bulkDepartmentId ? (
                                        <span className="text-indigo-600">单位可留空，留空时会按部门级上传。</span>
                                    ) : null}
                                    {rememberedSelection.departmentId ? (
                                        <span className="text-indigo-600">
                                            已记住上次选择，后续新增文件会优先带入。
                                        </span>
                                    ) : null}
                                </div>
                            </div>
                        )}

                        <div className="flex-1 overflow-y-auto border border-gray-200/50 dark:border-gray-700/50 rounded-xl bg-gray-50/50 dark:bg-gray-900/30">
                            {files.map((fileItem, index) => (
                                <div
                                    key={fileItem.id}
                                    className={`flex items-center gap-3 px-4 py-3 border-b border-gray-100 dark:border-gray-800 last:border-b-0 transition-colors ${currentIndex === index ? "bg-indigo-50/50 dark:bg-indigo-900/10" : ""
                                        } ${fileItem.status === "success" ? "bg-green-50/30 dark:bg-green-900/10" : ""} ${fileItem.status === "failed" ? "bg-red-50/30 dark:bg-red-900/10" : ""
                                        }`}
                                >
                                    {/* Status Icon */}
                                    <div className="flex-shrink-0">
                                        {getStatusIcon(fileItem.isDetecting ? "uploading" : fileItem.status)}
                                    </div>

                                    {/* File Info */}
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" title={fileItem.filename}>
                                            {fileItem.filename}
                                        </div>

                                        {editingId === fileItem.id ? (
                                            /* Edit Mode */
                                            <div className="flex flex-wrap items-center gap-2 mt-1.5">
                                                <label className="text-xs text-gray-500">年份:</label>
                                                <input
                                                    type="text"
                                                    value={fileItem.year}
                                                    onChange={(e) => {
                                                        updateFile(fileItem.id, {
                                                            year: e.target.value,
                                                            metadataNeedsConfirmation: true,
                                                            metadataConfirmed: false,
                                                            metadataHint: "年度已修改，请确认当前年度和材料类型。",
                                                        });
                                                        persistRememberedSelection({ year: e.target.value });
                                                    }}
                                                    className="w-16 px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                    placeholder="年份"
                                                />
                                                <label className="text-xs text-gray-500">类型:</label>
                                                <select
                                                    value={fileItem.docType}
                                                    onChange={(e) => {
                                                        const docType = e.target.value as "dept_final" | "dept_budget";
                                                        updateFile(fileItem.id, {
                                                            docType,
                                                            metadataNeedsConfirmation: true,
                                                            metadataConfirmed: false,
                                                            metadataHint: "材料类型已修改，请确认当前年度和材料类型。",
                                                        });
                                                        persistRememberedSelection({ docType });
                                                    }}
                                                    className="px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                >
                                                    <option value="dept_final">决算</option>
                                                    <option value="dept_budget">预算</option>
                                                </select>
                                                <label className="text-xs text-gray-500">部门:</label>
                                                <select
                                                    value={resolveDepartmentId(fileItem)}
                                                    disabled={isScopedUpload}
                                                    onChange={(e) => handleDepartmentChange(fileItem.id, e.target.value)}
                                                    className="w-36 px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                                >
                                                    <option value="">请选择部门</option>
                                                    {visibleDepartments.map((department) => (
                                                        <option key={department.id} value={department.id}>
                                                            {department.name}
                                                        </option>
                                                    ))}
                                                </select>
                                                <label className="text-xs text-gray-500">单位:</label>
                                                <select
                                                    value={resolveUnitId(fileItem)}
                                                    disabled={isScopedUpload || !resolveDepartmentId(fileItem)}
                                                    onChange={(e) =>
                                                        handleUnitChange(
                                                            fileItem.id,
                                                            resolveDepartmentId(fileItem),
                                                            e.target.value
                                                        )
                                                    }
                                                    className="w-40 px-1.5 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 disabled:bg-gray-100 dark:disabled:bg-gray-900/50"
                                                >
                                                    <option value="">
                                                        {resolveDepartmentId(fileItem) ? "不选单位则按部门上传" : "请先选择部门"}
                                                    </option>
                                                    {getVisibleUnits(resolveDepartmentId(fileItem), resolveUnitId(fileItem)).map((unit) => (
                                                        <option key={unit.id} value={unit.id}>
                                                            {formatUnitOption(unit, departmentMap.get(resolveDepartmentId(fileItem)))}
                                                        </option>
                                                    ))}
                                                </select>
                                                {!isScopedUpload && resolveDepartmentId(fileItem) ? (
                                                    <span className="text-[11px] text-slate-500">
                                                        留空单位时将按部门级上传
                                                    </span>
                                                ) : null}
                                                <button
                                                    onClick={() => setEditingId(null)}
                                                    className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                                                >
                                                    完成
                                                </button>
                                            </div>
                                        ) : (
                                            /* Display Mode */
                                            <div className="flex flex-wrap items-center gap-2 mt-0.5">
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
                                                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] ${resolveUnitId(fileItem)
                                                    ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                                                    : resolveDepartmentId(fileItem)
                                                        ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                                                        : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"}`}>
                                                    {resolveUnitId(fileItem)
                                                        ? `${resolveDepartmentName(fileItem) ? `${resolveDepartmentName(fileItem)} / ` : ""}${fileItem.orgName || orgMap.get(resolveUnitId(fileItem))?.name || "当前单位"}`
                                                        : resolveDepartmentId(fileItem)
                                                            ? `${resolveDepartmentName(fileItem) || "当前部门"} / 部门级上传`
                                                            : isScopedUpload
                                                                ? "当前组织"
                                                                : "未选择部门和单位"}
                                                </span>
                                                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] ${getMatchBadgeClass(fileItem)}`}>
                                                    {getMatchBadgeLabel(fileItem)}
                                                </span>
                                                {fileItem.matchHint && (
                                                    <span className="text-[11px] text-gray-500 dark:text-gray-400 truncate max-w-[260px]" title={fileItem.matchHint}>
                                                        {fileItem.matchHint}
                                                    </span>
                                                )}
                                                {fileItem.metadataHint && (
                                                    <span className={`text-[11px] ${needsMetadataConfirmation(fileItem) ? "text-orange-700" : "text-gray-500 dark:text-gray-400"}`}>
                                                        {fileItem.metadataHint}
                                                    </span>
                                                )}
                                                {fileItem.message && (
                                                    <span className="text-xs text-gray-500 dark:text-gray-400 truncate">
                                                        {fileItem.message}
                                                    </span>
                                                )}
                                                {needsMetadataConfirmation(fileItem) ? (
                                                    <button
                                                        type="button"
                                                        data-testid={`batch-confirm-metadata-${fileItem.id}`}
                                                        onClick={() => confirmFileMetadata(fileItem.id)}
                                                        className="rounded-md border border-orange-300 bg-orange-50 px-2 py-1 text-[11px] font-medium text-orange-800 hover:bg-orange-100"
                                                    >
                                                        确认年度和类型
                                                    </button>
                                                ) : null}
                                                {needsOrganizationConfirmation(fileItem) && resolveUploadTargetId(fileItem) ? (
                                                    <button
                                                        type="button"
                                                        data-testid={`batch-confirm-organization-${fileItem.id}`}
                                                        onClick={() => confirmFileOrganization(fileItem.id)}
                                                        className="rounded-md border border-orange-300 bg-orange-50 px-2 py-1 text-[11px] font-medium text-orange-800 hover:bg-orange-100"
                                                    >
                                                        确认材料归属
                                                    </button>
                                                ) : null}
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
                    <div className="flex items-center gap-2">
                        <button
                            onClick={onClose}
                            disabled={isProcessing}
                            className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {progress.completed > 0 && !isProcessing ? "关闭" : "取消"}
                        </button>
                        {!isProcessing && stats.failed > 0 ? (
                            <button
                                data-testid="batch-retry-failed"
                                type="button"
                                onClick={retryFailedFiles}
                                className="px-4 py-2 text-sm font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 rounded-xl transition-colors"
                            >
                                仅重试失败项 ({stats.failed})
                            </button>
                        ) : null}
                    </div>

                    {!isProcessing ? (
                        <button
                            data-testid="batch-start"
                            onClick={startProcessing}
                            disabled={
                                files.filter((f) => f.status === "pending").length === 0 ||
                                hasPendingPreflight ||
                                pendingConfirmationCount > 0
                            }
                            title={
                                hasPendingPreflight
                                    ? "正在识别PDF首页，请稍候"
                                    : pendingConfirmationCount > 0
                                        ? `有 ${pendingConfirmationCount} 份材料尚未确认年度、类型或归属信息`
                                        : ""
                            }
                            className="px-6 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 rounded-xl shadow-lg shadow-indigo-500/30 hover:shadow-xl hover:shadow-indigo-500/40 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none flex items-center gap-2"
                        >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            {isScopedUpload ? "开始上传" : "开始批量上传"} ({files.filter((f) => f.status === "pending").length})
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
