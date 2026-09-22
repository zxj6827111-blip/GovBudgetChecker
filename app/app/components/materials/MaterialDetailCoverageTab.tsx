"use client";

import { Badge, Card, Metric } from "@/components/ui";

import {
  COVERAGE_UNAVAILABLE_LABEL,
  buildCoverageGroups,
  buildCoverageKpis,
  presentObligationItem,
  type CoverageBlock,
} from "./materialDetailAdapters";
import {
  presentCoverageStatus,
  presentCoverageReason,
} from "../../../lib/materialDetailPresentation";

/**
 * MaterialDetailCoverageTab（页面 11）：检查覆盖。
 *
 * 展示的是**这份材料自己**的检查覆盖（应当检查的事项里完成了多少），
 * 不是"系统总共有多少条规则"。
 *
 * 不可用时整体只说一句话
 * ----------------------
 * 没有覆盖记录时页面**不显示任何数字**：不显示 `0 / 0`、`0%`、`100%`、
 * 也不显示"全部通过"（§三十七）。"没有覆盖记录"与"覆盖完整"在数字上
 * 不能长得一样 —— 后者会被当成"这份材料已经查完了"。
 *
 * 阻塞事项只展示，不给操作入口
 * ----------------------------
 * `blocking_total > 0` 时页面明确写出"不得标成检查完成"，但**不**提供
 * "人工完成 / 关闭阻塞"按钮：那属于 WP3 的复核生命周期，本轮是只读。
 */
export interface MaterialDetailCoverageTabProps {
  coverage: CoverageBlock | null | undefined;
}

export function MaterialDetailCoverageTab({ coverage }: MaterialDetailCoverageTabProps) {
  if (!coverage?.available || !coverage.summary) {
    return (
      <Card title="检查覆盖" desc="这份材料的检查义务完成情况">
        <p className="text-sm text-slate-600" data-testid="gbc-material-coverage-unavailable">
          {COVERAGE_UNAVAILABLE_LABEL}。
        </p>
        <p className="mt-1 text-xs text-slate-500">
          可能原因：该文件版本尚未完成分析、分析结果尚未落库，或该次运行没有产出检查义务账本。
          没有记录时页面一个数字都不给：不用「零覆盖」或「全部覆盖」代替「没有记录」。
        </p>
      </Card>
    );
  }

  const summary = coverage.summary;
  const kpis = buildCoverageKpis(coverage);
  const groups = buildCoverageGroups(coverage);

  return (
    <div className="space-y-5">
      <div
        className="rounded-card border border-border bg-surface-100 px-4 py-3 text-xs text-slate-600"
        data-testid="gbc-material-coverage-scope"
      >
        <p>
          口径：应检查义务按材料画像展开，分母来自义务清单而不是规则注册数；
          {summary.catalog_version ? ` 检查要求版本 ${summary.catalog_version}` : " 检查要求版本未记录"}。
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {kpis.map((kpi) => (
          <Metric
            key={kpi.key}
            label={kpi.label}
            value={kpi.value}
            desc={kpi.hint}
            tone={kpi.key === "blocking" ? "danger" : "info"}
            data-testid={`gbc-material-coverage-kpi-${kpi.key}`}
          />
        ))}
      </div>

      {summary.blocking_total > 0 ? (
        <p
          className="rounded-card border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700"
          data-testid="gbc-material-coverage-blocking-note"
        >
          存在 {summary.blocking_total} 项阻塞未完成（未实现 / 未执行 / 取数不足 / 解析歧义 / 文种冲突），
          因此这份材料不能被标成「检查完成」。关闭阻塞属于复核生命周期，本轮只读展示。
        </p>
      ) : null}

      <Card
        title="检查义务台账"
        desc="业务用户看到的是「检查事项」，技术规则/义务编号只在展开时显示"
      >
        {groups.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="gbc-material-coverage-groups-empty">
            本次运行没有给出分组明细。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] border-collapse">
              <thead>
                <tr>
                  <th className="border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    检查分组
                  </th>
                  <th className="w-[140px] border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    完成 / 应检查
                  </th>
                  <th className="border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    未完成情况
                  </th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <tr key={group.groupId} data-testid={`gbc-material-coverage-group-${group.groupId}`}>
                    <td className="border-b border-border px-4 py-3 text-sm text-slate-800">
                      {group.title}
                    </td>
                    <td
                      className="border-b border-border px-4 py-3 text-sm text-slate-700"
                      data-testid={`gbc-material-coverage-group-${group.groupId}-progress`}
                    >
                      {group.progress}
                    </td>
                    <td className="border-b border-border px-4 py-3 text-sm">
                      {group.unresolvedNotes.length === 0 ? (
                        <span className="text-slate-400">全部完成</span>
                      ) : (
                        <ul className="flex flex-wrap gap-x-3 gap-y-1">
                          {group.unresolvedNotes.map((note) => (
                            <li
                              key={note.code}
                              className="text-danger-700"
                              data-testid={`gbc-material-coverage-group-${group.groupId}-note-${note.code}`}
                            >
                              {note.count} 项{note.label}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {coverage.items.length > 0 ? (
        <Card title="逐项明细" desc="技术义务编号与未完成原因放在展开详情里">
          <details data-testid="gbc-material-coverage-items">
            <summary className="cursor-pointer text-sm text-primary-700">
              展开全部 {coverage.items.length} 项检查义务
            </summary>
            <ul className="mt-2 space-y-1">
              {coverage.items.map((item) => {
                const view = presentObligationItem(item);
                const presentation = presentCoverageStatus(item.status);
                return (
                  <li
                    key={item.obligation_id}
                    className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                    data-testid={`gbc-material-coverage-item-${item.obligation_id}`}
                    data-obligation-status={item.status}
                  >
                    <Badge tone={presentation.tone}>{presentation.label}</Badge>
                    <span className="text-slate-800">{view.title}</span>
                    {item.reason ? (
                      <span className="text-xs text-slate-500">
                        {presentCoverageReason(item.reason)}
                      </span>
                    ) : null}
                    <span className="ml-auto text-xs text-slate-400">{view.technicalLabel}</span>
                  </li>
                );
              })}
            </ul>
          </details>
        </Card>
      ) : null}
    </div>
  );
}

export default MaterialDetailCoverageTab;
