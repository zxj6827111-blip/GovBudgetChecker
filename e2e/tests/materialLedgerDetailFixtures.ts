import type { Page } from "../../app/node_modules/playwright/test";

/**
 * WP2-B（单位时间轴 + 材料详情）e2e 的共享 mock 夹具。
 *
 * 为什么抽成一个非 `.spec.ts` 的文件
 * ----------------------------------
 * 功能用例与截图采集两套 spec 必须用**同一份**数据：如果各自维护一份 mock，
 * "截图里的样子"和"测试断言的样子"就会开始漂移，截图作为交付证据的价值
 * 也随之消失。文件名不带 `.spec.ts`，Playwright 不会把它当测试文件收集。
 *
 * 数据只覆盖口径上真正有区别的形态：
 * - 2024 / 2025 / 2026 三个年度 + 一个年度未识别；
 * - 2024 决算下**两条**槽位（正式 + 待确认占位）—— 验证"多槽位不被静默吃掉"；
 * - 一个真实 `missing` 槽位（有历史版本）；
 * - 一个 `not_due + due_at_unknown` 槽位；
 * - 一个"V2 是当前版本但 V2 没有分析、V1 有 3 个 finding"的槽位。
 */

export const DISTRICT_ID = "district-pt";
export const DEPT_ID = "dept-ghzy";
export const HEAD_UNIT_ID = "unit-ghzy-head";
export const SUB_UNIT_ID = "unit-ghzy-enforcement";

export const SLOT_MAIN = "slot-head-final-2024";
export const SLOT_MULTI_PRIMARY = "slot-head-budget-2024";
export const SLOT_MULTI_EXTRA = "slot-head-budget-2024-placeholder";
export const SLOT_YEAR_UNKNOWN = "slot-head-year-unknown";
export const SLOT_MISSING = "slot-sub-final-2025-missing";
export const SLOT_DUE_UNKNOWN = "slot-head-budget-2026-notdue";
export const SLOT_VERSION_REPLACED = "slot-enforcement-final-2024";
export const SLOT_NO_ANALYSIS = "slot-sub-final-2025-processing";

export const UNIT_NAME = "上海市普陀区规划和自然资源局本级";
export const DEPT_NAME = "上海市普陀区规划和自然资源局";

export function meta(extra: Record<string, unknown> = {}) {
  return {
    data_basis: "existing_slots_only",
    expected_materials_ready: false,
    generated_at: "2026-09-21T12:00:00Z",
    linkage_basis: "structured_document_version_id",
    legacy_unlinked_runs_excluded: true,
    ...extra,
  };
}

export function slotSummary(overrides: Record<string, unknown>) {
  return {
    slot_id: "slot-x",
    slot_key: "key-x",
    jurisdiction_id: DISTRICT_ID,
    jurisdiction_name: "普陀区",
    department_org_id: DEPT_ID,
    department_name: DEPT_NAME,
    subject_org_id: HEAD_UNIT_ID,
    subject_org_name: UNIT_NAME,
    subject_kind: "unit",
    material_scope: "unit_self",
    fiscal_year: 2024,
    report_kind: "final",
    caliber: "self",
    caliber_conflict_candidate: null,
    status: "review_required",
    status_reason: "findings_pending",
    applicability_status: "applicable",
    applicability_note: null,
    due_at: "2025-08-31T00:00:00Z",
    current_document_version_id: 22,
    formal_issue_count: null,
    updated_at: "2026-09-20T15:38:00Z",
    ...overrides,
  };
}

// ---- 08 部门矩阵 ------------------------------------------------------------

