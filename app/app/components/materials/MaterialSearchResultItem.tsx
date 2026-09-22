"use client";

import Link from "next/link";
import type { Route } from "next";

import { Badge } from "@/components/ui";

import type { MaterialSearchItem, MaterialSearchItemView } from "./materialSearchAdapters";
import { describeSearchItem } from "./materialSearchAdapters";

/**
 * MaterialSearchResultItem：一条搜索结果的展示。
 *
 * 三条必须守住的显示纪律：
 *
 * 1. **命中的是历史文件时必须显著标注**（§四十五）。只显示"当前文件名"会让用户
 *    以为搜索命中的就是当前版本；只显示历史文件名又会被当成当前材料。
 *    两个都显示，并给历史命中一个独立标记。
 * 2. **「打开材料」与"命中版本"无关**：href 恒为 `/materials/slots/<slotId>`，
 *    详情页的当前真值仍是槽位指针（§二十二）。搜索命中历史版本不得把详情
 *    切回旧版本结论。
 * 3. **「进入复核」不可用时给出原因，而不是一个点了没反应的按钮**（§二十九）。
 *    原因文案由适配层给出（后端权限 + 前端状态判定两段合取的结果）。
 */
export interface MaterialSearchResultItemProps {
  item: MaterialSearchItem;
  index: number;
  selected: boolean;
  onHover: (index: number) => void;
}

export function MaterialSearchResultItem({
  item,
  index,
  selected,
  onHover,
}: MaterialSearchResultItemProps) {
  const view: MaterialSearchItemView = describeSearchItem(item);

  return (
    <li
      data-testid="gbc-global-search-item"
      data-slot-id={item.slot_id}
      data-selected={selected ? "true" : "false"}
      onMouseEnter={() => onHover(index)}
      className={
        selected
          ? "rounded-md border border-primary-200 bg-primary-50 p-3"
          : "rounded-md border border-border bg-white p-3"
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-900" data-testid="gbc-global-search-subject">
            {view.title}
          </div>
          <div className="mt-0.5 truncate text-xs text-slate-500" data-testid="gbc-global-search-breadcrumb">
            {view.subtitle}
          </div>
        </div>
        <Badge
          // 历史版本命中用 review 色调（与"需人工复核"同色系）：它需要用户注意
          // "这不是当前版本"，但不是一个错误状态。
          tone={item.matched_version_is_current === false ? "review" : "neutral"}
          data-testid="gbc-global-search-matched-kind"
        >
          {item.matched_version_is_current === false ? "历史版本命中" : "命中材料"}
        </Badge>
      </div>

      <div className="mt-2 text-xs text-slate-600" data-testid="gbc-global-search-meta">
        {view.metaLine}
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500" data-testid="gbc-global-search-current-filename">
          当前文件：{view.currentFilename ?? "无当前文件版本"}
        </span>
        {view.historicalFilenameNotice ? (
          <span
            className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700"
            data-testid="gbc-global-search-historical-filename"
          >
            {view.historicalFilenameNotice}：{item.matched_filename}
          </span>
        ) : null}
        {view.historicalJobNotice ? (
          <span
            className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700"
            data-testid="gbc-global-search-historical-job"
          >
            {view.historicalJobNotice}：{item.matched_job_uuid}
          </span>
        ) : null}
      </div>

      <div className="mt-2 text-xs text-slate-500" data-testid="gbc-global-search-reason">
        {view.matchedReasonText}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Link
          href={view.openHref as Route}
          data-testid="gbc-global-search-open-material"
          className="rounded-md border border-border bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          打开材料
        </Link>
        {view.reviewHref ? (
          <Link
            href={view.reviewHref as Route}
            data-testid="gbc-global-search-enter-review"
            className="rounded-md bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-700"
          >
            进入复核
          </Link>
        ) : (
          <span
            className="text-xs text-slate-400"
            data-testid="gbc-global-search-review-blocked"
            title={view.reviewBlockedReason ?? undefined}
          >
            进入复核不可用：{view.reviewBlockedReason}
          </span>
        )}
      </div>
    </li>
  );
}

export default MaterialSearchResultItem;
