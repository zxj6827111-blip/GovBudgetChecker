/**
 * ArchivePage：Task 9「导出归档」——把旧单体（viewer/gbc-ui-demo）的整改包
 * 能力迁入新 UI 的第 9 个导航入口，确保"确认问题 → 生成整改包 → 下载"
 * 交付闭环不丢失（这是修复 D 入口切换的前置条件）。
 *
 * 能力等价性（对照旧单体 ArchivePage / createPackage / downloadPackage）：
 * - 一键生成整改包：默认打包全部已确认（confirmed）问题；旧单体的
 *   "勾选子集生成"来自问题处理台，新 UI 无该屏，因此在本页的已确认问题
 *   清单上提供勾选（默认全选），子集打包能力不丢失；
 * - 整改包列表：名称/单位/问题数/任务数/内容/状态/操作（下载 ZIP）；
 * - 下载：POST /api/reports/download-batch {job_ids} → blob →
 *   `{整改包名}.zip`（与旧单体完全相同的接口与文件名口径）；
 * - 生成后问题转 in_package：后端 create_package 行为，前端用响应里的
 *   state 刷新（与旧单体一致）。
 *
 * 接口路径用 /api/workflow（Task 6.6 建的代理），不用旧单体的
 * /api/gbc-ui-demo/workflow——后者随 Task 10 下线，新页必须独立于它。
 *
 * 反例（任务书要求）：无已确认问题时不得生成空整改包——按钮禁用 + 明确
 * 提示，不发请求（buildCreatePackagePayload 对空目标返回 null）。
 */
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, Metric, SectionTitle, Td, Th } from "@/components/ui";
import type { IssueWorkflowRecord, IssueWorkflowState, RemediationPackageRecord } from "@/lib/issueWorkflowTypes";
import { formatDateTime } from "@/lib/uiAdapters";

import {
  PACKAGE_CONTENT_TEXT,
  buildCreatePackagePayload,
  buildPackageDownloadBody,
  buildPackageDownloadFilename,
  deriveArchiveSummary,
  normalizeWorkflowState,
  resolvePackageStatusLabel,
  resolvePackageStatusTone,
  selectConfirmedIssues,
  type CreatePackagePayload,
} from "./archivePageAdapters";

interface WorkflowMutationResponse {
  state?: IssueWorkflowState;
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text) as Record<string, unknown>;
    return String(payload.detail || payload.error || payload.message || text || `HTTP ${response.status}`);
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function issueDisplayName(record: IssueWorkflowRecord): string {
  return record.title || record.issue_id;
}