export const MATRIX_BODY = {
  ok: true,
  data: {
    department: {
      department_id: DEPT_ID,
      department_name: DEPT_NAME,
      jurisdiction_id: DISTRICT_ID,
      jurisdiction_name: "普陀区",
    },
    fiscal_year: 2024,
    groups: {
      department_summary: [
        {
          subject_org_id: DEPT_ID,
          subject_org_name: DEPT_NAME,
          subject_kind: "department",
          relationship: "department_summary",
          budget: {
            exists: true,
            slot: slotSummary({
              slot_id: "slot-summary-budget-2024",
              subject_org_id: DEPT_ID,
              subject_org_name: DEPT_NAME,
              subject_kind: "department",
              material_scope: "department_summary",
              report_kind: "budget",
              status: "completed",
              status_reason: "review_completed",
              current_document_version_id: 7,
            }),
          },
          final: { exists: false, slot: null },
          unclassified: { exists: false, slot: null },
        },
      ],
      head_unit: [
        {
          subject_org_id: HEAD_UNIT_ID,
          subject_org_name: UNIT_NAME,
          subject_kind: "unit",
          relationship: "head_unit",
          budget: {
            exists: true,
            slot: slotSummary({
              slot_id: SLOT_MULTI_PRIMARY,
              report_kind: "budget",
              status: "uploaded",
              status_reason: "awaiting_analysis",
              current_document_version_id: 11,
            }),
          },
          final: {
            exists: true,
            slot: slotSummary({ slot_id: SLOT_MAIN }),
          },
          unclassified: { exists: false, slot: null },
        },
      ],
      subordinate_units: [
        {
          subject_org_id: SUB_UNIT_ID,
          subject_org_name: "上海市普陀区规划和自然资源局执法大队",
          subject_kind: "unit",
          relationship: "subordinate_unit",
          budget: { exists: false, slot: null },
          final: {
            exists: true,
            slot: slotSummary({
              slot_id: SLOT_VERSION_REPLACED,
              subject_org_id: SUB_UNIT_ID,
              subject_org_name: "上海市普陀区规划和自然资源局执法大队",
              status: "processing",
              status_reason: "analysis_running",
              current_document_version_id: 42,
            }),
          },
          unclassified: { exists: false, slot: null },
        },
      ],
      relationship_unknown: [],
    },
  },
  meta: meta(),
};

// ---- 09 单位时间轴 ----------------------------------------------------------

export const TIMELINE_BODY = {
  ok: true,
  data: {
    unit: {
      unit_id: HEAD_UNIT_ID,
      unit_name: UNIT_NAME,
      subject_kind: "unit",
      department_id: DEPT_ID,
      department_name: DEPT_NAME,
      jurisdiction_id: DISTRICT_ID,
      jurisdiction_name: "普陀区",
    },
    years: [
      {
        fiscal_year: 2026,
        budget_slots: [
          slotSummary({
            slot_id: SLOT_DUE_UNKNOWN,
            fiscal_year: 2026,
            report_kind: "budget",
            status: "not_due",
            status_reason: "due_at_unknown",
            due_at: null,
            current_document_version_id: null,
          }),
        ],
        final_slots: [],
        unclassified_slots: [],
      },
      {
        fiscal_year: 2025,
        budget_slots: [],
        final_slots: [],
        unclassified_slots: [
          slotSummary({
            slot_id: SLOT_NO_ANALYSIS,
            fiscal_year: 2025,
            report_kind: "unknown",
            status: "mapping_required",
            status_reason: "identity_unresolved",
            mapping_hint: "占位",
            current_document_version_id: null,
          }),
        ],
      },
      {
        fiscal_year: 2024,
        budget_slots: [
          slotSummary({
            slot_id: SLOT_MULTI_PRIMARY,
            report_kind: "budget",
            status: "uploaded",
            status_reason: "awaiting_analysis",
            current_document_version_id: 11,
          }),
          slotSummary({
            slot_id: SLOT_MULTI_EXTRA,
            report_kind: "budget",
            status: "mapping_required",
            status_reason: "identity_unresolved",
            mapping_key: "sha256:placeholder",
            current_document_version_id: null,
          }),
        ],
        final_slots: [slotSummary({ slot_id: SLOT_MAIN })],
        unclassified_slots: [],
      },
    ],
    unresolved_year_slots: [
      slotSummary({
        slot_id: SLOT_YEAR_UNKNOWN,
        fiscal_year: null,
        report_kind: "budget",
        status: "mapping_required",
        status_reason: "identity_unresolved",
        mapping_key: "sha256:year-unknown",
        current_document_version_id: null,
      }),
    ],
  },
  meta: meta(),
};

// ---- 10-12 材料详情 ---------------------------------------------------------

