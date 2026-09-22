import assert from "node:assert/strict";

import {
  HISTORICAL_FILENAME_NOTICE,
  HISTORICAL_JOB_NOTICE,
  SEARCH_EMPTY_TEXT,
  SEARCH_IDLE_EXAMPLE,
  SEARCH_IDLE_HINT,
  presentMatchedField,
  presentMatchedFields,
  presentRelationshipResolution,
} from "../lib/materialSearchPresentation";
import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_PAGE_SIZE,
  beginSearchRequest,
  buildMaterialSearchUrl,
  createSearchSequence,
  describeSearchItem,
  isLatestSearchResponse,
  moveSelectionIndex,
  readMaterialSearchPayload,
  resolveSearchRequest,
  type MaterialSearchItem,
} from "../app/components/materials/materialSearchAdapters";

/**
 * 全局材料搜索（WP2-C）前端纯逻辑层测试。
 *
 * 这批函数承载的是"搜索结果最容易骗人"的地方，因此每条断言都对应一个具体反例：
 *
 * - 空/过短查询发请求 -> 后端 422，界面显示"搜索失败"，用户以为系统坏了；
 * - 过期响应覆盖新结果 -> 用户输入已改，界面却显示上一个词的结果（§四十一）；
 * - 命中历史文件名被显示成当前文件 -> 用户以为材料就叫这个名字（§四十五）；
 * - 空结果文案暗示"存在但无权" -> 泄露材料存在性（§四十四）；
 * - 直接写 `status === "completed"` 判定复核入口 -> 与审核工作台两套状态口径（§二十八）。
 */

// ---- 查询串 → 是否发请求 ---------------------------------------------------

assert.deepEqual(
  resolveSearchRequest(""),
  { kind: "idle" },
  "REGRESSION: 空查询必须停在等待输入，不得发请求（§四十三）",
);
assert.deepEqual(resolveSearchRequest("   "), { kind: "idle" }, "只输入空白等同空查询");
assert.deepEqual(
  resolveSearchRequest("a"),
  { kind: "invalid", reason: "至少输入 2 个字符" },
  "REGRESSION: 长度口径必须与后端一致，否则界面会显示一个必然 422 的查询",
);
assert.deepEqual(
  resolveSearchRequest(" a "),
  { kind: "invalid", reason: "至少输入 2 个字符" },
  "REGRESSION: 长度按 trim 之后算：' a ' 实际只有 1 个字符",
);
assert.deepEqual(
  resolveSearchRequest("普陀"),
  { kind: "search", q: "普陀" },
  "有效查询：发请求，且查询串已 trim",
);
assert.deepEqual(resolveSearchRequest("  财政局 本部  "), { kind: "search", q: "财政局 本部" });
assert.equal(
  resolveSearchRequest("普".repeat(201)).kind,
  "invalid",
  "超过 200 字符不发请求（与后端 max_length 同口径）",
);

// ---- debounce 与过期响应丢弃 ----------------------------------------------

assert.equal(SEARCH_DEBOUNCE_MS, 250, "REGRESSION: debounce 必须是 250ms（§四十二）");
assert.equal(SEARCH_PAGE_SIZE, 20, "默认每页 20 条（与后端 DEFAULT_SEARCH_PAGE_SIZE 同值）");

const sequence = createSearchSequence();
const firstA = beginSearchRequest(sequence); // 用户输入 A
const firstB = beginSearchRequest(sequence); // 马上改成 B
assert.notEqual(firstA, firstB, "每次请求都要拿到不同的序号");
assert.equal(
  isLatestSearchResponse(sequence, firstA),
  false,
  "REGRESSION: 慢响应 A 在后发请求 B 之后返回时必须被丢弃（§四十一）",
);
assert.equal(isLatestSearchResponse(sequence, firstB), true, "最后发出的请求可以写入状态");

const staleOne = beginSearchRequest(sequence);
const staleTwo = beginSearchRequest(sequence);
const staleThree = beginSearchRequest(sequence);
assert.equal(isLatestSearchResponse(sequence, staleTwo), false, "中间那次响应同样要丢弃");
assert.equal(isLatestSearchResponse(sequence, staleThree), true);
assert.equal(isLatestSearchResponse(sequence, staleOne + 99), false, "未知序号不得写入状态");

// ---- 键盘选择 --------------------------------------------------------------

assert.equal(moveSelectionIndex(-1, 1, 3), 0, "首次按下箭头选中第一条");
assert.equal(moveSelectionIndex(0, 1, 3), 1);
assert.equal(moveSelectionIndex(2, 1, 3), 2, "到底部不环绕（避免无意识跳到顶部）");
assert.equal(moveSelectionIndex(0, -1, 3), 0, "到顶部不环绕");
assert.equal(
  moveSelectionIndex(-1, -1, 3),
  0,
  "还没选中时按上键 -> 落在第一条（不能停在 -1，否则 Enter 没有目标）",
);
assert.equal(moveSelectionIndex(1, 1, 0), -1, "空列表恒无选中项");
assert.equal(moveSelectionIndex(-1, 1, 0), -1, "空列表恒无选中项");

