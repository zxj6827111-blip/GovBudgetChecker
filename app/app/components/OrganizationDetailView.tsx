"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { format } from "date-fns";
import { zhCN } from "date-fns/locale";

interface UnitItem {
  id: string;
  name: string;
  level: "department" | "unit" | string;
  parent_id: string | null;
}

interface TopIssueRule {
  rule_id: string;
  count: number;
}

interface JobSummary {
  job_id: string;
  filename: string;
  status:
    | "queued"
    | "processing"
    | "running"
    | "started"
    | "done"
    | "completed"
    | "error"
    | "failed"
    | "unknown";
  progress: number;
  ts: number;
  mode?: string;
  dual_mode_enabled?: boolean;
  stage?: string;
  report_year?: number | null;
  report_kind?: "budget" | "final" | "unknown";
  doc_type?: string | null;
  issue_total?: number;
  issue_error?: number;
  issue_warn?: number;
  issue_info?: number;
  has_issues?: boolean;
  top_issue_rules?: TopIssueRule[];
  local_participated?: boolean;
  ai_participated?: boolean;
  local_issue_total?: number;
  local_issue_error?: number;
  local_issue_warn?: number;
  local_issue_info?: number;
  ai_issue_total?: number;
  ai_issue_error?: number;
  ai_issue_warn?: number;
  ai_issue_info?: number;
  organization_id?: string | null;
  organization_name?: string | null;
  organization_match_type?: string | null;
  organization_match_confidence?: number | null;
  structured_ingest_status?: string | null;
  structured_document_version_id?: number | null;
  structured_tables_count?: number | null;
  structured_recognized_tables?: number | null;
  structured_facts_count?: number | null;
  structured_document_profile?: string | null;
  review_item_count?: number;
  low_confidence_item_count?: number;
  structured_report_id?: string | null;
  structured_table_data_count?: number | null;
  structured_line_item_count?: number | null;
  structured_sync_match_mode?: string | null;
}

interface OrgCardStats {
  job_count: number;
  issue_total: number;
  has_issues: boolean;
}

interface OrganizationDetailViewProps {
  departmentId: string;
  departmentName: string;
  selectedUnitId?: string | null;
  onSelectUnit: (unit: UnitItem | null) => void;
  onSelectJob: (jobId: string) => void;
  onUpload: () => void;
  refreshKey?: number;
  onJobDeleted?: () => void;
  onUnitCreated?: () => void;
  onUnitDeleted?: () => void;
}

const ORG_JOBS_PAGE_SIZE = 50;
const ORG_JOBS_CACHE_TTL_MS = 15000;
const TABLE_VIRTUAL_ROW_HEIGHT = 112;
const TABLE_VIRTUAL_OVERSCAN = 6;
const TABLE_VIRTUAL_THRESHOLD = 120;

