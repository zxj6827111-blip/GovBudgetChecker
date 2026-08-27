/**
 * WorkbenchAlertsPanel：原型图「质量告警」面板（Task 4.3）。
 *
 * 严禁事项：原型图画了三条告警，第三条"Golden Corpus 召回率下降"本轮无标注
 * 语料（决策 1=b，见 docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节第 2 条），
 * 一律不渲染。本组件只负责渲染 deriveQualityAlerts() 已经派生好的列表，
 * 不在这里补任何第三条——渲染层与派生逻辑分离，即使未来有人想在 JSX 里"顺手加一条"
 * 也无法绕开 workbenchAdapters.ts 里没有对应分支这件事。
 */
"use client";

import { Card } from "@/components/ui";

import type { QualityAlert } from "./workbenchAdapters";

export interface WorkbenchAlertsPanelProps {
  alerts: QualityAlert[] | null;
  onAlertClick: (alert: QualityAlert) => void;
}

export function WorkbenchAlertsPanel({ alerts, onAlertClick }: WorkbenchAlertsPanelProps) {
  return (
    <Card title="质量告警" desc="优先处理影响结论可信度的问题。" data-testid="gbc-workbench-alerts-panel">
      {alerts === null ? (
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-alerts-loading">
          正在加载…
        </div>
      ) : alerts.length === 0 ? (
        <div className="py-6 text-center text-sm text-slate-400" data-testid="gbc-workbench-alerts-empty">
          当前没有触发阈值的质量告警。
        </div>
      ) : (
        <ul className="space-y-3" data-testid="gbc-workbench-alerts-list">
          {alerts.map((alert) => (
            <li key={alert.id}>
              <button
                type="button"
                onClick={() => onAlertClick(alert)}
                data-testid={`gbc-workbench-alert-${alert.id}`}
                className="w-full rounded-md border-l-4 border-warning-600 bg-warning-50 p-3 text-left transition-colors hover:bg-warning-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
              >
                <div className="text-sm font-semibold text-slate-900">{alert.title}</div>
                <div className="mt-1 text-xs text-slate-500">{alert.description}</div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default WorkbenchAlertsPanel;
