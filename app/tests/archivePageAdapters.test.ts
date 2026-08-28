import assert from "node:assert/strict";

import type { IssueWorkflowRecord, IssueWorkflowState } from "../lib/issueWorkflowTypes";
import {
  PACKAGE_CONTENT_TEXT,
  buildCreatePackagePayload,
  buildPackageDownloadBody,
  buildPackageDownloadFilename,
  deriveArchiveSummary,
  normalizeWorkflowState,
  resolvePackageStatusLabel,
  resolvePackageStatusTone,
  selectConfirmedIssues,
} from "../app/components/archive/archivePageAdapters";

// 本文件测试 Task 9 导出归档页的纯逻辑层：工作流状态归一、KPI 汇总、
// create_package 请求体拼装与整改包列表展示口径。
// 能力等价性是硬要求——这里钉住与旧单体一致的拼装语义。

function makeIssue(overrides: Partial<IssueWorkflowRecord> & { key: string; job_id: string; issue_id: string }): IssueWorkflowRecord {
  return {
    status: "confirmed",
    updated_at: "2026-08-28T10:00:00Z",
    ...overrides,
  } as IssueWorkflowRecord;
}

const CONFIRMED_A = makeIssue({
  key: "job-1::issue-1",
  job_id: "job-1",
  issue_id: "issue-1",
  organization_id: "dept-caizheng",
  organization_name: "上海市普陀区财政局",
  title: "预算分项合计与公开总额不一致",
  updated_at: "2026-08-28T10:00:00Z",
});
const CONFIRMED_B_SAME_ORG = makeIssue({
  key: "job-2::issue-9",
  job_id: "job-2",
  issue_id: "issue-9",
  organization_id: "dept-caizheng",
  organization_name: "上海市普陀区财政局",
  title: "三公经费合计不等于分项之和",
  updated_at: "2026-08-28T12:00:00Z",
});
const IN_PACKAGE = makeIssue({
  key: "job-3::issue-5",
  job_id: "job-3",
  issue_id: "issue-5",
  status: "in_package",
  organization_id: "dept-jiaoyu",
  organization_name: "上海市普陀区教育局",
  updated_at: "2026-08-27T09:00:00Z",
});
const NO_ISSUE = makeIssue({
  key: "job-1::issue-2",
  job_id: "job-1",
  issue_id: "issue-2",
  status: "no_issue",
  updated_at: "2026-08-26T08:00:00Z",
});

const SAMPLE_STATE: IssueWorkflowState = {
  issues: {
    [CONFIRMED_A.key]: CONFIRMED_A,
    [CONFIRMED_B_SAME_ORG.key]: CONFIRMED_B_SAME_ORG,
    [IN_PACKAGE.key]: IN_PACKAGE,
    [NO_ISSUE.key]: NO_ISSUE,
  },
  packages: [
    {
      id: "pkg-1",
      name: "上海市普陀区教育局整改包",
      organization_id: "dept-jiaoyu",
      organization_name: "上海市普陀区教育局",
      job_ids: ["job-3"],
      issue_keys: ["job-3::issue-5"],
      status: "ready",
      created_at: "2026-08-27T10:00:00Z",
      updated_at: "2026-08-27T10:00:00Z",
    },
  ],
};

// --- normalizeWorkflowState：坏数据跳过、非法状态归 pending --------------------

const normalized = normalizeWorkflowState(SAMPLE_STATE);
assert.equal(Object.keys(normalized.issues).length, 4);
assert.equal(normalized.packages.length, 1);
assert.equal(normalized.packages[0].name, "上海市普陀区教育局整改包");

const messy = normalizeWorkflowState({
  issues: {
    "job-x::issue-x": { job_id: "job-x", issue_id: "issue-x", status: "confirmed" },
    bad: { job_id: "", issue_id: "" },
    "job-y::issue-y": { job_id: "job-y", issue_id: "issue-y", status: "weird-status" },
  },
  packages: [{ id: "" }, { id: "pkg-2", status: "unknown", job_ids: "not-an-array" }],
});
assert.equal(Object.keys(messy.issues).length, 2, "缺 job_id/issue_id 的记录必须跳过");
assert.equal(messy.issues["job-y::issue-y"].status, "pending", "非法状态必须归一为 pending");
assert.equal(messy.packages.length, 2, "无 id 的 package 条目保留占位但其余字段归一");
assert.equal(messy.packages[1].status, "draft", "非法 package 状态归一为 draft");
assert.deepEqual(messy.packages[1].job_ids, [], "非数组 job_ids 归一为空数组");

