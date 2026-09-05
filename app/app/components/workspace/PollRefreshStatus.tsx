"use client";

import { Button } from "@/components/ui";

/**
 * PollRefreshStatus（修复 1）：任务列表页共用的"最后更新时间 + 手动刷新"状态条。
 *
 * 目的：让"没在动"和"没刷新"可区分——
 * - 轮询全部终态停止后，用户看到的仍是真实数据，"最后更新 HH:MM:SS"说明了数据的新鲜度；
 * - 上次刷新失败时如实显示失败原因（不静默吞掉），且轮询循环本身会持续重试；
 * - 手动刷新入口让用户随时可以主动拉一次（也是全部终态停轮询后唯一的自动外恢复路径之外的显式入口）。
 */
export interface PollRefreshStatusProps {
  lastSyncedAt: Date | null;
  lastErrorMessage: string | null;
  isRefreshing: boolean;
  onRefresh: () => void;
  /** 测试锚点前缀，例如 "gbc-workbench-refresh"。 */
  testIdPrefix: string;
}

function formatClock(date: Date): string {
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function PollRefreshStatus({
  lastSyncedAt,
  lastErrorMessage,
  isRefreshing,
  onRefresh,
  testIdPrefix,
}: PollRefreshStatusProps) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-400">
      {lastErrorMessage ? (
        <span className="text-danger-600" data-testid={`${testIdPrefix}-error`}>
          上次刷新失败：{lastErrorMessage}（将持续重试）
        </span>
      ) : lastSyncedAt ? (
        <span data-testid={`${testIdPrefix}-synced-at`}>最后更新 {formatClock(lastSyncedAt)}</span>
      ) : (
        <span data-testid={`${testIdPrefix}-synced-at`}>尚未获取到数据</span>
      )}
      <Button
        variant="secondary"
        onClick={onRefresh}
        disabled={isRefreshing}
        data-testid={`${testIdPrefix}-button`}
        className="px-2 py-1 text-xs"
      >
        {isRefreshing ? "刷新中…" : "刷新"}
      </Button>
    </span>
  );
}

export default PollRefreshStatus;
