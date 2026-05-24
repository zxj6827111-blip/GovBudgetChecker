"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Organization = {
  id: string;
  name: string;
  level: string;
  level_name?: string;
};

type MatchSuggestion = {
  organization: Organization;
  confidence: number;
};

type CurrentMatch = {
  organization: Organization;
  match_type: string;
  confidence: number;
};

interface AssociateDialogProps {
  isOpen: boolean;
  jobId: string;
  filename: string;
  suggestions?: MatchSuggestion[];
  isSubmitting?: boolean;
  onClose: () => void;
  onAssociate: (orgId: string) => void | Promise<void>;
}

const levelStyles: Record<string, string> = {
  city: "bg-sky-100 text-sky-700",
  district: "bg-emerald-100 text-emerald-700",
  department: "bg-indigo-100 text-indigo-700",
  unit: "bg-slate-100 text-slate-700",
};

function normalizeSearchText(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

export default function AssociateDialog({
  isOpen,
  jobId,
  filename,
  suggestions = [],
  isSubmitting = false,
  onClose,
  onAssociate,
}: AssociateDialogProps) {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loadingOrganizations, setLoadingOrganizations] = useState(false);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [dynamicSuggestions, setDynamicSuggestions] = useState<MatchSuggestion[]>([]);
  const [currentMatch, setCurrentMatch] = useState<CurrentMatch | null>(null);

  const fetchOrganizations = useCallback(async () => {
    setLoadingOrganizations(true);
    try {
      const response = await fetch("/api/organizations/list", { cache: "no-store" });
      const payload = (await response.json().catch(() => ({}))) as {
        organizations?: Organization[];
      };
      setOrganizations(Array.isArray(payload.organizations) ? payload.organizations : []);
    } catch (error) {
      console.error("Failed to fetch organizations:", error);
      setOrganizations([]);
    } finally {
      setLoadingOrganizations(false);
    }
  }, []);

  const fetchSuggestions = useCallback(async () => {
    if (!jobId) {
      return;
    }

    setLoadingSuggestions(true);
    try {
      const response = await fetch(`/api/jobs/${jobId}/org-suggestions?top_n=5`, {
        cache: "no-store",
      });
      const payload = (await response.json().catch(() => ({}))) as {
        suggestions?: MatchSuggestion[];
        current?: CurrentMatch;
      };
      const nextSuggestions = Array.isArray(payload.suggestions) ? payload.suggestions : [];
      const nextCurrent = payload.current && typeof payload.current === "object" ? payload.current : null;
      setDynamicSuggestions(nextSuggestions);
      setCurrentMatch(nextCurrent);
      setSelectedOrgId(
        nextCurrent?.organization?.id ?? nextSuggestions[0]?.organization?.id ?? null,
      );
    } catch (error) {
      console.error("Failed to fetch organization suggestions:", error);
      setDynamicSuggestions([]);
      setCurrentMatch(null);
      setSelectedOrgId(null);
    } finally {
      setLoadingSuggestions(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setSearchQuery("");
    setSelectedOrgId(null);
    void fetchOrganizations();
    void fetchSuggestions();
  }, [fetchOrganizations, fetchSuggestions, isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isSubmitting, onClose]);

  const effectiveSuggestions = dynamicSuggestions.length > 0 ? dynamicSuggestions : suggestions;

  const filteredOrganizations = useMemo(() => {
    const query = normalizeSearchText(searchQuery);
    if (!query) {
      return organizations;
    }
    return organizations.filter((organization) =>
      normalizeSearchText(organization.name).includes(query),
    );
  }, [organizations, searchQuery]);

  const handleConfirm = () => {
    if (!selectedOrgId || isSubmitting) {
      return;
    }
    void onAssociate(selectedOrgId);
  };

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 px-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isSubmitting) {
          onClose();
        }
      }}
    >
      <div
        data-testid="associate-dialog"
        className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      >
        <div className="border-b border-slate-200 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">
                {currentMatch ? "更改报告关联" : "关联报告"}
              </h2>
              <p className="mt-1 text-sm text-slate-500">{filename || jobId}</p>
            </div>
            <button
              type="button"
              data-testid="associate-dialog-close"
              onClick={onClose}
              disabled={isSubmitting}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="关闭"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  d="M6 18 18 6M6 6l12 12"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                />
              </svg>
            </button>
          </div>

          {currentMatch ? (
            <div className="mt-4 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-700">
              <div className="font-medium">
                当前关联: {currentMatch.organization.name}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full bg-white/80 px-2 py-0.5">
                  {currentMatch.match_type === "manual" ? "手动关联" : "自动匹配"}
                </span>
                <span>
                  置信度 {(Number(currentMatch.confidence || 0) * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm text-amber-700">
              当前报告还没有关联到组织，请选择正确的部门或单位。
            </p>
          )}
        </div>

        {(loadingSuggestions || effectiveSuggestions.length > 0) && (
          <div className="border-b border-slate-200 bg-sky-50/70 px-6 py-4">
            <div className="text-sm font-medium text-sky-900">智能推荐</div>
            {loadingSuggestions ? (
              <div className="mt-2 text-sm text-sky-700">正在分析候选组织...</div>
            ) : (
              <div className="mt-3 space-y-2">
                {effectiveSuggestions.slice(0, 3).map(({ organization, confidence }) => {
                  const selected = selectedOrgId === organization.id;
                  return (
                    <button
                      key={organization.id}
                      type="button"
                      data-testid={`associate-suggestion-${organization.id}`}
                      onClick={() => setSelectedOrgId(organization.id)}
                      className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors ${
                        selected
                          ? "border-indigo-300 bg-indigo-50"
                          : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-900">
                          {organization.name}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                          <span
                            className={`rounded-full px-2 py-0.5 ${
                              levelStyles[organization.level] ?? levelStyles.unit
                            }`}
                          >
                            {organization.level_name || organization.level}
                          </span>
                          <span>置信度 {(confidence * 100).toFixed(0)}%</span>
                        </div>
                      </div>
                      {selected ? (
                        <span className="text-xs font-medium text-indigo-600">已选择</span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        <div className="border-b border-slate-200 px-6 py-4">
          <input
            type="text"
            data-testid="associate-dialog-search"
            placeholder="搜索组织名称"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
          />
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loadingOrganizations ? (
            <div className="py-10 text-center text-sm text-slate-500">正在加载组织列表...</div>
          ) : filteredOrganizations.length === 0 ? (
            <div className="py-10 text-center text-sm text-slate-500">
              {searchQuery ? "没有找到匹配的组织" : "当前没有可用的组织数据"}
            </div>
          ) : (
            <div className="space-y-2">
              {filteredOrganizations.map((organization) => {
                const selected = selectedOrgId === organization.id;
                return (
                  <button
                    key={organization.id}
                    type="button"
                    data-testid={`associate-option-${organization.id}`}
                    onClick={() => setSelectedOrgId(organization.id)}
                    className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors ${
                      selected
                        ? "border-indigo-300 bg-indigo-50"
                        : "border-transparent hover:border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-900">
                        {organization.name}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${
                        levelStyles[organization.level] ?? levelStyles.unit
                      }`}
                    >
                      {organization.level_name || organization.level}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-slate-200 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            data-testid="associate-dialog-submit"
            onClick={handleConfirm}
            disabled={!selectedOrgId || isSubmitting}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSubmitting ? "保存中..." : "确认关联"}
          </button>
        </div>
      </div>
    </div>
  );
}