assert.deepEqual(normalizeWorkflowState(null), { issues: {}, packages: [], updated_at: undefined });
assert.deepEqual(normalizeWorkflowState("garbage"), { issues: {}, packages: [], updated_at: undefined });

// --- deriveArchiveSummary：计数口径与旧页一致 -----------------------------------

assert.equal(deriveArchiveSummary(null), null, "REGRESSION: 状态未加载时返回 null（渲染加载态），不得当成 0");

const summary = deriveArchiveSummary(SAMPLE_STATE);
assert.deepEqual(summary, { confirmedCount: 2, packageCount: 1, inPackageCount: 1 });

const emptySummary = deriveArchiveSummary({ issues: {}, packages: [] });
assert.deepEqual(
  emptySummary,
  { confirmedCount: 0, packageCount: 0, inPackageCount: 0 },
  "空状态是真实的 0（已加载且确认无内容），不是 null——0 与 null 语义严格区分",
);

// --- selectConfirmedIssues：只取 confirmed，按更新时间倒序 ------------------------

const confirmed = selectConfirmedIssues(SAMPLE_STATE);
assert.equal(confirmed.length, 2);
assert.equal(confirmed[0].key, "job-2::issue-9", "最新确认的问题排前面");
assert.ok(confirmed.every((record) => record.status === "confirmed"));
assert.deepEqual(selectConfirmedIssues(null), []);

// --- buildCreatePackagePayload：与旧单体拼装口径一致 -----------------------------

// 同一组织：名称取组织名，组织 id/name 都落
const sameOrgPayload = buildCreatePackagePayload([CONFIRMED_A, CONFIRMED_B_SAME_ORG]);
assert.ok(sameOrgPayload);
assert.equal(sameOrgPayload.action, "create_package");
assert.equal(sameOrgPayload.name, "上海市普陀区财政局整改包");
assert.equal(sameOrgPayload.organization_id, "dept-caizheng");
assert.equal(sameOrgPayload.organization_name, "上海市普陀区财政局");
// job_ids 去重：两个问题分属两个任务，各自保留
assert.deepEqual(sameOrgPayload.job_ids, ["job-1", "job-2"]);
assert.deepEqual(sameOrgPayload.issue_keys, ["job-1::issue-1", "job-2::issue-9"]);

// 跨组织：名称"多单位整改包"，组织字段为 null
const crossOrgPayload = buildCreatePackagePayload([CONFIRMED_A, IN_PACKAGE]);
assert.ok(crossOrgPayload);
assert.equal(crossOrgPayload.name, "多单位整改包");
assert.equal(crossOrgPayload.organization_id, null);
assert.equal(crossOrgPayload.organization_name, null);

// 同一任务的多个问题：job_ids 去重
const sameJobPayload = buildCreatePackagePayload([
  CONFIRMED_A,
  makeIssue({ key: "job-1::issue-7", job_id: "job-1", issue_id: "issue-7" }),
]);
assert.ok(sameJobPayload);
assert.deepEqual(sameJobPayload.job_ids, ["job-1"], "同一任务的多个问题不得重复出现 job_id");
assert.equal(sameJobPayload.issue_keys.length, 2);

// 反例（核心断言）：空目标必须返回 null——前端不得发出空 issue_keys 的
// create_package 请求（后端会 400；更早的拦截是"不得生成空整改包"的产品语义）。
assert.equal(
  buildCreatePackagePayload([]),
  null,
  "REGRESSION: 无打包目标时必须返回 null，不得拼出空整改包请求",
);

// --- 整改包列表展示口径 ----------------------------------------------------------

assert.equal(PACKAGE_CONTENT_TEXT, "问题清单 / 证据页 / 处理状态 / 报告链接", "内容列文案与旧单体一致");

assert.equal(resolvePackageStatusLabel("ready"), "ready · 可下载");
assert.equal(resolvePackageStatusLabel("submitted"), "submitted · 已提交");
assert.equal(resolvePackageStatusLabel("draft"), "draft · 草稿");
assert.equal(resolvePackageStatusTone("ready"), "done");
assert.equal(resolvePackageStatusTone("draft"), "review");

// 下载口径：文件名「{名称}.zip」、请求体 {job_ids}（与旧单体相同）
assert.equal(buildPackageDownloadFilename(SAMPLE_STATE.packages[0]), "上海市普陀区教育局整改包.zip");
assert.equal(
  buildPackageDownloadFilename({ ...SAMPLE_STATE.packages[0], name: "" }),
  "reports-batch.zip",
  "名称缺失时退回默认文件名，不产生 .zip 前空白",
);
assert.deepEqual(buildPackageDownloadBody(SAMPLE_STATE.packages[0]), { job_ids: ["job-3"] });

console.log("archivePageAdapters.test.ts passed");