// ---- 响应解析 --------------------------------------------------------------

const VALID_ITEM = {
  slot_id: "slot-1",
  slot_key: "key-1",
  jurisdiction_id: "district-pt",
  jurisdiction_name: "普陀区",
  department_org_id: "dept-1",
  department_name: "上海市普陀区规划和自然资源局",
  subject_org_id: "unit-1",
  subject_org_name: "上海市普陀区规划和自然资源局",
  subject_kind: "unit",
  material_scope: "unit_self",
  relationship: "head_unit",
  fiscal_year: 2024,
  report_kind: "final",
  caliber: "self",
  status: "review_required",
  status_reason: "findings_pending",
  applicability_status: "applicable",
  current_document_version_id: 22,
  current_filename: "current-final.pdf",
  matched_fields: ["unit", "fiscal_year", "report_kind", "relationship"],
  matched_filename: null,
  matched_document_version_id: null,
  matched_version_is_current: null,
  matched_job_uuid: null,
  matched_job_version_is_current: null,
  review_candidate: null,
  updated_at: "2026-09-22T12:00:00Z",
};

function payload(itemOverrides: Record<string, unknown> = {}, metaOverrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    data: { items: [{ ...VALID_ITEM, ...itemOverrides }] },
    meta: {
      data_basis: "existing_slots_only",
      expected_materials_ready: false,
      generated_at: "2026-09-22T12:00:00Z",
      pagination: { page: 1, page_size: 20, total: 1, total_pages: 1 },
      query: "规划和自然资源局 本部 2024 决算",
      relationship_resolution: "resolved",
      linkage_basis: "structured_document_version_id",
      legacy_unlinked_job_matches_excluded: true,
      ...metaOverrides,
    },
  };
}

const parsed = readMaterialSearchPayload(payload());
assert.ok(parsed, "合法响应必须解析成功");
assert.equal(parsed?.items.length, 1);
assert.equal(parsed?.meta.pagination.total, 1);
assert.equal(parsed?.meta.legacy_unlinked_job_matches_excluded, true);

assert.equal(
  readMaterialSearchPayload({ ok: false, data: { items: [] }, meta: {} }),
  null,
  "REGRESSION: ok=false 不是空结果，必须按错误处理（不能被当成'没有材料'）",
);
assert.equal(
  readMaterialSearchPayload({ ok: true, data: {}, meta: {} }),
  null,
  "REGRESSION: 缺少 items 数组 = 契约不符，按错误处理而不是空列表",
);
assert.equal(readMaterialSearchPayload(null), null);
assert.equal(readMaterialSearchPayload({ ok: true, data: { items: [{ slot_key: "x" }] }, meta: {} }), null, "缺 slot_id 的行视为契约不符");
assert.equal(
  readMaterialSearchPayload({ ok: true, data: { items: [] }, meta: {} })?.items.length,
  0,
  "空数组是合法的空结果",
);

// 契约漂移：缺字段按 null/空数组处理，但整行仍然可用（不因为少一个可选字段就丢结果）
const sparse = readMaterialSearchPayload({
  ok: true,
  data: { items: [{ slot_id: "slot-sparse" }] },
  meta: {},
});
assert.equal(sparse?.items[0].current_filename, null);
assert.deepEqual(sparse?.items[0].matched_fields, []);
assert.equal(sparse?.items[0].review_candidate, null);
assert.equal(sparse?.meta.pagination.total, 0);

// ---- 每条结果的展示与动作 --------------------------------------------------

function view(overrides: Record<string, unknown> = {}) {
  const item = readMaterialSearchPayload(payload(overrides))?.items[0] as MaterialSearchItem;
  return describeSearchItem(item);
}

const normalView = view();
assert.equal(normalView.openHref, "/materials/slots/slot-1", "「打开材料」恒指向槽位详情");
assert.equal(normalView.historicalFilenameNotice, null);
assert.equal(normalView.historicalJobNotice, null);
assert.equal(normalView.currentFilename, "current-final.pdf");
assert.equal(normalView.matchedReasonText, "命中：单位名称 · 财政年度 · 文种 · 主体关系");
assert.ok(normalView.metaLine.includes("2024 年度"));
assert.ok(normalView.metaLine.includes("决算"));
assert.ok(normalView.metaLine.includes("本部单位"));

