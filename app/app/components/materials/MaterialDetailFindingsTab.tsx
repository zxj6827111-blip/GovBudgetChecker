"use client";

import { Badge, Card } from "@/components/ui";

import {
  ANALYSIS_UNAVAILABLE_LABEL,
  buildFindingsSections,
  type AnalysisBlock,
  type AnalysisFindingItem,
} from "./materialDetailAdapters";
import {
  formatCount,
  presentEvidenceStatus,
  presentSeverityBucket,
  presentSeverityCode,
} from "../../../lib/materialDetailPresentation";

/**
 * MaterialDetailFindingsTab（页面 10 的「检查结果」Tab）。
 *
 * 分三栏（**展示分组**）：正式问题 / 需人工核验 / 信息提示。
 *
 * 为什么必须分栏而不是一张表
 * --------------------------
 * "证据不足被降级"的条目与"已确认的正式 finding"含义完全不同：前者需要人去看，
 * 后者是需要处置的结论。混在一张表里，用户会把降级项当成确认的问题（过度告警），
 * 或者反过来把降级项也算进"没有问题"的分母（漏报）。三栏之和等于该版本分析的
 * 全部条目，既不漏也不重。
 *
 * 展示分组 ≠ 计数口径（这条边界必须守住）
 * --------------------------------------
 * 顶部的「正式检查记录：N」取后端的 `formal_issue_count`，其权威语义是
 * `evidence_guard.count_formal_findings`：**没有被降级就算正式 finding**，
 * `error` / `warn` / `info` 都算。所以 N = len(正式问题) + len(信息提示)，
 * 恒等成立。三栏只是把这批正式 finding 按严重度拆开展示，
 * 前端**不做**任何二次判定，也不允许用"正式问题"那一栏的长度去对外报数。
 */
export interface MaterialDetailFindingsTabProps {
  analysis: AnalysisBlock | null | undefined;
}

function FindingList({ items, testId }: { items: AnalysisFindingItem[]; testId: string }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid={`${testId}-empty`}>
        该分类下没有条目。
      </p>
    );
  }
  return (
    <ul className="space-y-2" data-testid={testId}>
      {items.map((item, index) => {
        const severity = presentSeverityBucket(item.severity_bucket);
        const evidenceStatus = presentEvidenceStatus(item.evidence_status);
        return (
          <li
            key={item.finding_id ?? `${testId}-${index}`}
            className="rounded-md border border-border bg-white px-3 py-2"
            data-testid={`${testId}-item-${index}`}
            data-finding-id={item.finding_id ?? ""}
            data-severity-bucket={item.severity_bucket}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={severity.tone} data-testid={`${testId}-item-${index}-severity`}>
                {presentSeverityCode(item.severity) || severity.label}
              </Badge>
              <span className="text-sm font-medium text-slate-900">
                {item.title ?? "（未记录标题）"}
              </span>
              {item.rule_id ? (
                <span className="text-xs text-slate-400" data-testid={`${testId}-item-${index}-rule`}>
                  规则 {item.rule_id}
                </span>
              ) : null}
              {item.obligation_ids.length > 0 ? (
                <span
                  className="text-xs text-slate-400"
                  data-testid={`${testId}-item-${index}-obligations`}
                >
                  对应义务 {item.obligation_ids.join("、")}
                </span>
              ) : null}
            </div>

            {item.message ? (
              <p className="mt-1 text-sm text-slate-700" data-testid={`${testId}-item-${index}-message`}>
                {item.message}
              </p>
            ) : null}

            <p className="mt-1 text-xs text-slate-500" data-testid={`${testId}-item-${index}-evidence`}>
              证据位置：
              {item.evidence_page ? `第 ${item.evidence_page} 页` : "未定位到页码"}
              {item.evidence_text ? ` · ${item.evidence_text}` : ""}
            </p>

            {evidenceStatus ? (
              <p className="mt-1 text-xs text-slate-500" data-testid={`${testId}-item-${index}-evidence-status`}>
                {evidenceStatus}
              </p>
            ) : null}
            {item.why_not ? (
              <p className="mt-1 text-xs text-slate-400">未采纳原因：{item.why_not}</p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function MaterialDetailFindingsTab({ analysis }: MaterialDetailFindingsTabProps) {
  if (!analysis?.available) {
    return (
      <Card title="检查结果" desc="只展示当前文件版本的已落库分析结论">
        <p
          className="text-sm text-slate-600"
          data-testid="gbc-material-findings-unavailable"
        >
          {ANALYSIS_UNAVAILABLE_LABEL}。
        </p>
        <p className="mt-1 text-xs text-slate-500">
          没有可确认的结论时，页面不会给出任何问题数，也不会把它显示成「未发现问题」。
        </p>
      </Card>
    );
  }

  const sections = buildFindingsSections(analysis);
  const formalCount: number | null = analysis.formal_issue_count ?? null;

  return (
    <div className="space-y-5">
      <div
        className="rounded-card border border-border bg-surface-100 px-4 py-3 text-xs text-slate-600"
        data-testid="gbc-material-findings-scope"
      >
        <p>
          以下结论只对应当前文件版本的分析运行
          {analysis.run?.job_uuid ? `（${analysis.run.job_uuid}）` : ""}。
          历史版本的分析不会出现在这里。
        </p>
        <p className="mt-1" data-testid="gbc-material-findings-formal-count">
          正式检查记录：{formatCount(formalCount ?? null)}
          {formalCount === null
            ? "（当前算不出来）"
            : formalCount === 0
              ? "（已确认没有正式 finding）"
              : ""}
        </p>
        {/* 总计口径与工作台/质量门禁完全一致：没有被 evidence guard 降级就是
            正式 finding，info 也算。因此这里必须说明"包含信息提示"，
            否则用户会把上面的总计与下面的「信息提示」栏看成两回事。 */}
        <p className="mt-1 text-xs text-slate-500" data-testid="gbc-material-findings-formal-count-note">
          包含正式问题与信息提示；证据不足被降级的待人工核验项不计入。
        </p>
      </div>

      {sections.map((section) => (
        <Card
          key={section.key}
          title={section.title}
          desc={section.desc}
          data-testid={`gbc-material-findings-section-${section.key}`}
        >
          <FindingList items={section.items} testId={`gbc-material-findings-${section.key}`} />
        </Card>
      ))}
    </div>
  );
}

export default MaterialDetailFindingsTab;
