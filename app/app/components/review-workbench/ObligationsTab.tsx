"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import {
  obligationDecisionLabel,
  obligationDecisionTone,
} from "@/lib/reviewLifecyclePresentation";

import {
  isObligationHandled,
  type ObligationReviewBlockRecord,
  type ObligationReviewItemRecord,
} from "./reviewLifecycleAdapters";

/**
 * 「检查补核」页签（WP3-B）：把"检查义务阻塞复核"从死胡同变成人工待办。
 *
 * 每条待办只呈现人能决断的要素：义务是什么、引擎为什么没查成、
 * 当前人工结论（若有）。三个结论按钮（补核通过 / 确认存在问题 / 不适用）
 * 与"置回待处理"都是**显式人工动作**——系统永远不得替人自动选择其中之一。
 *
 * 改写已有结论时必须带上当前 ``decision_revision``（乐观锁）：
 * 另一个复核人已经改过的话，服务端会回 409，本组件把这条原因交给页面层
 * 逐条显示，而不是假装成功。
 */

interface ObligationsTabProps {
  block: ObligationReviewBlockRecord;
  /** 正在提交补核的义务 id（防连点）。 */
  submittingId: string | null;
  onDecide: (
    item: ObligationReviewItemRecord,
    decision: string,
    note: string,
  ) => void;
}

function ObligationRow({ item, submittingId, onDecide }: {
  item: ObligationReviewItemRecord;
  submittingId: string | null;
  onDecide: ObligationsTabProps["onDecide"];
}) {
  const handled = isObligationHandled(item);
  const [note, setNote] = useState(item.note ?? "");
  const busy = submittingId === item.obligation_id;

  return (
    <li
      className="border-b border-border px-4 py-3 last:border-b-0"
      data-testid={`gbc-obligation-item-${item.obligation_id}`}
      data-obligation-status={item.status}
      data-obligation-decision={item.decision ?? ""}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-800">
            {item.title ?? item.obligation_id}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            {[item.group_title, item.obligation_id].filter(Boolean).join(" · ")}
          </div>
        </div>
        <Badge tone={obligationDecisionTone(item.decision)}>
          {obligationDecisionLabel(item.decision)}
        </Badge>
      </div>

      <div className="mt-1 text-xs text-slate-500">
        {item.reason_label || item.reason || item.status}
        {item.detail ? `：${item.detail}` : ""}
      </div>

      {item.decided_by ? (
        <div className="mt-1 text-xs text-slate-400">
          {item.decided_by}
          {item.decided_at ? ` · ${item.decided_at}` : ""}
          {item.note ? ` · ${item.note}` : ""}
          {item.evidence_reference ? ` · 依据：${item.evidence_reference}` : ""}
        </div>
      ) : null}

      <div className="mt-2 flex items-center gap-2">
        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="补核依据（可选，审计留痕）"
          data-testid={`gbc-obligation-note-${item.obligation_id}`}
          className="h-8 min-w-0 flex-1 rounded-md border border-border px-2 text-xs text-slate-700 placeholder:text-slate-300"
          disabled={busy}
        />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          disabled={busy}
          data-testid={`gbc-obligation-verify-ok-${item.obligation_id}`}
          onClick={() => onDecide(item, "verified_ok", note)}
        >
          补核通过
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          data-testid={`gbc-obligation-verify-issue-${item.obligation_id}`}
          onClick={() => onDecide(item, "verified_issue", note)}
        >
          确认存在问题
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          data-testid={`gbc-obligation-not-applicable-${item.obligation_id}`}
          onClick={() => onDecide(item, "not_applicable", note)}
        >
          不适用
        </Button>
        {handled ? (
          <Button
            variant="secondary"
            disabled={busy}
            data-testid={`gbc-obligation-reset-${item.obligation_id}`}
            onClick={() => onDecide(item, "pending", note)}
          >
            置回待处理
          </Button>
        ) : null}
      </div>
      {/* 乐观锁版本留在 DOM 上供自动化断言：改写走哪个版本一眼可查。 */}
      {item.decision_revision !== null ? (
        <span
          className="hidden"
          data-testid={`gbc-obligation-revision-${item.obligation_id}`}
        >
          {item.decision_revision}
        </span>
      ) : null}
    </li>
  );
}

export function ObligationsTab({ block, submittingId, onDecide }: ObligationsTabProps) {
  if (!block.available) {
    return (
      <div className="px-4 py-6 text-sm text-slate-500" data-testid="gbc-obligation-unavailable">
        当前文件版本暂无可确认的检查覆盖记录，无法进行人工补核。
      </div>
    );
  }
  if (block.items.length === 0) {
    return (
      <div className="px-4 py-6 text-sm text-slate-500" data-testid="gbc-obligation-empty">
        本次复核没有需要人工补核的检查义务。
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div
        className="shrink-0 border-b border-border bg-surface-50 px-4 py-2 text-xs text-slate-500"
        data-testid="gbc-obligation-summary"
      >
        待补核 {block.pending_total ?? 0} 项 · 共 {block.items.length} 项
      </div>
      <ul className="flex-1 overflow-auto" data-testid="gbc-obligation-list">
        {block.items.map((item) => (
          <ObligationRow
            key={item.obligation_id}
            item={item}
            submittingId={submittingId}
            onDecide={onDecide}
          />
        ))}
      </ul>
    </div>
  );
}

export default ObligationsTab;