export default function OrganizationDetailView({
  departmentId,
  departmentName,
  selectedUnitId,
  onSelectUnit,
  onSelectJob,
  onUpload,
  refreshKey,
  onJobDeleted,
  onUnitCreated,
  onUnitDeleted,
}: OrganizationDetailViewProps) {
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsLoadingMore, setJobsLoadingMore] = useState(false);
  const [jobsTotal, setJobsTotal] = useState(0);
  const jobsCacheRef = useRef<
    Map<string, { jobs: JobSummary[]; total: number; cachedAt: number }>
  >(new Map());
  const [tableScrollTop, setTableScrollTop] = useState(0);
  const [tableViewportHeight, setTableViewportHeight] = useState(360);
  const processingPollDelayRef = useRef(1500);
  const processingPollSignatureRef = useRef("");
  const jobsScrollRef = useRef<HTMLDivElement | null>(null);
  const loadMoreSentinelRef = useRef<HTMLDivElement | null>(null);
  const [orgStatsMap, setOrgStatsMap] = useState<Record<string, OrgCardStats>>({});
  const [statsLoading, setStatsLoading] = useState(false);
  const [orgViewTab, setOrgViewTab] = useState<"department" | "units">("department");
  const [selectedYearFilter, setSelectedYearFilter] = useState<string>("all");
  const [selectedKindFilter, setSelectedKindFilter] = useState<"all" | "budget" | "final">("all");
  const [yearFilterTouched, setYearFilterTouched] = useState(false);
  const [showCreateUnitModal, setShowCreateUnitModal] = useState(false);
  const [newUnitName, setNewUnitName] = useState("");
  const [isCreatingUnit, setIsCreatingUnit] = useState(false);
  const [deletingUnitId, setDeletingUnitId] = useState<string | null>(null);
  const lastRefreshKeyRef = useRef<number | undefined>(refreshKey);

  const buildJobsApiPath = useCallback(
    (orgId: string, includeChildren = false) =>
      `/api/organizations/${orgId}/jobs?include_children=${includeChildren ? "true" : "false"}`,
    []
  );

  const departmentOrg = useMemo<UnitItem>(
    () => ({
      id: departmentId,
      name: departmentName,
      level: "department",
      parent_id: null,
    }),
    [departmentId, departmentName]
  );

  const selectableOrganizations = useMemo(() => [departmentOrg, ...units], [departmentOrg, units]);

  const selectedUnit = useMemo<UnitItem | null>(
    () => selectableOrganizations.find((item) => item.id === selectedUnitId) || null,
    [selectableOrganizations, selectedUnitId]
  );

  const switchToDepartmentTab = useCallback(() => {
    setOrgViewTab("department");
    onSelectUnit(departmentOrg);
  }, [departmentOrg, onSelectUnit]);

  const switchToUnitsTab = useCallback(() => {
    setOrgViewTab("units");
    if (!selectedUnit || selectedUnit.level !== "unit") {
      onSelectUnit(null);
      setJobs([]);
    }
  }, [onSelectUnit, selectedUnit]);

  const openCreateUnitModal = useCallback(() => {
    setNewUnitName("");
    setShowCreateUnitModal(true);
  }, []);

  const closeCreateUnitModal = useCallback(() => {
    if (isCreatingUnit) return;
    setShowCreateUnitModal(false);
    setNewUnitName("");
  }, [isCreatingUnit]);

  const handleCreateUnit = useCallback(async () => {
    const normalizedName = newUnitName.trim();
    if (!normalizedName) return;

    setIsCreatingUnit(true);
    try {
      const res = await fetch(`/api/departments/${departmentId}/units`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: normalizedName }),
      });

      let payload: any = {};
      try {
        payload = await res.json();
      } catch {
        payload = {};
      }

      if (!res.ok) {
        throw new Error(
          payload?.detail || payload?.error || payload?.message || "创建下属单位失败"
        );
      }

      const createdId = String(payload.id || "").trim();
      if (!createdId) {
        throw new Error("创建单位成功，但返回结果缺少 ID");
      }

      const createdUnit: UnitItem = {
        id: createdId,
        name: String(payload.name || normalizedName),
        level: "unit",
        parent_id: payload.parent_id ?? departmentId,
      };

      setShowCreateUnitModal(false);
      setNewUnitName("");
      try {
        const unitsRes = await fetch(
          `/api/departments/${departmentId}/units`,
          { cache: "no-store" }
        );
        if (unitsRes.ok) {
          const unitsPayload = await unitsRes.json();
          const refreshedUnits: UnitItem[] = Array.isArray(unitsPayload.units)
            ? unitsPayload.units
                .filter((item: any) => item && item.level === "unit")
                .map((item: any) => ({
                  id: String(item.id),
                  name: String(item.name || ""),
                  level: "unit",
                  parent_id: item.parent_id ?? null,
                }))
            : [];
          setUnits(refreshedUnits);
        } else {
          setUnits((prev) => {
            if (prev.some((item) => item.id === createdUnit.id)) return prev;
            return [...prev, createdUnit];
          });
        }
      } catch {
        setUnits((prev) => {
          if (prev.some((item) => item.id === createdUnit.id)) return prev;
          return [...prev, createdUnit];
        });
      }
      setOrgViewTab("units");
      onSelectUnit(createdUnit);
      setOrgStatsMap((prev) => ({
        ...prev,
        [createdUnit.id]: { job_count: 0, issue_total: 0, has_issues: false },
      }));
      onUnitCreated?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "创建下属单位失败";
      alert(msg);
    } finally {
      setIsCreatingUnit(false);
    }
  }, [departmentId, newUnitName, onSelectUnit, onUnitCreated]);

  const fetchUnits = useCallback(async () => {
    setUnitsLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const toUnitItems = (items: any[]): UnitItem[] =>
        items
          .filter((item) => item && item.level === "unit")
          .map((item) => ({
            id: String(item.id),
            name: String(item.name || ""),
            level: "unit",
            parent_id: item.parent_id ?? null,
          }));

      const findUnitsInTree = (tree: any[]): UnitItem[] => {
        let target: any = null;
        const walk = (nodes: any[]) => {
          for (const node of nodes) {
            if (!node) continue;
            if (node.level === "department" && (node.id === departmentId || node.name === departmentName)) {
              target = node;
              return;
            }
            if (Array.isArray(node.children) && node.children.length > 0) {
              walk(node.children);
              if (target) return;
            }
          }
        };
        walk(Array.isArray(tree) ? tree : []);
        return target && Array.isArray(target.children) ? toUnitItems(target.children) : [];
      };

      const res = await fetch(`/api/departments/${departmentId}/units`, {
        signal: controller.signal,
        cache: "no-store",
      });
      if (res.ok) {
        const data = await res.json();
        setUnits(toUnitItems(Array.isArray(data.units) ? data.units : []));
        return;
      }

      const fallbackRes = await fetch(`/api/organizations`, {
        signal: controller.signal,
        cache: "no-store",
      });
      if (!fallbackRes.ok) {
        throw new Error("units api and fallback tree api both failed");
      }
      const fallbackData = await fallbackRes.json();
      const unitsFromTree = findUnitsInTree(Array.isArray(fallbackData.tree) ? fallbackData.tree : []);
      setUnits(unitsFromTree);
    } catch (e) {
      console.error("Failed to fetch department units", e);
      setUnits([]);
      onSelectUnit(departmentOrg);
    } finally {
      clearTimeout(timeoutId);
      setUnitsLoading(false);
    }
  }, [departmentId, departmentName, departmentOrg, onSelectUnit]);

  const fetchJobsForUnit = useCallback(
    async (
      unitId: string,
      options?: {
        offset?: number;
        limit?: number;
        append?: boolean;
        forceRefresh?: boolean;
        includeChildren?: boolean;
      }
    ) => {
      const append = options?.append ?? false;
      const offset = options?.offset ?? 0;
      const limit = options?.limit ?? ORG_JOBS_PAGE_SIZE;
      const forceRefresh = options?.forceRefresh ?? false;
      const includeChildren = options?.includeChildren ?? false;
      const canUseCache = !append && offset === 0 && !forceRefresh;
      const cacheKey = `${unitId}:${includeChildren ? "subtree" : "self"}:${limit}:${offset}`;
      const cached = canUseCache ? jobsCacheRef.current.get(cacheKey) : undefined;
      const now = Date.now();
      const hasFreshCache =
        !!cached && now - cached.cachedAt <= ORG_JOBS_CACHE_TTL_MS;

      if (hasFreshCache && cached) {
        setJobs(cached.jobs);
        setJobsTotal(cached.total);
      }

      if (append) {
        setJobsLoadingMore(true);
      } else if (!hasFreshCache) {
        setJobsLoading(true);
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      try {
        const res = await fetch(
          `${buildJobsApiPath(unitId, includeChildren)}&limit=${limit}&offset=${offset}`,
          {
          signal: controller.signal,
          cache: "no-store",
          }
        );
        if (res.ok) {
          const data = await res.json();
          const nextJobs = Array.isArray(data.jobs) ? data.jobs : [];
          const total = Number(data.total ?? nextJobs.length);
          const normalizedTotal = Number.isFinite(total) ? total : nextJobs.length;
          setJobsTotal(normalizedTotal);

          if (append) {
            setJobs((prev) => {
              const seen = new Set(prev.map((item) => item.job_id));
              const merged = [...prev];
              for (const item of nextJobs) {
                if (!item || typeof item.job_id !== "string") continue;
                if (seen.has(item.job_id)) continue;
                merged.push(item);
                seen.add(item.job_id);
              }
              return merged;
            });
          } else {
            setJobs(nextJobs);
            jobsCacheRef.current.set(cacheKey, {
              jobs: nextJobs,
              total: normalizedTotal,
              cachedAt: Date.now(),
            });
          }
        } else {
          if (!append) {
            setJobs([]);
            setJobsTotal(0);
            jobsCacheRef.current.delete(cacheKey);
          }
        }
      } catch (e) {
        console.error("Failed to fetch jobs", e);
        if (!append) {
          if (!hasFreshCache) {
            setJobs([]);
            setJobsTotal(0);
          }
        }
      } finally {
        clearTimeout(timeoutId);
        if (append) {
          setJobsLoadingMore(false);
        } else if (!hasFreshCache) {
          setJobsLoading(false);
        }
      }
    },
    [buildJobsApiPath]
  );

  const fetchOrgStats = useCallback(async () => {
    setStatsLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const res = await fetch(`/api/departments/${departmentId}/stats`, {
        signal: controller.signal,
        cache: "no-store",
      });
      if (!res.ok) {
        setOrgStatsMap({});
        return;
      }
      const payload = await res.json();
      const rawStats = payload?.stats;
      if (rawStats && typeof rawStats === "object") {
        setOrgStatsMap(rawStats as Record<string, OrgCardStats>);
      } else {
        setOrgStatsMap({});
      }
    } catch (e) {
      console.error("Failed to fetch organization stats", e);
      setOrgStatsMap({});
    } finally {
      clearTimeout(timeoutId);
      setStatsLoading(false);
    }
  }, [departmentId]);

  useEffect(() => {
    onSelectUnit(departmentOrg);
    setJobs([]);
    setJobsTotal(0);
    jobsCacheRef.current.clear();
    setOrgViewTab("department");
    setOrgStatsMap({});
    fetchUnits();
    fetchOrgStats();
  }, [departmentId, departmentOrg, fetchOrgStats, fetchUnits, onSelectUnit]);

  useEffect(() => {
    if (lastRefreshKeyRef.current === refreshKey) {
      return;
    }

    lastRefreshKeyRef.current = refreshKey;
    jobsCacheRef.current.clear();
    fetchUnits();
    fetchOrgStats();
  }, [fetchOrgStats, fetchUnits, refreshKey]);

  useEffect(() => {
    if (!selectedUnitId) {
      setJobs([]);
      setJobsTotal(0);
      return;
    }
    setYearFilterTouched(false);
    setSelectedYearFilter("all");
    setSelectedKindFilter("all");
    fetchJobsForUnit(selectedUnitId, {
      offset: 0,
      limit: ORG_JOBS_PAGE_SIZE,
      append: false,
      includeChildren: false,
    });
  }, [fetchJobsForUnit, refreshKey, selectedUnitId]);

  const availableYears = useMemo(() => {
    const years = new Set<number>();
    jobs.forEach((job) => {
      if (typeof job.report_year === "number" && job.report_year >= 2000 && job.report_year <= 2099) {
        years.add(job.report_year);
      }
    });
    return Array.from(years).sort((a, b) => b - a);
  }, [jobs]);

  useEffect(() => {
    if (yearFilterTouched || availableYears.length === 0) return;
    const latest = String(availableYears[0]);
    if (selectedYearFilter !== latest) setSelectedYearFilter(latest);
  }, [availableYears, selectedYearFilter, yearFilterTouched]);

  useEffect(() => {
    if (selectedYearFilter === "all") return;
    const target = Number(selectedYearFilter);
    if (!availableYears.includes(target)) {
      setSelectedYearFilter(availableYears.length > 0 ? String(availableYears[0]) : "all");
    }
  }, [availableYears, selectedYearFilter]);

  const filteredJobs = useMemo(() => {
    let scoped = jobs;
    if (selectedYearFilter !== "all") {
      const target = Number(selectedYearFilter);
      if (!Number.isNaN(target)) scoped = scoped.filter((job) => job.report_year === target);
    }
    if (selectedKindFilter !== "all") {
      scoped = scoped.filter((job) => job.report_kind === selectedKindFilter);
    }
    return scoped;
  }, [jobs, selectedKindFilter, selectedYearFilter]);

  useEffect(() => {
    const root = jobsScrollRef.current;
    if (!root) return;

    const syncMetrics = () => {
      setTableScrollTop(root.scrollTop);
      setTableViewportHeight(root.clientHeight || 360);
    };

    syncMetrics();
    root.addEventListener("scroll", syncMetrics, { passive: true });

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(syncMetrics);
      resizeObserver.observe(root);
    }

    return () => {
      root.removeEventListener("scroll", syncMetrics);
      if (resizeObserver) resizeObserver.disconnect();
    };
  }, [selectedUnitId]);

  useEffect(() => {
    const root = jobsScrollRef.current;
    if (root) {
      root.scrollTop = 0;
    }
    setTableScrollTop(0);
  }, [selectedUnitId, selectedKindFilter, selectedYearFilter]);

  const virtualWindow = useMemo(() => {
    if (filteredJobs.length < TABLE_VIRTUAL_THRESHOLD) {
      return {
        enabled: false,
        items: filteredJobs,
        topSpacerHeight: 0,
        bottomSpacerHeight: 0,
      };
    }

    const visibleCount = Math.max(
      1,
      Math.ceil(tableViewportHeight / TABLE_VIRTUAL_ROW_HEIGHT)
    );
    const startIndex = Math.max(
      0,
      Math.floor(tableScrollTop / TABLE_VIRTUAL_ROW_HEIGHT) - TABLE_VIRTUAL_OVERSCAN
    );
    const endIndex = Math.min(
      filteredJobs.length,
      startIndex + visibleCount + TABLE_VIRTUAL_OVERSCAN * 2
    );

    return {
      enabled: true,
      items: filteredJobs.slice(startIndex, endIndex),
      topSpacerHeight: startIndex * TABLE_VIRTUAL_ROW_HEIGHT,
      bottomSpacerHeight: Math.max(
        0,
        (filteredJobs.length - endIndex) * TABLE_VIRTUAL_ROW_HEIGHT
      ),
    };
  }, [filteredJobs, tableScrollTop, tableViewportHeight]);

  const filteredIssueTotal = useMemo(
    () => filteredJobs.reduce((sum, job) => sum + (job.issue_total || 0), 0),
    [filteredJobs]
  );

  const kindCounts = useMemo(() => {
    const counts = { budget: 0, final: 0 };
    jobs.forEach((job) => {
      if (job.report_kind === "budget") counts.budget += 1;
      if (job.report_kind === "final") counts.final += 1;
    });
    return counts;
  }, [jobs]);

  const hasMoreJobs = jobs.length < jobsTotal;
  const loadMoreJobs = useCallback(() => {
    if (!selectedUnitId || jobsLoading || jobsLoadingMore || !hasMoreJobs) {
      return;
    }
    fetchJobsForUnit(selectedUnitId, {
      offset: jobs.length,
      limit: ORG_JOBS_PAGE_SIZE,
      append: true,
      includeChildren: false,
    });
  }, [
    fetchJobsForUnit,
    hasMoreJobs,
    jobs.length,
    jobsLoading,
    jobsLoadingMore,
    selectedUnitId,
  ]);

  useEffect(() => {
    const root = jobsScrollRef.current;
    const target = loadMoreSentinelRef.current;
    if (!root || !target) return;
    if (!selectedUnitId || !hasMoreJobs) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const hit = entries.some((entry) => entry.isIntersecting);
        if (hit) {
          loadMoreJobs();
        }
      },
      {
        root,
        rootMargin: "80px",
        threshold: 0.1,
      }
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMoreJobs, jobs.length, loadMoreJobs, selectedUnitId]);

  useEffect(() => {
    if (!selectedUnitId) return;
    const processingJobs = jobs.filter(
      (j) => j.status === "processing" || j.status === "queued" || j.status === "running"
    );
    if (processingJobs.length === 0) {
      processingPollSignatureRef.current = "";
      processingPollDelayRef.current = 1500;
      return;
    }

    const signature = processingJobs
      .map((item) => `${item.job_id}:${item.status}:${item.progress ?? -1}`)
      .join("|");
    if (signature === processingPollSignatureRef.current) {
      processingPollDelayRef.current = Math.min(
        8000,
        Math.round(processingPollDelayRef.current * 1.5)
      );
    } else {
      processingPollDelayRef.current = 1500;
      processingPollSignatureRef.current = signature;
    }

    const refreshLimit = Math.max(ORG_JOBS_PAGE_SIZE, jobs.length);
    const timer = setTimeout(() => {
      fetchJobsForUnit(selectedUnitId, {
        offset: 0,
        limit: refreshLimit,
        append: false,
        forceRefresh: true,
        includeChildren: false,
      });
    }, processingPollDelayRef.current);
    return () => clearTimeout(timer);
  }, [fetchJobsForUnit, jobs, selectedUnitId]);

  useEffect(() => {
    if (!selectedUnitId) return;
    const loadedIssueTotal = jobs.reduce((sum, job) => sum + (job.issue_total || 0), 0);
    setOrgStatsMap((prev) => ({
      ...prev,
      [selectedUnitId]: {
        job_count: jobsTotal || jobs.length,
        issue_total:
          jobsTotal > 0 && jobs.length < jobsTotal
            ? (prev[selectedUnitId]?.issue_total ?? loadedIssueTotal)
            : loadedIssueTotal,
        has_issues:
          jobsTotal > 0 && jobs.length < jobsTotal
            ? (prev[selectedUnitId]?.issue_total ?? loadedIssueTotal) > 0
            : loadedIssueTotal > 0,
      },
    }));
  }, [jobs, jobsTotal, selectedUnitId]);

  const normalizeJobStatus = useCallback((rawStatus?: string) => {
    switch (String(rawStatus || "").trim().toLowerCase()) {
      case "started":
      case "queued":
        return "queued";
      case "processing":
      case "running":
        return "processing";
      case "done":
      case "completed":
      case "success":
        return "done";
      case "error":
      case "failed":
        return "error";
      default:
        return "unknown";
    }
  }, []);

  const getStatusBadge = (job: JobSummary) => {
    const normalizedStatus = normalizeJobStatus(job.status);
    if (normalizedStatus === "done") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-green-100 text-green-700">已完成</span>;
    if (normalizedStatus === "processing") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-blue-100 text-blue-700 animate-pulse">处理中</span>;
    if (normalizedStatus === "queued") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">排队中</span>;
    if (normalizedStatus === "error") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-700">异常</span>;
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">未知</span>;
  };
  const handleDelete = async (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个任务吗？此操作不可恢复。")) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      if (res.ok) {
        setJobs((prev) => prev.filter((item) => item.job_id !== jobId));
        setJobsTotal((prev) => Math.max(0, prev - 1));
        onJobDeleted?.();
      } else {
        alert("删除失败");
      }
    } catch (err) {
      console.error("Delete failed", err);
      alert("删除失败");
    }
  };

  const handleDeleteUnit = useCallback(
    async (unit: UnitItem, e: React.MouseEvent) => {
      e.stopPropagation();
      if (unit.level !== "unit") return;

      const confirmed = confirm(`确定要删除下属单位“${unit.name}”吗？`);
      if (!confirmed) return;

      setDeletingUnitId(unit.id);
      try {
        const res = await fetch(`/api/organizations/${unit.id}`, {
          method: "DELETE",
        });

        let payload: any = {};
        try {
          payload = await res.json();
        } catch {
          payload = {};
        }

        if (!res.ok) {
          throw new Error(
            payload?.detail || payload?.error || payload?.message || "删除下属单位失败"
          );
        }

        setUnits((prev) => prev.filter((item) => item.id !== unit.id));
        setOrgStatsMap((prev) => {
          const next = { ...prev };
          delete next[unit.id];
          return next;
        });

        if (selectedUnitId === unit.id) {
          onSelectUnit(null);
          setJobs([]);
          setJobsTotal(0);
        }

        onUnitDeleted?.();
      } catch (err) {
        const message = err instanceof Error ? err.message : "删除下属单位失败";
        alert(message);
      } finally {
        setDeletingUnitId(null);
      }
    },
    [onSelectUnit, onUnitDeleted, selectedUnitId]
  );

  const renderOrganizationCard = (org: UnitItem) => {
    const active = selectedUnitId === org.id;
    const liveStats = active
      ? {
          job_count: jobs.length,
          issue_total: jobs.reduce((sum, item) => sum + (item.issue_total || 0), 0),
          has_issues: jobs.some((item) => (item.issue_total || 0) > 0),
        }
      : undefined;
    const stats = liveStats || orgStatsMap[org.id];
    const hasKnownStats = !!stats;
    const jobCount = stats?.job_count ?? 0;
    const hasJobs = hasKnownStats ? jobCount > 0 : true;
    const isDepartment = org.level === "department";
    const isUnit = org.level === "unit";
    const orgDisplayName = !isDepartment && org.name === departmentName ? `${org.name} (Local Unit)` : org.name;
    const orgIssueTotal = stats?.issue_total ?? 0;
    const hasProblems = orgIssueTotal > 0;
    const isDeletingThisUnit = deletingUnitId === org.id;
    const buttonLabel = !hasKnownStats
      ? "\u67e5\u770b"
      : hasJobs
        ? (hasProblems ? "\u67e5\u770b\u95ee\u9898" : "\u67e5\u770b\u62a5\u544a")
        : "\u5f85\u4e0a\u4f20";

    return (
      <div key={org.id} className={`relative flex flex-col bg-white rounded-2xl border transition-all duration-300 shadow-sm hover:shadow-md ${active ? "border-indigo-500 shadow-indigo-100" : "border-gray-200 hover:border-indigo-300"}`}>
        <div className="p-5 flex-1">
          <div className="flex items-start justify-between gap-2 mb-3">
            <h3 className="font-bold text-gray-900 text-lg tracking-tight leading-snug" title={orgDisplayName}>{orgDisplayName}</h3>
            <div className="flex items-center gap-1.5">
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${isDepartment ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-700"}`}>{isDepartment ? "部门" : "单位"}</span>
              {isUnit && (
                <button
                  type="button"
                  onClick={(e) => handleDeleteUnit(org, e)}
                  disabled={isDeletingThisUnit}
                  className="inline-flex items-center justify-center h-7 w-7 rounded-md text-red-500 hover:text-red-600 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  title={isDeletingThisUnit ? "删除中..." : "删除下属单位"}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center text-sm">
            <span className="text-gray-500 font-medium w-24">文件数量:</span>
            <span className="text-gray-700 font-mono text-sm font-semibold">
              {hasKnownStats ? jobCount : "-"}
            </span>
          </div>
          <div className="flex items-center text-sm mt-2">
            <span className="text-gray-500 font-medium w-24">问题数量:</span>
            <span className={`font-mono text-sm font-semibold ${!hasKnownStats ? "text-gray-400" : orgIssueTotal > 0 ? "text-red-600" : "text-green-600"}`}>
              {hasKnownStats ? orgIssueTotal : "-"}
            </span>
          </div>
        </div>
        <div className="px-5 pb-5 pt-2">
          <button
            onClick={() => {
              setOrgViewTab(org.level === "department" ? "department" : "units");
              onSelectUnit(org);
            }}
            className={`w-full py-2.5 rounded-xl font-medium text-sm transition-all duration-300 flex items-center justify-center shadow-sm ${hasJobs ? "bg-indigo-600 hover:bg-indigo-700 text-white shadow-indigo-200" : "bg-amber-500 hover:bg-amber-600 text-white shadow-amber-200"}`}
          >
            {buttonLabel}
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full bg-transparent overflow-hidden">
      <div className="flex-none p-8 pb-4">
        <div className="rounded-2xl bg-white/70 backdrop-blur-xl border border-white/20 shadow-xl p-8">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 tracking-tight">{departmentName}</h1>
              <div className="mt-2 text-sm text-gray-500">
                {selectedUnit ? selectedUnit.name : "请选择部门或单位"}
              </div>
            </div>
            <button
              onClick={onUpload}
              disabled={!selectedUnit}
              className="inline-flex items-center justify-center px-5 py-3 text-sm font-medium text-white bg-indigo-600 rounded-xl shadow-lg hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              title={selectedUnit ? "上传到当前组织" : "请先选择部门或单位"}
            >
              上传报告
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-8 pb-8">
        <div className="space-y-6">
          <div>
            <div className="flex items-center justify-between gap-4 mb-4">
              <div className="text-sm font-semibold text-gray-700 bg-white/60 inline-flex px-4 py-2 rounded-lg border border-white/40 shadow-sm backdrop-blur-md">{departmentName} - 组织列表</div>
              <div className="flex items-center gap-2">
                {statsLoading && (
                  <div className="text-xs text-gray-400">同步组织统计中...</div>
                )}
                <div className="inline-flex bg-white/70 rounded-xl border border-white/30 p-1 shadow-sm">
                  <button type="button" onClick={switchToDepartmentTab} className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${orgViewTab === "department" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>部门本级 (1)</button>
                  <button type="button" onClick={switchToUnitsTab} className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${orgViewTab === "units" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>下属单位 ({units.length})</button>
                </div>
                <button
                  type="button"
                  onClick={openCreateUnitModal}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg border border-indigo-200 text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors"
                  title={`在 ${departmentName} 下新建单位`}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  新建下属单位
                </button>
              </div>
            </div>

            {orgViewTab === "department" ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">{renderOrganizationCard(departmentOrg)}</div>
            ) : unitsLoading ? (
              <div className="text-center py-12 text-gray-400">加载单位中...</div>
            ) : units.length === 0 ? (
              <div className="text-center py-12 text-gray-400">
                <div>该部门下暂无单位数据</div>
                <button
                  type="button"
                  onClick={openCreateUnitModal}
                  className="mt-3 inline-flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  新建第一个下属单位
                </button>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">{units.map((unit) => renderOrganizationCard(unit))}</div>
            )}
          </div>

          <div className="bg-white/40 backdrop-blur-md rounded-2xl border border-white/20 shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 text-xs text-gray-500">第三步：查看所选组织任务列表</div>

            {selectedUnit && !jobsLoading && jobs.length > 0 && (
              <div className="px-4 py-3 border-b border-gray-100 bg-white/30">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="inline-flex bg-white rounded-lg border border-gray-200 p-1">
                    <button
                      type="button"
                      onClick={() => setSelectedKindFilter("all")}
                      className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "all" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}
                    >
                      全部类型
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedKindFilter("budget")}
                      className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "budget" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}
                    >
                      预算 ({kindCounts.budget})
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedKindFilter("final")}
                      className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "final" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}
                    >
                      决算 ({kindCounts.final})
                    </button>
                  </div>

                  <div className="inline-flex bg-white rounded-lg border border-gray-200 p-1">
                    <button
                      type="button"
                      onClick={() => {
                        setYearFilterTouched(true);
                        setSelectedYearFilter("all");
                      }}
                      className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedYearFilter === "all" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}
                    >
                      全部年度
                    </button>
                    {availableYears.map((year) => (
                      <button
                        key={year}
                        type="button"
                        onClick={() => {
                          setYearFilterTouched(true);
                          setSelectedYearFilter(String(year));
                        }}
                        className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedYearFilter === String(year) ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}
                      >
                        {year}
                      </button>
                    ))}
                  </div>

                  <div className="text-xs text-gray-500">
                    当前{selectedKindFilter === "all" ? "全部类型" : selectedKindFilter === "budget" ? "预算" : "决算"}，当前{selectedYearFilter === "all" ? "全部年度" : `${selectedYearFilter}年度`}：{filteredJobs.length}个文件，问题{filteredIssueTotal}
                    <span className="ml-2 text-gray-400">已加载 {jobs.length}/{jobsTotal || jobs.length}</span>
                  </div>
                </div>
              </div>
            )}

            {!selectedUnit ? (
              <div className="text-center py-10 text-gray-400">请先选择部门或单位后查看任务</div>
            ) : jobsLoading ? (
              <div className="text-center py-10 text-gray-400">加载任务中...</div>
            ) : jobs.length === 0 ? (
              <div className="text-center py-10 text-gray-400">当前组织暂无文档任务</div>
            ) : filteredJobs.length === 0 ? (
              <div className="text-center py-10 text-gray-400">当前筛选条件下暂无任务</div>
            ) : (
              <div ref={jobsScrollRef} className="max-h-[360px] overflow-auto">
                <table className="min-w-full divide-y divide-gray-200/50">
                  <thead className="bg-gray-50/50">
                    <tr>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">文件名称</th>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">上传时间</th>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">状态</th>
                      <th className="px-6 py-4 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider w-32">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200/50 bg-transparent">
                    {virtualWindow.enabled && virtualWindow.topSpacerHeight > 0 && (
                      <tr aria-hidden="true" className="border-0">
                        <td colSpan={4} className="p-0" style={{ height: `${virtualWindow.topSpacerHeight}px` }} />
                      </tr>
                    )}
                    {virtualWindow.items.map((job) => {
                      const localParticipated = job.local_participated ?? true;
                      const aiParticipated = job.ai_participated ?? job.mode === "dual";
                      const localTotal = typeof job.local_issue_total === "number" ? job.local_issue_total : job.issue_total || 0;
                      const localError = typeof job.local_issue_error === "number" ? job.local_issue_error : job.issue_error || 0;
                      const localWarn = typeof job.local_issue_warn === "number" ? job.local_issue_warn : job.issue_warn || 0;
                      const localInfo = typeof job.local_issue_info === "number" ? job.local_issue_info : job.issue_info || 0;
                      const aiTotal = job.ai_issue_total || 0;
                      const aiError = job.ai_issue_error || 0;
                      const aiWarn = job.ai_issue_warn || 0;
                      const aiInfo = job.ai_issue_info || 0;
                      const localHasIssues = localParticipated && localTotal > 0;
                      const aiHasIssues = aiParticipated && aiTotal > 0;
                      const hasAnyIssues = localHasIssues || aiHasIssues;
                      const localBadgeClass = !localParticipated ? "bg-gray-100 text-gray-500" : localHasIssues ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700";
                      const aiBadgeClass = !aiParticipated ? "bg-gray-100 text-gray-500" : aiHasIssues ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700";
                      const structuredTablesCount = typeof job.structured_tables_count === "number" ? job.structured_tables_count : null;
                      const structuredRecognizedTables = typeof job.structured_recognized_tables === "number" ? job.structured_recognized_tables : null;
                      const structuredFactsCount = typeof job.structured_facts_count === "number" ? job.structured_facts_count : null;
                      const structuredLineItemCount = typeof job.structured_line_item_count === "number" ? job.structured_line_item_count : null;
                      const hasStructuredMetrics =
                        structuredRecognizedTables !== null ||
                        structuredFactsCount !== null ||
                        structuredLineItemCount !== null;

                      return (
                        <tr key={job.job_id} className="group cursor-pointer hover:bg-white/70 transition-colors duration-150" onClick={() => onSelectJob(job.job_id)}>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="text-sm font-medium text-gray-900 group-hover:text-indigo-600 transition-colors">{job.filename || "未命名文件"}</div>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                              <span className="text-gray-500 font-mono">ID: {job.job_id.slice(0, 8)}</span>
                              <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">{typeof job.report_year === "number" ? `${job.report_year}年度` : "年度未识别"}</span>
                              <span className={`inline-flex items-center px-2 py-0.5 rounded-full ${job.report_kind === "budget" ? "bg-emerald-50 text-emerald-700" : job.report_kind === "final" ? "bg-cyan-50 text-cyan-700" : "bg-gray-100 text-gray-600"}`}>{job.report_kind === "budget" ? "预算检查" : job.report_kind === "final" ? "决算检查" : "类型未识别"}</span>
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                              <span className={`inline-flex items-center px-2 py-0.5 rounded-full ${job.organization_name ? "bg-slate-100 text-slate-700" : "bg-amber-100 text-amber-700"}`}>
                                {job.organization_name ? `所属：${job.organization_name}` : "所属：未关联"}
                              </span>
                              {job.organization_match_type && (
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full ${job.organization_match_type === "manual" ? "bg-purple-100 text-purple-700" : "bg-blue-100 text-blue-700"}`}>
                                  {job.organization_match_type === "manual" ? "人工绑定" : "自动匹配"}
                                </span>
                              )}
                              {typeof job.organization_match_confidence === "number" && job.organization_match_confidence > 0 && (
                                <span className="text-blue-600">
                                  置信度 {(job.organization_match_confidence * 100).toFixed(0)}%
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 tabular-nums">{format(new Date(job.ts * 1000), "yyyy-MM-dd HH:mm", { locale: zhCN })}</td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="space-y-1">
                              {getStatusBadge(job)}
                              {job.structured_ingest_status && (
                                <div className="space-y-1">
                                  <div className="flex flex-wrap gap-1.5">
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${job.structured_ingest_status === "done" ? "bg-emerald-50 text-emerald-700" : job.structured_ingest_status === "error" ? "bg-red-50 text-red-700" : "bg-gray-100 text-gray-600"}`}>
                                      {job.structured_ingest_status === "done" ? "已结构化入库" : job.structured_ingest_status === "error" ? "入库失败" : "入库待处理"}
                                    </span>
                                    {(job.review_item_count || 0) > 0 && (
                                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-50 text-amber-700">
                                        待复核 {job.review_item_count} 项
                                      </span>
                                    )}
                                    {(job.low_confidence_item_count || 0) > 0 && (
                                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-orange-50 text-orange-700">
                                        低置信 {job.low_confidence_item_count} 张
                                      </span>
                                    )}
                                  </div>
                                  {hasStructuredMetrics && (
                                    <div className="text-[11px] text-gray-500">
                                      {structuredRecognizedTables !== null && (
                                        <span>
                                          识别 {structuredRecognizedTables}
                                          {structuredTablesCount !== null ? `/${structuredTablesCount}` : ""} 表
                                        </span>
                                      )}
                                      {structuredFactsCount !== null && (
                                        <span>
                                          {structuredRecognizedTables !== null ? " · " : ""}
                                          facts {structuredFactsCount}
                                        </span>
                                      )}
                                      {structuredLineItemCount !== null && (
                                        <span>
                                          {(structuredRecognizedTables !== null || structuredFactsCount !== null) ? " · " : ""}
                                          PS 行项 {structuredLineItemCount}
                                        </span>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )}
                              {normalizeJobStatus(job.status) === "done" && (
                                <div className="space-y-1">
                                  <div className="flex flex-wrap gap-1.5">
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${localBadgeClass}`}>{!localParticipated ? "本地：未参与" : localHasIssues ? `本地：${localTotal}个问题` : "本地：正常"}</span>
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${aiBadgeClass}`}>{!aiParticipated ? "AI：未参与" : aiHasIssues ? `AI：${aiTotal}个问题` : "AI：正常"}</span>
                                  </div>
                                  <div className={`text-xs font-medium ${hasAnyIssues ? "text-red-600" : "text-green-600"}`}>{hasAnyIssues ? "已发现问题" : "未发现问题"}</div>
                                  {localHasIssues && <div className="text-[11px] text-gray-500">本地明细：错{localError} / 警{localWarn} / 提{localInfo}</div>}
                                  {aiHasIssues && <div className="text-[11px] text-gray-500">AI明细：错{aiError} / 警{aiWarn} / 提{aiInfo}</div>}
                                  {localHasIssues && Array.isArray(job.top_issue_rules) && job.top_issue_rules.length > 0 && <div className="text-[11px] text-gray-500">主要类型：{job.top_issue_rules.map((item) => `${item.rule_id}(${item.count})`).join("、")}</div>}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                            <div className="flex items-center justify-end space-x-3">
                              <button onClick={(e) => handleDelete(job.job_id, e)} className="rounded px-2 py-1 text-red-500 transition-colors hover:bg-red-50 hover:text-red-700" title="删除任务">删除</button>
                              <button onClick={() => onSelectJob(job.job_id)} className="text-xs text-indigo-600 hover:text-indigo-900">查看</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {virtualWindow.enabled && virtualWindow.bottomSpacerHeight > 0 && (
                      <tr aria-hidden="true" className="border-0">
                        <td colSpan={4} className="p-0" style={{ height: `${virtualWindow.bottomSpacerHeight}px` }} />
                      </tr>
                    )}
                  </tbody>
                </table>
                {hasMoreJobs && (
                  <>
                    <div ref={loadMoreSentinelRef} className="h-1 w-full" />
                    <div className="sticky bottom-0 border-t border-gray-200/60 bg-white/80 p-3 text-center backdrop-blur-sm">
                      <button
                        type="button"
                        onClick={loadMoreJobs}
                        disabled={jobsLoading || jobsLoadingMore}
                        className="inline-flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-1.5 text-sm text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {jobsLoadingMore ? "加载中..." : `加载更多 (${jobs.length}/${jobsTotal})`}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      {showCreateUnitModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-2xl bg-white border border-gray-200 shadow-2xl p-6">
            <h3 className="text-lg font-semibold text-gray-900">新建下属单位</h3>
            <p className="mt-1 text-sm text-gray-500">
              上级部门: <span className="font-medium text-gray-700">{departmentName}</span>
            </p>

            <input
              type="text"
              autoFocus
              value={newUnitName}
              onChange={(e) => setNewUnitName(e.target.value)}
              placeholder="请输入单位名称"
              className="mt-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  handleCreateUnit();
                }
              }}
            />

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeCreateUnitModal}
                disabled={isCreatingUnit}
                className="px-4 py-2 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleCreateUnit}
                disabled={!newUnitName.trim() || isCreatingUnit}
                className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {isCreatingUnit ? "创建中..." : "确认创建"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