const SOURCE_OFFICIAL = {
  source_id: "source-official",
  source_kind: "official_site",
  source_url: "https://www.shpt.gov.cn/xxgk/2024-final.html",
  source_page_title: "规划和自然资源局（本部）财政信息页",
  source_site: "普陀区政府门户网站",
  published_at: "2025-08-20T00:00:00Z",
  discovered_at: "2026-09-20T15:38:00Z",
  last_checked_at: null,
  source_page_hash: null,
  status: "active",
};

const SOURCE_MANUAL = {
  source_id: "source-manual",
  source_kind: "manual_upload",
  source_url: null,
  source_page_title: null,
  source_site: null,
  published_at: null,
  discovered_at: "2026-09-18T10:12:00Z",
  last_checked_at: null,
  source_page_hash: null,
  status: "active",
};

const CURRENT_VERSION = {
  document_version_id: 22,
  document_id: 3,
  file_hash: "a8c9" + "f".repeat(56) + "91ef",
  original_filename: "2024年度单位决算.pdf",
  file_size_bytes: 2048 * 512,
  content_type: "application/pdf",
  storage_backend: "filesystem",
  created_at: "2026-09-20T15:38:00Z",
  is_current: true,
  preview_url: null,
  download_url: null,
};

const RUN_V2 = {
  job_uuid: "job-240920-02",
  document_version_id: 22,
  is_current_document_version: true,
  status: "review_required",
  mode: "dual",
  started_at: "2026-09-20T15:40:00Z",
  completed_at: "2026-09-20T15:42:00Z",
  created_at: "2026-09-20T15:39:00Z",
  updated_at: "2026-09-20T15:42:00Z",
  ai_findings_count: 6,
  rule_findings_count: 2,
  merged_findings_count: 6,
  has_results: true,
  structured_ingest_status: "done",
  elapsed_total_ms: 12345,
  error_summary: null,
  analysis_status: "review_required",
  quality_status: "review_required",
  analysis_conclusion: "findings_detected",
  obligation_catalog_version: "v3.3",
  formal_issue_count: null,
  linkage_basis: "structured_document_version_id",
};

const RUN_V1 = {
  ...RUN_V2,
  job_uuid: "job-240918-01",
  document_version_id: 11,
  is_current_document_version: false,
  completed_at: "2026-09-18T10:14:00Z",
  started_at: "2026-09-18T10:12:00Z",
  created_at: "2026-09-18T10:11:00Z",
  updated_at: "2026-09-18T10:14:00Z",
  ai_findings_count: 5,
  rule_findings_count: 0,
  merged_findings_count: 5,
  obligation_catalog_version: "v3.2",
};

const FINDING_FORMAL_HIGH = {
  finding_id: "v2-f1",
  title: "表内金额勾稽不一致",
  message: "决算表合计与分项之和不一致。",
  severity: "high",
  severity_bucket: "error",
  source: "rule",
  rule_id: "V33-121",
  obligation_ids: ["OBL-T-004"],
  evidence_page: 15,
  evidence_bbox: [100, 220, 420, 300],
  evidence_text: "合计 35.20",
  evidence_status: "complete",
  evidence_missing: [],
  tags: [],
  why_not: null,
};

const FINDING_FORMAL_MEDIUM = {
  finding_id: "v2-f2",
  title: "说明与表格金额不一致",
  message: null,
  severity: "medium",
  severity_bucket: "warn",
  source: "rule",
  rule_id: "V33-233",
  obligation_ids: [],
  evidence_page: 28,
  evidence_bbox: null,
  evidence_text: null,
  evidence_status: "complete",
  evidence_missing: ["missing_bbox"],
  tags: [],
  why_not: null,
};

const FINDING_MANUAL = {
  finding_id: "v2-f3",
  title: "缺证据的候选问题",
  message: null,
  severity: "manual_review",
  severity_bucket: "warn",
  source: "ai",
  rule_id: null,
  obligation_ids: [],
  evidence_page: null,
  evidence_bbox: null,
  evidence_text: null,
  evidence_status: "degraded_missing_evidence",
  evidence_missing: ["missing_page"],
  tags: [],
  why_not: "EVIDENCE_INCOMPLETE: missing_page",
};