export function ArchivePage() {
  const [workflowState, setWorkflowState] = useState<IssueWorkflowState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  /** 勾选的打包目标（已确认问题的 key）。默认跟随全部已确认问题（见下方同步 effect）。 */
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [downloadingPackageId, setDownloadingPackageId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const loadWorkflow = useCallback(async () => {
    try {
      const response = await fetch("/api/workflow", { cache: "no-store" });
      if (!response.ok) {
        setLoadFailed(true);
        return;
      }
      setWorkflowState(normalizeWorkflowState(await response.json()));
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void loadWorkflow();
  }, [loadWorkflow]);

  const confirmedIssues = useMemo(() => selectConfirmedIssues(workflowState), [workflowState]);
  const summary = useMemo(() => deriveArchiveSummary(workflowState), [workflowState]);

  // 已确认问题集合变化时同步默认勾选：新确认的问题默认纳入打包范围
  // （等价于旧单体"未显式勾选时打包全部已确认"的默认行为）。
  useEffect(() => {
    setSelectedKeys((previous) => {
      const currentKeys = new Set(confirmedIssues.map((record) => record.key));
      const kept = previous.filter((key) => currentKeys.has(key));
      const added = confirmedIssues
        .map((record) => record.key)
        .filter((key) => !previous.includes(key));
      return [...kept, ...added];
    });
  }, [confirmedIssues]);

  const selectedTargets = useMemo(
    () => confirmedIssues.filter((record) => selectedKeys.includes(record.key)),
    [confirmedIssues, selectedKeys],
  );

  const toggleIssue = useCallback((key: string) => {
    setSelectedKeys((previous) =>
      previous.includes(key) ? previous.filter((item) => item !== key) : [...previous, key],
    );
  }, []);

  const toggleAll = useCallback(() => {
    setSelectedKeys((previous) =>
      previous.length === confirmedIssues.length ? [] : confirmedIssues.map((record) => record.key),
    );
  }, [confirmedIssues]);

  const handleCreatePackage = useCallback(async () => {
    const payload: CreatePackagePayload | null = buildCreatePackagePayload(selectedTargets);
    if (!payload) {
      // 反例防线：空目标不出请求（正常情况下按钮已禁用，这里兜底）。
      setStatusMessage({ tone: "error", text: "没有可生成整改包的问题：请先在审核工作台确认问题。" });
      return;
    }
    setIsCreating(true);
    setStatusMessage(null);
    try {
      const response = await fetch("/api/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const result = (await response.json()) as WorkflowMutationResponse;
      if (result.state) {
        setWorkflowState(normalizeWorkflowState(result.state));
      } else {
        await loadWorkflow();
      }
      setStatusMessage({ tone: "success", text: `整改包已生成（${payload.issue_keys.length} 个问题，${payload.job_ids.length} 个任务）。` });
    } catch (error) {
      setStatusMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "整改包生成失败",
      });
    } finally {
      setIsCreating(false);
    }
  }, [loadWorkflow, selectedTargets]);

  const handleDownloadPackage = useCallback(async (pkg: RemediationPackageRecord) => {
    const body = buildPackageDownloadBody(pkg);
    if (body.job_ids.length === 0) {
      setStatusMessage({ tone: "error", text: `整改包「${pkg.name}」没有可导出的任务。` });
      return;
    }
    setDownloadingPackageId(pkg.id);
    setStatusMessage(null);
    try {
      const response = await fetch("/api/reports/download-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = buildPackageDownloadFilename(pkg);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setStatusMessage({ tone: "success", text: `归档报告包「${pkg.name}」已开始下载。` });
    } catch (error) {
      setStatusMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "归档报告包下载失败",
      });
    } finally {
      setDownloadingPackageId(null);
    }
  }, []);

  const canCreatePackage = selectedTargets.length > 0 && !isCreating;

  return (
    <div className="p-8" data-testid="gbc-archive-page">
      <SectionTitle
        title="导出归档"
        desc="将已确认问题、证据、整改建议和材料版本记录生成客户整改包与年度归档包。"
        action={
          <Button
            variant="primary"
            onClick={() => void handleCreatePackage()}
            disabled={!canCreatePackage}
            data-testid="gbc-archive-create-package"
            title={
              selectedTargets.length === 0
                ? "没有已确认的问题可打包：请先在审核工作台确认问题"
                : undefined
            }
          >
            {isCreating ? "正在生成…" : "一键生成整改包"}
          </Button>
        }
      />

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Metric
          label="待生成整改包"
          corner="已确认"
          value={summary?.confirmedCount}
          desc="已确认问题"
          tone="warning"
          data-testid="gbc-archive-confirmed-count"
        />
        <Metric
          label="可下载结果"
          corner="整改包"
          value={summary?.packageCount}
          desc="单位级结果包"
          tone="success"
          data-testid="gbc-archive-package-count"
        />
        <Metric
          label="已归档问题"
          corner="in_package"
          value={summary?.inPackageCount}
          desc="整改包内问题"
          tone="info"
          data-testid="gbc-archive-inpackage-count"
        />
      </div>

      {statusMessage ? (
        <div
          className={`mt-4 rounded-md border px-4 py-3 text-sm ${
            statusMessage.tone === "success"
              ? "border-success-200 bg-success-50 text-success-700"
              : "border-danger-200 bg-danger-50 text-danger-700"
          }`}
          data-testid="gbc-archive-status"
        >
          {statusMessage.text}
        </div>
      ) : null}

      {loadFailed ? (
        <div
          className="mt-6 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-archive-load-failed"
        >
          工作流状态加载失败，请刷新重试。
        </div>
      ) : null}

      <div className="mt-6 rounded-card border border-border bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-medium text-slate-900">
            已确认问题（{confirmedIssues.length}）· 勾选后可只打包子集
          </div>
          {confirmedIssues.length > 0 ? (
            <Button variant="secondary" size="sm" onClick={toggleAll} data-testid="gbc-archive-toggle-all">
              {selectedKeys.length === confirmedIssues.length ? "全不选" : "全选"}
            </Button>
          ) : null}
        </div>
        {workflowState === null && !loadFailed ? (
          <div className="p-4 text-center text-sm text-slate-500" data-testid="gbc-archive-loading">
            正在加载工作流状态…
          </div>
        ) : confirmedIssues.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-6 text-center text-sm text-slate-500"
            data-testid="gbc-archive-empty-hint"
          >
            暂无已确认问题。请先在审核工作台确认问题，再回到本页生成整改包——空包没有交付意义，系统不会生成空整改包。
          </div>
        ) : (
          <ul className="divide-y divide-border" data-testid="gbc-archive-issue-list">
            {confirmedIssues.map((record) => (
              <li key={record.key} className="flex items-start gap-3 py-2.5">
                <input
                  type="checkbox"
                  checked={selectedKeys.includes(record.key)}
                  onChange={() => toggleIssue(record.key)}
                  aria-label={`打包问题 ${issueDisplayName(record)}`}
                  data-testid={`gbc-archive-issue-check-${record.key}`}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-500"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-slate-900" title={issueDisplayName(record)}>
                    {issueDisplayName(record)}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-400">
                    {record.organization_name || "未关联单位"} · 任务 {record.job_id}
                    {typeof record.page === "number" ? ` · 第 ${record.page} 页` : ""} · 确认于{" "}
                    {formatDateTime(record.updated_at)}
                  </div>
                </div>
                {record.severity ? <Badge tone="neutral">{record.severity}</Badge> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-6 overflow-x-auto rounded-card border border-border" data-testid="gbc-archive-package-table">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr>
              <Th>整改包名称</Th>
              <Th>单位</Th>
              <Th>问题数</Th>
              <Th>任务数</Th>
              <Th>内容</Th>
              <Th>状态</Th>
              <Th className="text-right">操作</Th>
            </tr>
          </thead>
          <tbody>
            {(workflowState?.packages ?? []).length === 0 ? (
              <tr>
                <Td colSpan={7} className="text-center text-sm text-slate-500" data-testid="gbc-archive-package-empty">
                  暂无整改包，请先确认问题后生成。
                </Td>
              </tr>
            ) : (
              (workflowState?.packages ?? []).map((pkg) => (
                <tr key={pkg.id} data-testid={`gbc-archive-package-row-${pkg.id}`} className="hover:bg-surface-100">
                  <Td>
                    <span className="font-medium text-slate-900">{pkg.name}</span>
                    <div className="mt-0.5 text-xs text-slate-400">生成时间：{formatDateTime(pkg.created_at)}</div>
                  </Td>
                  <Td>
                    <span title={pkg.organization_name ?? undefined}>
                      {pkg.organization_name || "多单位"}
                    </span>
                  </Td>
                  <Td>{pkg.issue_keys.length}</Td>
                  <Td>{pkg.job_ids.length}</Td>
                  <Td className="text-xs text-slate-500">{PACKAGE_CONTENT_TEXT}</Td>
                  <Td>
                    <Badge tone={resolvePackageStatusTone(pkg.status)}>{resolvePackageStatusLabel(pkg.status)}</Badge>
                  </Td>
                  <Td className="text-right">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void handleDownloadPackage(pkg)}
                      disabled={downloadingPackageId === pkg.id}
                      data-testid={`gbc-archive-download-${pkg.id}`}
                    >
                      {downloadingPackageId === pkg.id ? "下载中…" : "下载 ZIP"}
                    </Button>
                  </Td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default ArchivePage;
