/**
 * 全局材料搜索的类型与纯函数适配层（WP2-C）。
 *
 * 类型与后端 `src/schemas/material_search.py` 一一对应（字段名刻意不改写）。
 * 所有能在无 React 环境下测到的逻辑都放在这里，让 `jiti` 直跑的单测覆盖：
 *
 * - 查询串 → 是否发请求 / 发什么（长度口径与后端同源）；
 * - debounce 与"过期响应丢弃"（sequence 语义）；
 * - 键盘上下移动的边界；
 * - 每条结果的展示投影与两个动作（打开材料 / 进入复核）。
 *
 * 「进入复核」的判定**复用** `resolveReviewEntryDecision()`（审核工作台的唯一
 * 状态口径），本模块不另写 `status === "completed"` 这类第二套状态逻辑（§二十八）。
 */

import { resolveReviewEntryDecision } from "../workspace/workbenchAdapters";

import {
  HISTORICAL_FILENAME_NOTICE,
  HISTORICAL_JOB_NOTICE,
  SEARCH_MAX_QUERY_LENGTH,
  SEARCH_MIN_QUERY_LENGTH,
  presentMatchedFields,
} from "../../../lib/materialSearchPresentation";
import {
  presentCaliber,
  presentMaterialStatus,
  presentRelationship,
  presentReportKind,
} from "../../../lib/materialStatusPresentation";

// ---- 后端契约 --------------------------------------------------------------

export interface MaterialSearchReviewCandidate {
  job_uuid: string;
  status: string;
}

export interface MaterialSearchItem {
  slot_id: string;
  slot_key: string;
  jurisdiction_id: string | null;
  jurisdiction_name: string | null;
  department_org_id: string | null;
  department_name: string | null;
  subject_org_id: string;
  subject_org_name: string;
  subject_kind: string;
  material_scope: string;
  relationship: string;
  fiscal_year: number | null;
  report_kind: string;
  caliber: string;
  status: string;
  status_reason: string | null;
  applicability_status: string;
  current_document_version_id: number | null;
  current_filename: string | null;
  matched_fields: string[];
  matched_filename: string | null;
  matched_document_version_id: number | null;
  matched_version_is_current: boolean | null;
  matched_job_uuid: string | null;
  matched_job_version_is_current: boolean | null;
  review_candidate: MaterialSearchReviewCandidate | null;
  updated_at: string | null;
}

export interface MaterialSearchPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface MaterialSearchMeta {
  data_basis: string;
  expected_materials_ready: boolean;
  generated_at: string;
  pagination: MaterialSearchPagination;
  query: string;
  relationship_resolution: string;
  linkage_basis: string;
  legacy_unlinked_job_matches_excluded: boolean;
}

export interface MaterialSearchResult {
  items: MaterialSearchItem[];
  meta: MaterialSearchMeta;
}

/** 默认分页（与后端 `DEFAULT_SEARCH_PAGE_SIZE` 同值）。 */
export const SEARCH_PAGE_SIZE = 20;

/** debounce 时长：250ms（§四十二）。 */
export const SEARCH_DEBOUNCE_MS = 250;