const FINDING_INFO = {
  finding_id: "v2-f4",
  title: "科目名称写法与标准表述略有差异",
  message: null,
  severity: "info",
  severity_bucket: "info",
  source: "rule",
  rule_id: "V33-001",
  obligation_ids: [],
  evidence_page: null,
  evidence_bbox: null,
  evidence_text: null,
  evidence_status: "complete",
  evidence_missing: ["missing_page"],
  tags: [],
  why_not: null,
};

export const COVERAGE_AVAILABLE = {
  available: true,
  reason: null,
  summary: {
    catalog_version: "v3.3",
    catalog_fingerprint: "fp-e2e",
    applicable_total: 42,
    completed_total: 26,
    not_applicable_total: 1,
    unresolved_total: 16,
    blocking_total: 15,
    coverage_rate: 0.619,
    auto_completion_rate: 0.6429,
    by_reason: { not_implemented: 12, insufficient_data: 3, ai_not_run: 1 },
    by_group: [
      {
        group_id: "table_internal",
        group_title: "表内关系",
        applicable: 7,
        completed: 6,
        not_applicable: 0,
        unresolved: 1,
      },
      {
        group_id: "table_cross",
        group_title: "表间关系",
        applicable: 7,
        completed: 5,
        not_applicable: 0,
        unresolved: 2,
      },
      {
        group_id: "three_public",
        group_title: "三公经费",
        applicable: 4,
        completed: 2,
        not_applicable: 0,
        unresolved: 2,
      },
    ],
  },
  items: [
    {
      obligation_id: "OBL-T-004",
      group_id: "table_internal",
      group_title: "表内关系",
      title: "表内合计与分项一致",
      status: "insufficient_data",
      reason: "insufficient_data",
      reason_label: "取数不足",
      detail: "未能定位合计行",
      blocks_gate: true,
      requires_ai: false,
      input_gaps: ["table_total"],
    },
    {
      obligation_id: "OBL-T-101",
      group_id: "table_cross",
      group_title: "表间关系",
      title: "表间勾稽一致",
      status: "not_implemented",
      reason: "not_implemented",
      reason_label: "自动检查暂不可用",
      detail: null,
      blocks_gate: true,
      requires_ai: false,
      input_gaps: [],
    },
    {
      obligation_id: "OBL-C-001",
      group_id: "three_public",
      group_title: "三公经费",
      title: "三公经费说明完整",
      status: "ai_not_run",
      reason: "ai_not_run",
      reason_label: "语义检查未执行",
      detail: null,
      blocks_gate: true,
      requires_ai: true,
      input_gaps: [],
    },
    {
      obligation_id: "OBL-T-005",
      group_id: "table_internal",
      group_title: "表内关系",
      title: "金额单位一致",
      status: "completed",
      reason: null,
      reason_label: null,
      detail: null,
      blocks_gate: false,
      requires_ai: false,
      input_gaps: [],
    },
  ],
};

export const COVERAGE_UNAVAILABLE = {
  available: false,
  reason: "no_coverage_for_current_document_version",
  summary: null,
  items: [],
};

export const DETAIL_BODY = {
  ok: true,
  data: {
    slot: slotSummary({ slot_id: SLOT_MAIN }),
    current_version: CURRENT_VERSION,
    historical_version_count: 1,
    version_total: 2,
    sources: [SOURCE_OFFICIAL, SOURCE_MANUAL],
    current_analysis: {
      available: true,
      reason: null,
      run: RUN_V2,
      formal_findings: [FINDING_FORMAL_HIGH, FINDING_FORMAL_MEDIUM],
      manual_review_items: [FINDING_MANUAL],
      info_findings: [FINDING_INFO],
      // 与后端权威口径一致：没有被 evidence guard 降级就是正式 finding，
      // info 也算。因此总计 = 2（error/warn）+ 1（info）= 3，
      // 而不是"正式问题那一栏"的 2。mock 必须自洽，否则页面口径断言没有意义。
      formal_issue_count: 3,
      coverage: COVERAGE_AVAILABLE,
    },
  },
  meta: meta(),
};

