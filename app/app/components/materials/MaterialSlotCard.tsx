"use client";

import Link from "next/link";
import type { Route } from "next";

import type { MaterialSlotSummary, SubjectSlotRef } from "./materialLedgerAdapters";
import { presentSlotCell } from "./materialLedgerAdapters";
import { MaterialStatusBadge } from "./MaterialStatusBadge";
import { MaterialStatusReason } from "./MaterialStatusReason";

import {
  formatMaterialTimestamp,
  presentCaliber,
  presentMaterialScope,
  presentReportKind,
  presentSubjectKind,
} from "@/lib/materialStatusPresentation";

/**
 * MaterialSlotCard：材料矩阵/时间轴里"一个主体 × 一个文种"的槽位卡。
 *
 * 显示：状态 + 状态原因 + 财政年度 + 文种 + 口径 + 更新时间（§三十六）。
 *
 * 三条口径写在实现里
 * ------------------
 * 1. **没有槽位时只显示「尚无已建立材料」**。禁止显示"缺失 / 逾期 / 未上传"——
 *    应收基线未建立时，"库里没有"推不出"应该有却没有"。
 * 2. **口径冲突必须独立于当前口径显示**。`caliber_conflict_candidate != null`
 *    表示有一次相反的口径识别还没被人工裁决，只显示当前口径会把矛盾藏起来。
 * 3. **有槽位才可点击**（WP2-B §六十五）。`href` 只在槽位真实存在时用于跳详情：
 *    没有槽位却给一个链接，等于把"尚无已建立材料"变成"点进去看看"，
 *    用户点开只会看到一个 404。
 */
export interface MaterialSlotCardProps {
  ref: SubjectSlotRef | null | undefined;
  /** 用于拼 data-testid，如 `gbc-material-slot-<subject>-budget`。 */
  testId: string;
  /** 有值则整卡可点击进入材料详情；null/undefined 时保持纯展示。 */
  href?: string | null;
}

function SlotDetail({ slot }: { slot: MaterialSlotSummary }) {
  return (
    <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-500">
      <div>
        <dt className="inline">年度：</dt>
        <dd className="inline text-slate-700">{slot.fiscal_year ?? "未识别"}</dd>
      </div>
      <div>
        <dt className="inline">文种：</dt>
        <dd className="inline text-slate-700">{presentReportKind(slot.report_kind)}</dd>
      </div>
      <div>
        <dt className="inline">口径：</dt>
        <dd className="inline text-slate-700">{presentCaliber(slot.caliber)}</dd>
      </div>
      <div>
        <dt className="inline">主体：</dt>
        <dd className="inline text-slate-700">
          {presentSubjectKind(slot.subject_kind)} · {presentMaterialScope(slot.material_scope)}
        </dd>
      </div>
      <div className="col-span-2">
        <dt className="inline">更新时间：</dt>
        <dd className="inline text-slate-700">{formatMaterialTimestamp(slot.updated_at)}</dd>
      </div>
    </dl>
  );
}

export function MaterialSlotCard({ ref, testId, href = null }: MaterialSlotCardProps) {
  const cell = presentSlotCell(ref);

  if (!cell.exists || !cell.slot) {
    return (
      <div
        className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-slate-400"
        data-testid={testId}
        data-slot-exists="false"
      >
        {cell.emptyLabel}
      </div>
    );
  }

  const slot = cell.slot;
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <MaterialStatusBadge status={slot.status} testId={`${testId}-status`} />
        <MaterialStatusReason
          reason={slot.status_reason}
          className="text-xs text-slate-500"
          testId={`${testId}-reason`}
        />
      </div>

      {slot.caliber_conflict_candidate ? (
        <p
          className="mt-1 text-xs text-warning-700"
          data-testid={`${testId}-caliber-conflict`}
        >
          口径冲突待确认：当前为「{presentCaliber(slot.caliber)}」，另一次识别为「
          {presentCaliber(slot.caliber_conflict_candidate)}」，需人工裁决。
        </p>
      ) : null}

      <SlotDetail slot={slot} />
    </>
  );

  if (!href) {
    return (
      <div
        className="rounded-md border border-border bg-white px-3 py-2"
        data-testid={testId}
        data-slot-exists="true"
        data-slot-id={slot.slot_id}
        data-slot-status={String(slot.status)}
      >
        {body}
      </div>
    );
  }

  return (
    <Link
      href={href as Route}
      className="block rounded-md border border-border bg-white px-3 py-2 transition-colors hover:border-primary-300 hover:bg-primary-50/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
      data-testid={testId}
      data-slot-exists="true"
      data-slot-id={slot.slot_id}
      data-slot-status={String(slot.status)}
      data-slot-href={href}
    >
      {body}
    </Link>
  );
}

export default MaterialSlotCard;