// 命中历史文件名：显著标注 + 当前文件名仍然显示当前版本（§四十五/§二十二）
const historical = view({
  matched_filename: "old-final.pdf",
  matched_document_version_id: 11,
  matched_version_is_current: false,
  matched_fields: ["historical_filename"],
});
assert.equal(historical.historicalFilenameNotice, HISTORICAL_FILENAME_NOTICE);
assert.equal(
  historical.currentFilename,
  "current-final.pdf",
  "REGRESSION: 命中历史版本不得把'当前文件'显示成历史文件",
);
assert.equal(historical.openHref, "/materials/slots/slot-1", "历史命中的打开目标仍是同一个槽位");

// 命中历史版本的 job：另一个独立标记（不把两件事混成一个标签）
const historicalJob = view({
  matched_job_uuid: "job-history-001",
  matched_job_version_is_current: false,
  matched_fields: ["job_id"],
});
assert.equal(historicalJob.historicalJobNotice, HISTORICAL_JOB_NOTICE);
assert.equal(historicalJob.matchedReasonText, "命中：处理任务 ID");

// 复核入口：null 候选 -> 不给链接并给出原因（§二十九）
assert.equal(normalView.reviewHref, null);
assert.ok(normalView.reviewBlockedReason);

// 复核入口：候选状态由 resolveReviewEntryDecision 判定（复用审核工作台口径）
const reviewable = view({
  review_candidate: { job_uuid: "job-current-001", status: "done" },
});
assert.equal(reviewable.reviewHref, "/review?job=job-current-001", "已完成任务可进入复核");

const reviewing = view({
  review_candidate: { job_uuid: "job-current-001", status: "processing" },
});
assert.equal(reviewing.reviewHref, null, "REGRESSION: 还在跑的任务不得给复核入口（进去只会看到空列表）");
assert.ok(reviewing.reviewBlockedReason && reviewing.reviewBlockedReason.includes("尚未分析完成"));

const failedCandidate = view({
  review_candidate: { job_uuid: "job-current-001", status: "error" },
});
assert.equal(failedCandidate.reviewHref, null, "失败任务没有可复核结果");
assert.ok(failedCandidate.reviewBlockedReason && failedCandidate.reviewBlockedReason.includes("失败"));

// job uuid 需要编码，避免拼出坏链接
const encoded = view({ review_candidate: { job_uuid: "job/with space", status: "review_required" } });
assert.equal(encoded.reviewHref, "/review?job=job%2Fwith%20space");

// ---- 文案：不得泄露存在性、不得静默降级 -------------------------------------

assert.equal(SEARCH_EMPTY_TEXT, "没有找到符合条件且你有权限查看的材料");
assert.ok(
  !SEARCH_EMPTY_TEXT.includes("无权") || SEARCH_EMPTY_TEXT.includes("有权限"),
  "空态文案不得暗示'材料存在但你无权'",
);
assert.ok(SEARCH_IDLE_HINT.includes("文件名"));
assert.equal(SEARCH_IDLE_EXAMPLE, "规划和自然资源局 本部 2024 决算");

assert.deepEqual(
  presentRelationshipResolution("resolved"),
  { resolved: true, notice: null },
  "关系解析成功时不给额外提示（避免噪音）",
);
assert.deepEqual(presentRelationshipResolution("not_requested"), { resolved: true, notice: null });
const unavailable = presentRelationshipResolution("unavailable");
assert.equal(unavailable.resolved, false);
assert.ok(
  unavailable.notice && unavailable.notice.includes("组织目录不可用"),
  "REGRESSION: 关系解析失败必须显式说明原因，否则用户会把'筛不了'当成'没有这种材料'",
);

assert.equal(presentMatchedField("unit"), "单位名称");
assert.equal(presentMatchedField("historical_filename"), "历史文件名");
assert.equal(
  presentMatchedField("brand_new_code"),
  "brand_new_code",
  "未登记命中码原样显示（便于发现后端契约漂移）",
);
assert.equal(presentMatchedFields(["unit", "job_id"]), "单位名称 · 处理任务 ID");
assert.equal(presentMatchedFields([]), "命中原因未知");
assert.equal(presentMatchedFields(null), "命中原因未知");

// ---- URL 构造 --------------------------------------------------------------

assert.equal(
  buildMaterialSearchUrl("普陀 财政局"),
  "/api/materials/search?q=%E6%99%AE%E9%99%80%20%E8%B4%A2%E6%94%BF%E5%B1%80&page=1&page_size=20",
);
assert.equal(
  buildMaterialSearchUrl("100%完成", 2, 50),
  "/api/materials/search?q=100%25%E5%AE%8C%E6%88%90&page=2&page_size=50",
  "REGRESSION: q 必须编码，裸 % 会被当成非法转义",
);

console.log("materialSearchAdapters.test.ts passed");
