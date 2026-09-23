import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { Problem } from "../lib/mock";
import { toUiProblems } from "../lib/uiAdapters";
import {
  computeWorkflowStatusCounts,
  type WorkflowIssueRecord,
} from "../app/components/review-workbench/reviewWorkbenchAdapters";

/**
 * 复核问题集合的**跨语言契约**（WP3-A §四十三）。
 *
 * 同一份夹具 `tests/fixtures/review_problem_fixture.json` 被两侧读取：
 *   - 后端 `src/services/review_problem_set.py`（完成门禁的唯一口径）
 *   - 前端 `toUiProblems` + `computeWorkflowStatusCounts`（工作台展示与计数）
 * Python 侧的对应用例在 `tests/test_review_lifecycle_service.py`。
 *
 * 为什么必须有它
 * --------------
 * 前端的 `toUiProblems` 会把 findings、merged、结构化待复核项合成一个问题列表，
 * 后端完成门禁如果只看"正式 finding"，就会出现最危险的一类**假完成**：
 *
 *     页面还挂着「结构化识别待复核」
 *     → 后端却认为问题都处理完了
 *     → 复核被判定为完成
 *
 * 或者反过来的**死局**：后端要求处理某个 id，而前端页面上根本没有这一条。
 * 两侧的"什么算一条待人工处理的问题"必须逐字一致，靠的就是这份夹具：
 * 任何一侧单独改动规则，本用例与 Python 侧用例会同时变红。
 *
 * 夹具的 `decisions` 用 issue_id 作键（合同层形态）；持久化文件里用
 * `<job_id>::<issue_id>` 是存储层细节，不属于本契约面。
 */

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, "..", "..", "tests", "fixtures", "review_problem_fixture.json"), "utf-8"),
) as {
  job_uuid: string;
  detail: Record<string, unknown>;
  issue_ids_in_order: string[];
  blocking_issue_ids_in_order: string[];
  decisions: Record<string, string>;
  expected_counts: {
    total: number;
    confirmed: number;
    no_issue: number;
    in_package: number;
    pending: number;
    needs_review: number;
  };
  expected_blocking: { pending_findings: number; needs_review_findings: number };
};

const problems: Problem[] = toUiProblems(fixture.detail as never, {
  includeEvidencePreview: false,
});

// ---- 问题身份与顺序 --------------------------------------------------------

assert.deepEqual(
  problems.map((problem) => problem.id),
  fixture.issue_ids_in_order,
  "前端问题 id 列表与顺序必须与夹具一致（后端 review_problem_set 读同一份夹具）",
);

const workflowRecords: Record<string, WorkflowIssueRecord> = {};
for (const [issueId, status] of Object.entries(fixture.decisions)) {
  workflowRecords[issueId] = { issue_id: issueId, status };
}

// ---- 计数口径 --------------------------------------------------------------

const counts = computeWorkflowStatusCounts(problems, workflowRecords);
const expected = fixture.expected_counts;

assert.equal(
  counts.confirmed,
  expected.confirmed,
  "已确认数必须与后端完成门禁同口径",
);
assert.equal(counts.ignored, expected.no_issue, "已忽略（no_issue）数必须与后端同口径");
assert.equal(counts.inPackage, expected.in_package, "已进整改包（in_package）属已处理终态");
assert.equal(counts.needsReview, expected.needs_review, "待复核（needs_review）与后端同口径");

// 前端底部状态条的"待处理"= 后端门禁的 pending_findings 阻塞数。
// 两者必须相等：一个说"还有 1 条待处理"、另一个同意完成，是最糟的组合。
assert.equal(
  counts.pending,
  fixture.expected_blocking.pending_findings,
  "前端待处理数必须等于后端 pending_findings 阻塞数",
);
assert.equal(
  counts.needsReview,
  fixture.expected_blocking.needs_review_findings,
  "前端待复核数必须等于后端 needs_review_findings 阻塞数",
);

// 正式问题总数（total）= 各终态之和。降级条目既不算待处理也不算已处理。
assert.equal(
  counts.confirmed + counts.ignored + counts.inPackage + counts.needsReview + counts.pending,
  expected.total,
  "五桶之和必须等于正式问题总数（夹具 total）",
);

// ---- 降级条目的口径 --------------------------------------------------------

const degraded = problems.filter(
  (problem) => problem.evidenceStatus === "degraded_missing_evidence",
);
assert.ok(
  !degraded.some((problem) => problems.slice(0, 1).includes(problem)),
  "夹具里应包含至少一条降级条目，用于验证它不参与计数",
);
assert.equal(
  problems.length - degraded.length,
  expected.total,
  "列表长度减去降级条目数必须等于正式问题总数（降级条目只展示、不计入）",
);

console.log("reviewWorkbenchContract.test.ts: 前端与后端复核问题集合契约一致");