/** V1 有 3 个 finding、V2 是当前版本且**没有**分析结果。 */
export const DETAIL_VERSION_REPLACED_BODY = {
  ok: true,
  data: {
    slot: slotSummary({
      slot_id: SLOT_VERSION_REPLACED,
      subject_org_id: SUB_UNIT_ID,
      subject_org_name: "上海市普陀区规划和自然资源局执法大队",
      status: "processing",
      status_reason: "analysis_running",
    }),
    current_version: {
      ...CURRENT_VERSION,
      document_version_id: 42,
      original_filename: "2024年度单位决算（第二次上传）.pdf",
      file_hash: "b" + "1".repeat(62),
    },
    historical_version_count: 1,
    version_total: 2,
    sources: [SOURCE_OFFICIAL],
    current_analysis: {
      available: false,
      reason: "no_persisted_analysis_for_current_version",
      run: { ...RUN_V2, job_uuid: "job-v2-pending", document_version_id: 42, has_results: false },
      formal_findings: null,
      manual_review_items: null,
      info_findings: null,
      formal_issue_count: null,
      coverage: COVERAGE_UNAVAILABLE,
    },
  },
  meta: meta(),
};

/** 真实缺失态：status=missing、无当前版本、但有历史版本。 */
export const DETAIL_MISSING_BODY = {
  ok: true,
  data: {
    slot: slotSummary({
      slot_id: SLOT_MISSING,
      subject_org_id: SUB_UNIT_ID,
      subject_org_name: "上海市普陀区规划和自然资源局执法大队",
      fiscal_year: 2025,
      report_kind: "final",
      status: "missing",
      status_reason: "due_exceeded",
      due_at: "2026-08-31T00:00:00Z",
      current_document_version_id: null,
    }),
    current_version: null,
    historical_version_count: 1,
    version_total: 1,
    sources: [SOURCE_OFFICIAL],
    current_analysis: {
      available: false,
      reason: "no_current_document_version",
      run: null,
      formal_findings: null,
      manual_review_items: null,
      info_findings: null,
      formal_issue_count: null,
      coverage: COVERAGE_UNAVAILABLE,
    },
  },
  meta: meta(),
};

/** 截止时间未知：status=not_due + due_at_unknown，绝不能显示成缺失。 */
export const DETAIL_DUE_UNKNOWN_BODY = {
  ok: true,
  data: {
    slot: slotSummary({
      slot_id: SLOT_DUE_UNKNOWN,
      fiscal_year: 2026,
      report_kind: "budget",
      status: "not_due",
      status_reason: "due_at_unknown",
      due_at: null,
      current_document_version_id: null,
    }),
    current_version: null,
    historical_version_count: 0,
    version_total: 0,
    sources: [SOURCE_MANUAL],
    current_analysis: {
      available: false,
      reason: "no_current_document_version",
      run: null,
      formal_findings: null,
      manual_review_items: null,
      info_findings: null,
      formal_issue_count: null,
      coverage: COVERAGE_UNAVAILABLE,
    },
  },
  meta: meta(),
};

export const VERSIONS_BODY = {
  ok: true,
  data: {
    slot_id: SLOT_MAIN,
    current_document_version_id: 22,
    items: [
      CURRENT_VERSION,
      {
        ...CURRENT_VERSION,
        document_version_id: 11,
        file_hash: "73bd" + "c".repeat(56) + "c21a",
        original_filename: "2024年度单位决算（初版）.pdf",
        created_at: "2026-09-18T10:12:00Z",
        is_current: false,
      },
    ],
  },
  meta: meta(),
};

export const VERSIONS_VERSION_REPLACED_BODY = {
  ok: true,
  data: {
    slot_id: SLOT_VERSION_REPLACED,
    current_document_version_id: 42,
    items: [
      {
        ...CURRENT_VERSION,
        document_version_id: 42,
        is_current: true,
        created_at: "2026-09-20T15:38:00Z",
      },
      {
        ...CURRENT_VERSION,
        document_version_id: 11,
        is_current: false,
        created_at: "2026-09-18T10:12:00Z",
        original_filename: "2024年度单位决算.pdf",
      },
    ],
  },
  meta: meta(),
};

