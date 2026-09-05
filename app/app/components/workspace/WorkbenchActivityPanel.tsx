/**
 * WorkbenchActivityPanel：原型图「最近活动」面板（Task 4.4）。
 *
 * `/api/activity` 是 require_admin（见 api/routes/activity.py），非管理员请求会
 * 收到 401/403。关键约束：非管理员必须优雅降级（隐藏面板或显示无权限提示），
 * 不得报错或崩页——本组件在拿到 403/401 时切换到"needs_admin"态而不是把
 * fetch 异常抛出去让整页崩溃。
 */
"use client";

import { useEffect, useState } from "react";

import { Card } from "@/components/ui";

interface ActivityEvent {
  ts?: number;
  action?: string;
  actor?: string;
  result?: string;
  resource_type?: string;
  resource_name?: string;
}

interface ActivityResponse {
  items?: ActivityEvent[];
}

type ActivityLoadState = "loading" | "loaded" | "needs_admin" | "error";

function formatRelativeTime(tsSeconds: number | undefined, nowMs: number): string {
  if (typeof tsSeconds !== "number" || !Number.isFinite(tsSeconds) || tsSeconds <= 0) {
    return "—";
  }
  const deltaMs = nowMs - tsSeconds * 1000;
  const deltaMinutes = Math.round(deltaMs / 60000);
  if (deltaMinutes < 1) {
    return "刚刚";
  }
  if (deltaMinutes < 60) {
    return `${deltaMinutes} 分钟前`;
  }
  const deltaHours = Math.round(deltaMinutes / 60);
  if (deltaHours < 24) {
    return `${deltaHours} 小时前`;
  }
  return `${Math.round(deltaHours / 24)} 天前`;
}

/** 把 action + resource_name 拼成原型图那种一句话描述，缺字段时不编造具体内容。 */
function describeEvent(event: ActivityEvent): string {
  const action = String(event.action ?? "").trim();
  const resourceName = String(event.resource_name ?? "").trim();
  if (action && resourceName) {
    return `${action} · ${resourceName}`;
  }
  return action || "（未记录操作类型）";
}

export function WorkbenchActivityPanel() {
  const [state, setState] = useState<ActivityLoadState>("loading");
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  useEffect(() => {
    let cancelled = false;

    async function loadActivity() {
      try {
        const response = await fetch("/api/activity?limit=10", { cache: "no-store" });
        if (response.status === 401 || response.status === 403) {
          if (!cancelled) {
            setState("needs_admin");
          }
          return;
        }
        if (!response.ok) {
          if (!cancelled) {
            setState("error");
          }
          return;
        }
        const payload = (await response.json()) as ActivityResponse;
        if (!cancelled) {
          setEvents(Array.isArray(payload.items) ? payload.items : []);
          setNowMs(Date.now());
          setState("loaded");
        }
      } catch {
        if (!cancelled) {
          setState("error");
        }
      }
    }

    void loadActivity();
    return () => {
      cancelled = true;
    };
  }, []);

  // 非管理员：优雅降级为提示文案，不隐藏整个面板（隐藏会让人以为页面缺了一块），
  // 也不抛错崩页——这是任务书 4.4 明确要求的两种可接受降级形态之一。
  if (state === "needs_admin") {
    return (
      <Card title="最近活动" data-testid="gbc-workbench-activity-panel">
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-activity-needs-admin">
          仅管理员可查看操作记录。
        </div>
      </Card>
    );
  }

  return (
    <Card title="最近活动" desc="审核、重试和元数据修正记录。" data-testid="gbc-workbench-activity-panel">
      {state === "loading" ? (
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-activity-loading">
          正在加载…
        </div>
      ) : state === "error" ? (
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-activity-error">
          活动记录暂时无法加载。
        </div>
      ) : events.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-activity-empty">
          暂无最近活动。
        </div>
      ) : (
        <ul className="space-y-3" data-testid="gbc-workbench-activity-list">
          {events.map((event, index) => (
            <li key={`${event.ts ?? index}-${index}`} className="border-l-2 border-primary-200 pl-3">
              <div className="text-sm text-slate-700">{describeEvent(event)}</div>
              <div className="mt-0.5 text-xs text-slate-400">{formatRelativeTime(event.ts, nowMs)}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default WorkbenchActivityPanel;