const FALLBACK_PAGINATION: MaterialSearchPagination = {
  page: 1,
  page_size: SEARCH_PAGE_SIZE,
  total: 0,
  total_pages: 0,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * 响应体 → 契约对象；不符合契约返回 null。
 *
 * 返回 null 表示"响应不符合契约"，由调用方按**错误**处理，而不是按"空数据"处理：
 * 把格式错误显示成"没有找到材料"是最危险的一种假结论（与 `useMaterialResource`
 * 的同名约定一致）。
 */
export function readMaterialSearchPayload(payload: unknown): MaterialSearchResult | null {
  if (!isRecord(payload) || payload.ok !== true) {
    return null;
  }
  const data = payload.data;
  if (!isRecord(data) || !Array.isArray(data.items)) {
    return null;
  }
  const items: MaterialSearchItem[] = [];
  for (const raw of data.items) {
    if (!isRecord(raw) || typeof raw.slot_id !== "string" || !raw.slot_id) {
      return null;
    }
    items.push({
      slot_id: raw.slot_id,
      slot_key: String(raw.slot_key ?? ""),
      jurisdiction_id: (raw.jurisdiction_id as string | null) ?? null,
      jurisdiction_name: (raw.jurisdiction_name as string | null) ?? null,
      department_org_id: (raw.department_org_id as string | null) ?? null,
      department_name: (raw.department_name as string | null) ?? null,
      subject_org_id: String(raw.subject_org_id ?? ""),
      subject_org_name: String(raw.subject_org_name ?? ""),
      subject_kind: String(raw.subject_kind ?? "unknown"),
      material_scope: String(raw.material_scope ?? "unknown"),
      relationship: String(raw.relationship ?? "relationship_unknown"),
      fiscal_year: typeof raw.fiscal_year === "number" ? raw.fiscal_year : null,
      report_kind: String(raw.report_kind ?? "unknown"),
      caliber: String(raw.caliber ?? "unknown"),
      status: String(raw.status ?? ""),
      status_reason: (raw.status_reason as string | null) ?? null,
      applicability_status: String(raw.applicability_status ?? "applicable"),
      current_document_version_id:
        typeof raw.current_document_version_id === "number"
          ? raw.current_document_version_id
          : null,
      current_filename: (raw.current_filename as string | null) ?? null,
      matched_fields: Array.isArray(raw.matched_fields)
        ? raw.matched_fields.map((code) => String(code))
        : [],
      matched_filename: (raw.matched_filename as string | null) ?? null,
      matched_document_version_id:
        typeof raw.matched_document_version_id === "number"
          ? raw.matched_document_version_id
          : null,
      matched_version_is_current:
        typeof raw.matched_version_is_current === "boolean"
          ? raw.matched_version_is_current
          : null,
      matched_job_uuid: (raw.matched_job_uuid as string | null) ?? null,
      matched_job_version_is_current:
        typeof raw.matched_job_version_is_current === "boolean"
          ? raw.matched_job_version_is_current
          : null,
      review_candidate: isRecord(raw.review_candidate) && typeof raw.review_candidate.job_uuid === "string"
        ? {
            job_uuid: raw.review_candidate.job_uuid,
            status: String(raw.review_candidate.status ?? ""),
          }
        : null,
      updated_at: (raw.updated_at as string | null) ?? null,
    });
  }

  const meta = isRecord(payload.meta) ? payload.meta : {};
  const pagination = isRecord(meta.pagination) ? meta.pagination : {};
  return {
    items,
    meta: {
      data_basis: String(meta.data_basis ?? ""),
      expected_materials_ready: meta.expected_materials_ready === true,
      generated_at: String(meta.generated_at ?? ""),
      pagination: {
        page: Number(pagination.page ?? FALLBACK_PAGINATION.page),
        page_size: Number(pagination.page_size ?? FALLBACK_PAGINATION.page_size),
        total: Number(pagination.total ?? 0),
        total_pages: Number(pagination.total_pages ?? 0),
      },
      query: String(meta.query ?? ""),
      relationship_resolution: String(meta.relationship_resolution ?? "not_requested"),
      linkage_basis: String(meta.linkage_basis ?? ""),
      legacy_unlinked_job_matches_excluded: meta.legacy_unlinked_job_matches_excluded === true,
    },
  };
}

/** 搜索请求 URL。q 由调用方保证已 trim；这里只做参数编码。 */
export function buildMaterialSearchUrl(q: string, page = 1, pageSize = SEARCH_PAGE_SIZE): string {
  return `/api/materials/search?q=${encodeURIComponent(q)}&page=${page}&page_size=${pageSize}`;
}

// ---- 何时发请求 ------------------------------------------------------------

export type SearchRequestDecision =
  | { kind: "idle" }
  | { kind: "invalid"; reason: string }
  | { kind: "search"; q: string };

/**
 * 输入 → 是否发请求。三态而不是布尔，是因为"还没输入"与"输入不合法"要分开：
 *
 * - ``idle``：空/只有空白 —— 显示引导文案，**不发请求**（§四十三）；
 * - ``invalid``：trim 后短于 2 个字符或超过 200 —— 显示原因，不发请求
 *   （长度口径与后端一致，避免"面板说能搜、后端 422"）；
 * - ``search``：可以发。
 */
export function resolveSearchRequest(rawQuery: string): SearchRequestDecision {
  const q = String(rawQuery ?? "").trim();
  if (q.length === 0) {
    return { kind: "idle" };
  }
  if (q.length < SEARCH_MIN_QUERY_LENGTH) {
    return { kind: "invalid", reason: `至少输入 ${SEARCH_MIN_QUERY_LENGTH} 个字符` };
  }
  if (q.length > SEARCH_MAX_QUERY_LENGTH) {
    return { kind: "invalid", reason: `最多 ${SEARCH_MAX_QUERY_LENGTH} 个字符` };
  }
  return { kind: "search", q };
}

// ---- 过期响应丢弃（§四十一） ----------------------------------------------

export interface SearchSequence {
  latest: number;
}

export function createSearchSequence(): SearchSequence {
  return { latest: 0 };
}

/** 发一次请求前登记序号：返回值就是这次请求的 id。 */
export function beginSearchRequest(sequence: SearchSequence): number {
  sequence.latest += 1;
  return sequence.latest;
}

/**
 * 响应/错误是否可以落到界面上。
 *
 * 只有当它就是**最后一次**发出的请求时才允许写入状态：慢响应（A）在后发的
 * 快响应（B）之后返回时会被丢弃，界面因此始终显示用户最后输入的那个词。
 */
export function isLatestSearchResponse(sequence: SearchSequence, requestId: number): boolean {
  return requestId === sequence.latest;
}

// ---- 键盘选择（§四十） ----------------------------------------------------

/**
 * 上下键移动选中项：**不环绕**，到边界停住。
 *
 * ``current`` 用 -1 表示"还没选中任何一条"（首次打开面板的状态）。
 * 空列表恒返回 -1，避免出现"选中了不存在的一条"。
 */
export function moveSelectionIndex(current: number, delta: number, length: number): number {
  if (length <= 0) {
    return -1;
  }
  const base = Number.isFinite(current) ? Math.trunc(current) : -1;
  const next = base + Math.trunc(delta);
  if (next < 0) {
    return 0;
  }
  if (next > length - 1) {
    return length - 1;
  }
  return next;
}

// ---- 一条结果的展示投影与动作 ----------------------------------------------

export interface MaterialSearchItemView {
  /** 主体名（列表主标题）。 */
  title: string;
  /** 主管部门 / 区县（副标题）。 */
  subtitle: string;
  /** 年度 · 文种 · 口径 · 状态 · 关系。 */
  metaLine: string;
  currentFilename: string | null;
  /** 命中原因文案（"命中：单位名称 · 财政年度"）。 */
  matchedReasonText: string;
  /** 命中历史文件名时的显著提示；非历史命中为 null。 */
  historicalFilenameNotice: string | null;
  /** 命中历史版本处理任务时的显著提示。 */
  historicalJobNotice: string | null;
  /** 「打开材料」的 href —— 恒指向槽位详情，与当前/历史命中无关（§二十二）。 */
  openHref: string;
  /** 「进入复核」的 href；不可用时为 null。 */
  reviewHref: string | null;
  /** 「进入复核」不可用的原因（可点击时为 null）。 */
  reviewBlockedReason: string | null;
}

export function describeSearchItem(item: MaterialSearchItem): MaterialSearchItemView {
  const subject = item.subject_org_name || "未识别主体";
  const department = item.department_name || "未识别主管部门";
  const district = item.jurisdiction_name || "未识别区县";
  const year = item.fiscal_year === null ? "年度未识别" : `${item.fiscal_year} 年度`;
  const relationship = presentRelationship(item.relationship);
  const status = presentMaterialStatus(item.status).label;

  // 命中历史文件名时**必须**显著标注：否则用户会以为当前材料就叫这个名字。
  const historicalFilenameNotice =
    item.matched_version_is_current === false ? HISTORICAL_FILENAME_NOTICE : null;
  const historicalJobNotice =
    item.matched_job_version_is_current === false ? HISTORICAL_JOB_NOTICE : null;

  // 「进入复核」= 后端给了候选（权限已过） + 前端状态判定通过（§二十八）。
  let reviewHref: string | null = null;
  let reviewBlockedReason: string | null = null;
  const candidate = item.review_candidate;
  if (!candidate) {
    reviewBlockedReason = "这条材料当前没有可进入复核的处理任务（或你无权访问该任务）";
  } else {
    const decision = resolveReviewEntryDecision({
      job_id: candidate.job_uuid,
      status: candidate.status,
    });
    if (decision.canEnter) {
      reviewHref = `/review?job=${encodeURIComponent(candidate.job_uuid)}`;
    } else {
      reviewBlockedReason = decision.reason;
    }
  }

  return {
    title: subject,
    subtitle: `${department} · ${district}`,
    metaLine: `${year} · ${presentReportKind(item.report_kind)} · ${presentCaliber(
      item.caliber,
    )} · ${status} · ${relationship}`,
    currentFilename: item.current_filename,
    matchedReasonText: `命中：${presentMatchedFields(item.matched_fields)}`,
    historicalFilenameNotice,
    historicalJobNotice,
    openHref: `/materials/slots/${encodeURIComponent(item.slot_id)}`,
    reviewHref,
    reviewBlockedReason,
  };
}