export const VERSIONS_MISSING_BODY = {
  ok: true,
  data: {
    slot_id: SLOT_MISSING,
    current_document_version_id: null,
    items: [
      {
        ...CURRENT_VERSION,
        document_version_id: 33,
        is_current: false,
        original_filename: "2024年度单位决算（历史）.pdf",
      },
    ],
  },
  meta: meta(),
};

export const RUNS_BODY = {
  ok: true,
  data: { slot_id: SLOT_MAIN, items: [RUN_V2, RUN_V1] },
  meta: meta(),
};

export const RUNS_VERSION_REPLACED_BODY = {
  ok: true,
  data: {
    slot_id: SLOT_VERSION_REPLACED,
    items: [
      { ...RUN_V1, document_version_id: 11, is_current_document_version: false },
    ],
  },
  meta: meta(),
};

export const RUNS_EMPTY_BODY = {
  ok: true,
  data: { slot_id: SLOT_DUE_UNKNOWN, items: [] },
  meta: meta(),
};

// ---- mock 安装 --------------------------------------------------------------

export interface DetailMockOptions {
  detail?: Record<string, unknown>;
  versions?: Record<string, unknown>;
  runs?: Record<string, unknown>;
  timeline?: Record<string, unknown>;
}

/**
 * 安装 WP2-B 的接口 mock。
 *
 * 用前缀/后缀匹配而不是写死 id：用例会构造多个槽位 id，
 * 写死会让它们落到兜底响应上，表现成"页面渲染不出来"而不是"断言失败"。
 */
export async function installDetailMocks(page: Page, options: DetailMockOptions = {}) {
  const detail = options.detail ?? DETAIL_BODY;
  const versions = options.versions ?? VERSIONS_BODY;
  const runs = options.runs ?? RUNS_BODY;
  const timeline = options.timeline ?? TIMELINE_BODY;
  // 按槽位 id 分派的详情表：同一个用例里可能命中不同形态的槽位。
  const detailBySlot: Record<string, Record<string, unknown>> = {
    [SLOT_MAIN]: detail,
    [SLOT_VERSION_REPLACED]: DETAIL_VERSION_REPLACED_BODY,
    [SLOT_MISSING]: DETAIL_MISSING_BODY,
    [SLOT_DUE_UNKNOWN]: DETAIL_DUE_UNKNOWN_BODY,
  };
  const versionsBySlot: Record<string, Record<string, unknown>> = {
    [SLOT_MAIN]: versions,
    [SLOT_VERSION_REPLACED]: VERSIONS_VERSION_REPLACED_BODY,
    [SLOT_MISSING]: VERSIONS_MISSING_BODY,
  };
  const runsBySlot: Record<string, Record<string, unknown>> = {
    [SLOT_MAIN]: runs,
    [SLOT_VERSION_REPLACED]: RUNS_VERSION_REPLACED_BODY,
    [SLOT_DUE_UNKNOWN]: RUNS_EMPTY_BODY,
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
      });
      return;
    }
    if (path === "/api/health") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
      return;
    }
    if (path.startsWith("/api/materials/units/") && path.endsWith("/timeline")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(timeline),
      });
      return;
    }
    if (path.startsWith("/api/materials/departments/") && path.endsWith("/matrix")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MATRIX_BODY),
      });
      return;
    }
    if (path.startsWith("/api/materials/slots/")) {
      const rest = path.slice("/api/materials/slots/".length).split("/");
      const slotId = decodeURIComponent(rest[0] ?? "");
      const kind = rest[1] ?? "";
      const body =
        kind === "versions"
          ? versionsBySlot[slotId] ?? { ...VERSIONS_BODY, data: { ...VERSIONS_BODY.data, slot_id: slotId, current_document_version_id: null, items: [] } }
          : kind === "runs"
            ? runsBySlot[slotId] ?? { ...RUNS_EMPTY_BODY, data: { slot_id: slotId, items: [] } }
            : detailBySlot[slotId] ?? DETAIL_BODY;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

/** 会话 cookie：与 WP2-A 的 e2e 一致，中间件只判断存在性。 */
export const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};
