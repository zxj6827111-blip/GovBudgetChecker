"use client";

import { useMemo } from "react";

import { Badge, Card } from "@/components/ui";

import {
  readSlotVersionsPayload,
  type MaterialVersionItem,
  type SlotDetailData,
  type SlotVersionListResponse,
} from "./materialDetailAdapters";
import {
  formatFileHash,
  formatFileSize,
  presentSourceKind,
  presentSourceStatus,
  presentVersionBadge,
} from "../../../lib/materialDetailPresentation";
import { formatMaterialTimestamp } from "@/lib/materialStatusPresentation";
import { useMaterialResource } from "./useMaterialResource";

/**
 * MaterialDetailVersionsTab（页面 12）：版本与来源。
 *
 * 两块内容刻意分开：**文件版本**（这份材料的 PDF 有几个版本、哪一个是当前）
 * 与 **材料来源**（这份材料是从哪里来的、发布日期是什么时候）。
 * 把两者混在一起，就会出现"版本时间"与"发布日期"被当成同一件事的经典错位。
 *
 * 三条不能让步的口径
 * ------------------
 * 1. **版本历史绝不删除**：只读展示，不提供"只保留最新版"之类的清理入口；
 * 2. **不展示内部存储路径**：`storage_key` 后端根本不返回。版本级的安全预览
 *    入口本轮也不生成 —— 仓库现有 PDF 入口都是任务维度、另一套权限主体，
 *    从槽位页面发一个任务链接会让"页面可见"与"链接可用"分属两套判定；
 * 3. **人工上传没有 URL 是正常的**：显示「人工上传」，不是"来源缺失"。
 */
export interface MaterialDetailVersionsTabProps {
  slotId: string;
  /** 首屏的详情数据：用来立刻渲染当前版本，不必等第二个请求。 */
  detail: SlotDetailData;
}

function VersionCard({ version, testId }: { version: MaterialVersionItem; testId: string }) {
  const badge = presentVersionBadge(version.is_current);
  return (
    <div
      className="rounded-md border border-border bg-white px-4 py-3"
      data-testid={testId}
      data-document-version-id={version.document_version_id}
      data-is-current={version.is_current ? "true" : "false"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-900">
          v{version.document_version_id}
        </span>
        <Badge
          tone={badge.tone}
          data-testid={`${testId}-badge`}
        >
          {badge.label}
        </Badge>
        <span className="text-sm text-slate-700">
          {version.original_filename ?? "文件名未记录"}
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs text-slate-500 sm:grid-cols-2">
        <div>
          <dt className="inline">文件哈希：</dt>
          <dd className="inline text-slate-700" data-testid={`${testId}-hash`}>
            {formatFileHash(version.file_hash)}
          </dd>
        </div>
        <div>
          <dt className="inline">文件大小：</dt>
          <dd className="inline text-slate-700">{formatFileSize(version.file_size_bytes)}</dd>
        </div>
        <div>
          <dt className="inline">创建时间：</dt>
          <dd className="inline text-slate-700">{formatMaterialTimestamp(version.created_at)}</dd>
        </div>
        <div>
          <dt className="inline">存储后端：</dt>
          <dd className="inline text-slate-700">{version.storage_backend ?? "—"}</dd>
        </div>
      </dl>
      <p
        className="mt-2 text-xs text-slate-400"
        data-testid={`${testId}-preview-note`}
      >
        {version.preview_url || version.download_url
          ? "预览与下载入口已提供。"
          : "版本级的安全预览/下载入口暂未开放（现有 PDF 入口按任务维度鉴权，与材料权限不是同一主体）。可在审核工作台按任务查看原件。"}
      </p>
    </div>
  );
}

export function MaterialDetailVersionsTab({ slotId, detail }: MaterialDetailVersionsTabProps) {
  const url = useMemo(
    () => `/api/materials/slots/${encodeURIComponent(slotId)}/versions`,
    [slotId],
  );
  const response = useMaterialResource<SlotVersionListResponse>(url, readSlotVersionsPayload);
  const items = response.data?.data.items ?? null;

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <Card
        title="材料来源"
        desc="官网来源 / 人工上传 / 表格导入；发布日期与财政年度分开记录"
        data-testid="gbc-material-versions-sources"
      >
        {detail.sources.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="gbc-material-sources-empty">
            当前没有登记材料来源记录。这不代表这份材料「没有来源」—— 来源记录尚未采集时，
            页面不会凭空编一个。
          </p>
        ) : (
          <ul className="space-y-3">
            {detail.sources.map((source, index) => (
              <li
                key={source.source_id}
                className="rounded-md border border-border px-3 py-2 text-sm"
                data-testid={`gbc-material-source-${index}`}
                data-source-kind={source.source_kind}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    tone={source.source_kind === "official_site" ? "processing" : "neutral"}
                    data-testid={`gbc-material-source-${index}-kind`}
                  >
                    {presentSourceKind(source.source_kind)}
                  </Badge>
                  <span className="text-slate-700">
                    {source.source_site ?? source.source_page_title ?? "—"}
                  </span>
                  <span className="text-xs text-slate-500" data-testid={`gbc-material-source-${index}-status`}>
                    {presentSourceStatus(source.status)}
                  </span>
                </div>
                <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1 text-xs text-slate-500 sm:grid-cols-2">
                  <div>
                    <dt className="inline">网页标题：</dt>
                    <dd className="inline text-slate-700">{source.source_page_title ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="inline">来源 URL：</dt>
                    <dd
                      className="inline break-all text-slate-700"
                      data-testid={`gbc-material-source-${index}-url`}
                    >
                      {source.source_url ?? "无（人工上传来源本就没有 URL）"}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline">网页发布日期：</dt>
                    <dd
                      className="inline text-slate-700"
                      data-testid={`gbc-material-source-${index}-published`}
                    >
                      {formatMaterialTimestamp(source.published_at)}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline">发现时间：</dt>
                    <dd className="inline text-slate-700">
                      {formatMaterialTimestamp(source.discovered_at)}
                    </dd>
                  </div>
                </dl>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-slate-500">
          口径原则：财政年度 ≠ 网页发布年份。2024 年度决算可以在 2025 年发布，
          仍归入「财政年度 2024」。
        </p>
      </Card>

      <Card
        title="文件版本"
        desc="同一材料槽位允许有多个 PDF 版本；历史版本不会被清理"
        data-testid="gbc-material-versions-list"
      >
        {response.loading ? (
          <p className="text-sm text-slate-500" data-testid="gbc-material-versions-loading">
            正在加载版本历史…
          </p>
        ) : null}
        {response.error ? (
          <div className="text-sm text-danger-700" data-testid="gbc-material-versions-error">
            <p>版本历史加载失败：{response.error}</p>
            <button
              type="button"
              onClick={response.reload}
              className="mt-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600"
              data-testid="gbc-material-versions-retry"
            >
              重试
            </button>
          </div>
        ) : null}
        {!response.loading && !response.error && items ? (
          items.length === 0 ? (
            <p className="text-sm text-slate-500" data-testid="gbc-material-versions-empty">
              该材料槽位尚未关联任何 PDF 版本。这不等于该单位从未上传过文件 ——
              文件可能已上传但未绑定到这条材料。
            </p>
          ) : (
            <div className="space-y-3">
              {items.map((version) => (
                <VersionCard
                  key={version.document_version_id}
                  version={version}
                  testId={`gbc-material-version-${version.document_version_id}`}
                />
              ))}
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}

export default MaterialDetailVersionsTab;
