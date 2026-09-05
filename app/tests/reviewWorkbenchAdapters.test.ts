import assert from "node:assert/strict";

import type { Problem } from "../lib/mock";
import type { JobDetailRecord } from "../lib/uiAdapters";
import {
  computeOverlayBoxAtScale,
  computeWorkflowStatusCounts,
  countFormalProblems,
  deriveStageHistory,
  extractFindingVersions,
  extractTotalPageCount,
  formatMetadataReportKind,
  formatMetadataYear,
  formatVersionList,
  isProblemDegraded,
  resolveProblemTargetPage,
  resolveWorkbenchHeaderBadge,
  sortProblemsForReview,
  ThumbnailLoadScheduler,
} from "../app/components/review-workbench/reviewWorkbenchAdapters";

// 本文件测试 Task 6 审核工作台的纯逻辑层：缩略图并发调度/问题计数与排序口径/
// 元数据格式化/顶部状态徽章/底部工作流计数。这批函数承载任务书里最容易被
// 复核抓到的红线（"问题数不得前端另算"、"年份未识别到禁止2000兜底"、
// "review_required不得显示成分析完成"、"底部计数不得写死2/1/3"）。

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- ThumbnailLoadScheduler：懒加载 + 并发上限 + 缓存（性能关键，mock fetch）--

async function testConcurrencyCapIsRespected(): Promise<void> {
  let activeCount = 0;
  let observedPeak = 0;
  const scheduler = new ThumbnailLoadScheduler({
    maxConcurrent: 3,
    fetchPage: async (page) => {
      activeCount += 1;
      observedPeak = Math.max(observedPeak, activeCount);
      await delay(10);
      activeCount -= 1;
      return `blob:page-${page}`;
    },
  });

  // 一次性请求 10 页（模拟 48 页缩略图栏一次性挂载全部行的最坏情况）。
  for (let page = 1; page <= 10; page += 1) {
    scheduler.requestPage(page);
  }

  // 等待全部完成
  await delay(200);

  assert.equal(
    observedPeak <= 3,
    true,
    `REGRESSION: 并发上限=3 时实测峰值并发不得超过 3，实际观测到 ${observedPeak}`,
  );
  assert.equal(scheduler.getTotalRequestCount(), 10, "10 个不同页码应产生 10 次真实请求");
}

async function testCachingAvoidsDuplicateRequests(): Promise<void> {
  let requestCount = 0;
  const scheduler = new ThumbnailLoadScheduler({
    maxConcurrent: 3,
    fetchPage: async (page) => {
      requestCount += 1;
      await delay(5);
      return `blob:page-${page}`;
    },
  });

  scheduler.requestPage(5);
  scheduler.requestPage(5); // 重复请求同一页（例如滚动来回触发 IntersectionObserver）
  scheduler.requestPage(5);
  await delay(50);
  scheduler.requestPage(5); // 加载完成后再次请求，应直接命中缓存

  assert.equal(
    requestCount,
    1,
    `REGRESSION: 同一页重复请求必须去重+缓存，只应产生 1 次真实网络请求，实际 ${requestCount} 次`,
  );
  assert.equal(scheduler.getEntry(5).status, "loaded");
  assert.equal(scheduler.getEntry(5).src, "blob:page-5");
}

async function testLazyLoadDoesNotFetchUnrequestedPages(): Promise<void> {
  let requestCount = 0;
  const scheduler = new ThumbnailLoadScheduler({
    maxConcurrent: 5,
    fetchPage: async (page) => {
      requestCount += 1;
      return `blob:page-${page}`;
    },
  });

  // 只请求视口内的 2 页，模拟"懒加载：仅渲染视口内 + 少量预取"，
  // 其余 46 页（总共 48 页场景）不应被调度器自己扫描出来提前加载。
  scheduler.requestPage(12);
  scheduler.requestPage(13);
  await delay(20);

  assert.equal(
    requestCount,
    2,
    `REGRESSION: 懒加载调度器不应主动拉取未被请求的页，实际发生了 ${requestCount} 次请求`,
  );
}

async function testFailedPageIsCachedAsErrorNotRetriedInLoop(): Promise<void> {
  let requestCount = 0;
  const scheduler = new ThumbnailLoadScheduler({
    maxConcurrent: 3,
    fetchPage: async () => {
      requestCount += 1;
      throw new Error("network error");
    },
  });

  scheduler.requestPage(7);
  await delay(20);
  scheduler.requestPage(7); // 加载中/已完成（即便是失败）时重复请求不应再次触发网络调用

  assert.equal(scheduler.getEntry(7).status, "error");
  assert.equal(
    requestCount,
    1,
    "REGRESSION: 失败的页不应在短时间内被同一个 requestPage 调用重复触发网络请求（缓存失败结果，避免坏图拖慢整体加载）",
  );
}

// --- 同步断言：问题计数/排序/元数据格式化/顶部徽章/底部工作流计数 -------------

function makeProblem(overrides: Partial<Problem>): Problem {
  return {
    id: "p-1",
    ruleId: "C-001",
    title: "示例问题",
    severity: "high",
    category: "本地规则",
    description: "示例描述",
    suggestion: "示例建议",
    snippet: "示例引文",
    evidenceImage: "",
    status: "pending",
    ...overrides,
  };
}

function runSyncAssertions(): void {
  // --- computeOverlayBoxAtScale：bbox 百分比换算，支持可变渲染缩放 -----------

  assert.equal(computeOverlayBoxAtScale(undefined, { width: 1000, height: 1000 }, 1.0), null);
  assert.equal(computeOverlayBoxAtScale([10, 20, 100, 200], null, 1.0), null, "naturalSize 未知时不得猜测定位框");
  assert.equal(
    computeOverlayBoxAtScale([100, 200, 50, 200], { width: 1000, height: 1000 }, 1.0),
    null,
    "REGRESSION: x1<=x0（非法 bbox）必须返回 null，不得渲染出一个宽度为负的高亮框",
  );

  // bbox=[100,100,200,300]（PDF 点坐标），渲染缩放 1.0，图片自然宽高 1000x1000
  // 时，左上角应在 10%/10%，宽高应是 10%/20%。
  const boxAtScale1 = computeOverlayBoxAtScale([100, 100, 200, 300], { width: 1000, height: 1000 }, 1.0);
  assert.ok(boxAtScale1);
  assert.equal(boxAtScale1?.leftPct, 10);
  assert.equal(boxAtScale1?.topPct, 10);
  assert.equal(boxAtScale1?.widthPct, 10);
  assert.equal(boxAtScale1?.heightPct, 20);

  // 同一个 bbox，渲染缩放变为 2.0 时，图片自然宽高也应相应变为 2000x2000
  // （用户调大缩放后重新拉取的图片确实更大），换算出的百分比应保持不变——
  // 这是"缩放变化后高亮框仍然对齐"这条反例的核心验证。
  const boxAtScale2 = computeOverlayBoxAtScale([100, 100, 200, 300], { width: 2000, height: 2000 }, 2.0);
  assert.ok(boxAtScale2);
  assert.equal(
    boxAtScale2?.leftPct,
    boxAtScale1?.leftPct,
    "REGRESSION: 缩放变化时（渲染缩放与图片自然尺寸同步变化），百分比定位必须保持不变，否则高亮框会随缩放漂移错位",
  );
  assert.equal(boxAtScale2?.topPct, boxAtScale1?.topPct);
  assert.equal(boxAtScale2?.widthPct, boxAtScale1?.widthPct);
  assert.equal(boxAtScale2?.heightPct, boxAtScale1?.heightPct);

  // 反例：如果只改变 renderScale 而不同步改变图片自然尺寸（模拟"忘记用新缩放
  // 重新请求图片，用旧图片配新缩放值计算"这种 bug），百分比必须明显不同——
  // 证明 renderScale 确实是计算里的活跃变量，不是被忽略的死参数。
  const boxWithMismatchedScale = computeOverlayBoxAtScale([100, 100, 200, 300], { width: 1000, height: 1000 }, 2.0);
  assert.notEqual(
    boxWithMismatchedScale?.leftPct,
    boxAtScale1?.leftPct,
    "renderScale 必须是换算公式里的活跃参数，改变它必须改变结果",
  );

  // --- resolveProblemTargetPage：无法定位时返回 null，不得猜测页码 -----------

  assert.equal(resolveProblemTargetPage(makeProblem({ page: 12 })), 12);
  assert.equal(
    resolveProblemTargetPage(makeProblem({ page: undefined })),
    null,
    "REGRESSION: 页码未知时必须返回 null，调用方不得猜测跳到某一页",
  );
  assert.equal(resolveProblemTargetPage(makeProblem({ page: 0 })), null, "页码 0 不是合法页码");

  // --- deriveStageHistory：5 态阶段记录派生（不含 OCR），失败/未知语义反例 ---

  const successCasePhase3 = deriveStageHistory({ phase: "metadata_recognition", percent: 65 }, null);
  assert.deepEqual(
    successCasePhase3.map((entry) => entry.status),
    ["done", "done", "current", "pending", "pending"],
    "REGRESSION: 当前阶段之前应为 done，当前阶段本身为 current，之后为 pending",
  );
  assert.equal(successCasePhase3[2].percent, 65);
  assert.equal(successCasePhase3[0].percent, null, "非 current 阶段不应携带百分比（避免误导为'也在进行中'）");
  assert.deepEqual(
    successCasePhase3.map((entry) => entry.stage),
    ["upload", "pdf_parse", "metadata_recognition", "rule_ai_analysis", "quality_gate"],
    "REGRESSION: 5 态阶段顺序中不得出现 ocr，且顺序必须与后端枚举一致",
  );

  const failureCase = deriveStageHistory(null, { phase: "rule_ai_analysis" });
  assert.deepEqual(
    failureCase.map((entry) => entry.status),
    ["done", "done", "done", "failed", "unknown"],
    "REGRESSION: 失败阶段之前应为 done，失败阶段本身为 failed，之后为 unknown（不是 pending——流水线已中断，不能暗示'还会继续跑'）",
  );

  const unknownCase = deriveStageHistory(null, null);
  assert.deepEqual(
    unknownCase.map((entry) => entry.status),
    ["unknown", "unknown", "unknown", "unknown", "unknown"],
    "REGRESSION: 完全没有阶段数据时，5 个阶段都必须是 unknown，不得凭空标记任何一个为 done",
  );

  const unrecognizedPhaseCase = deriveStageHistory({ phase: "some_future_stage", percent: 10 }, null);
  assert.deepEqual(
    unrecognizedPhaseCase.map((entry) => entry.status),
    ["unknown", "unknown", "unknown", "unknown", "unknown"],
    "REGRESSION: phase 不在已知 5 态枚举里时（未来新增但前端未同步/历史脏数据），不得猜测映射到某个已知阶段",
  );

  // --- countFormalProblems / isProblemDegraded：与 count_formal_findings 同口径

  const degradedProblem = makeProblem({ id: "p-degraded", evidenceStatus: "degraded_missing_evidence" });
  const completeProblem = makeProblem({ id: "p-complete", evidenceStatus: "complete" });
  const legacyProblem = makeProblem({ id: "p-legacy" }); // 历史产物没有 evidenceStatus 字段

  assert.equal(isProblemDegraded(degradedProblem), true);
  assert.equal(isProblemDegraded(completeProblem), false);
  assert.equal(isProblemDegraded(legacyProblem), false, "历史产物没有 evidenceStatus 字段时不应被误判为降级");

  assert.equal(
    countFormalProblems([degradedProblem, completeProblem, legacyProblem]),
    2,
    "REGRESSION: 降级问题不计入正式问题数（与后端 count_formal_findings 同口径），3 条里只有 2 条正式",
  );
  assert.equal(countFormalProblems([degradedProblem]), 0, "全部降级时正式问题数必须是 0");
  assert.equal(countFormalProblems([]), 0);

  // --- sortProblemsForReview：按页码升序，无页码排最后，稳定排序 -------------

  const unordered: Problem[] = [
    makeProblem({ id: "no-page-1", page: undefined }),
    makeProblem({ id: "page-12", page: 12 }),
    makeProblem({ id: "page-3", page: 3 }),
    makeProblem({ id: "page-3-b", page: 3 }),
    makeProblem({ id: "no-page-2", page: undefined }),
  ];
  const sorted = sortProblemsForReview(unordered);
  assert.deepEqual(
    sorted.map((p) => p.id),
    ["page-3", "page-3-b", "page-12", "no-page-1", "no-page-2"],
    "REGRESSION: 应按页码升序排列，无页码的排最后，同页保持原始相对顺序（稳定排序）",
  );

  // --- extractTotalPageCount：不得猜测总页数（原型图 48 是设计稿占位） --------

  assert.equal(extractTotalPageCount(null), null);
  assert.equal(extractTotalPageCount({ job_id: "j-1" } as JobDetailRecord), null, "缺少 result.meta.pages 时必须返回 null");
  const detailWithPages = { job_id: "j-3", result: { meta: { pages: 48 } } } as unknown as JobDetailRecord;
  assert.equal(extractTotalPageCount(detailWithPages), 48);
  const detailWithZeroPages = { job_id: "j-4", result: { meta: { pages: 0 } } } as unknown as JobDetailRecord;
  assert.equal(extractTotalPageCount(detailWithZeroPages), null, "0 页不是合法的真实页数，应视为未知");

  // --- extractFindingVersions / formatVersionList：M2 版本留痕 ---------------

  assert.equal(extractFindingVersions(null), null);
  assert.equal(extractFindingVersions(undefined), null);
  assert.equal(
    extractFindingVersions({ job_id: "j-1" } as JobDetailRecord),
    null,
    "缺少 result 字段时必须返回 null，不得凑一个假的版本摘要",
  );

  const detailWithVersions = {
    job_id: "j-2",
    result: {
      meta: {
        versions: {
          rule_versions: ["v3_3"],
          engine_version: "0.1.0",
          model_versions: [],
          prompt_versions: [],
        },
      },
    },
  } as unknown as JobDetailRecord;
  const versions = extractFindingVersions(detailWithVersions);
  assert.ok(versions);
  assert.deepEqual(versions?.rule_versions, ["v3_3"]);
  assert.equal(versions?.engine_version, "0.1.0");

  assert.equal(formatVersionList(["v3_3"]), "v3_3");
  assert.equal(formatVersionList(["v3_3", "v3_4"]), "v3_3、v3_4");
  assert.equal(formatVersionList([]), "未识别到", "REGRESSION: 空列表必须显示未识别到，不得显示空字符串或占位符");
  assert.equal(formatVersionList(null), "未识别到");
  assert.equal(formatVersionList(undefined), "未识别到");

  // --- formatMetadataYear / formatMetadataReportKind：禁止 2000 兜底 ---------

  assert.equal(formatMetadataYear(2026), "2026");
  assert.equal(formatMetadataYear(null), "未识别到", "REGRESSION: 年份未识别到(null)必须显示'未识别到'，禁止任何 2000 兜底");
  assert.equal(formatMetadataYear(undefined), "未识别到");
  assert.equal(formatMetadataYear(0), "未识别到", "0 不是有效年份，不应显示'0'");
  assert.notEqual(formatMetadataYear(null), "2000", "REGRESSION: 绝不能出现 2000 兜底值");

  assert.equal(formatMetadataReportKind("budget"), "部门预算");
  assert.equal(formatMetadataReportKind("final"), "部门决算");
  assert.equal(formatMetadataReportKind("unknown"), "未识别到", "REGRESSION: 类型未识别到必须显示'未识别到'，不得猜测成具体类型");
  assert.equal(formatMetadataReportKind(undefined), "未识别到");

  // --- resolveWorkbenchHeaderBadge：review_required 不得显示成"分析完成" -----

  assert.deepEqual(
    resolveWorkbenchHeaderBadge({ status: "review_required" }),
    { tone: "review", label: "需要人工复核" },
    "REGRESSION: review_required 必须显示'需要人工复核'，绝不能显示成'分析完成'",
  );
  assert.deepEqual(resolveWorkbenchHeaderBadge({ status: "error" }), { tone: "failed", label: "处理失败" });
  assert.deepEqual(resolveWorkbenchHeaderBadge({ status: "processing" }), { tone: "processing", label: "正在分析" });
  assert.deepEqual(
    resolveWorkbenchHeaderBadge({ status: "done", quality_status: "degraded" }),
    { tone: "lowconf", label: "低置信度" },
  );
  assert.deepEqual(resolveWorkbenchHeaderBadge({ status: "done" }), { tone: "done", label: "分析完成" });
  assert.notDeepEqual(
    resolveWorkbenchHeaderBadge({ status: "review_required" }),
    { tone: "done", label: "分析完成" },
    "REGRESSION: 双重确认 review_required 与'分析完成'徽章不是同一个结果",
  );

  // --- computeWorkflowStatusCounts：不得写死原型图的 2/1/3 -------------------

  assert.deepEqual(
    computeWorkflowStatusCounts([], {}),
    { confirmed: 0, ignored: 0, pending: 0 },
    "REGRESSION: 没有任何问题、没有任何工作流记录时，三项计数必须是真实的 0/0/0，不得是原型图示例的 2/1/3",
  );

  const sixProblems: Problem[] = Array.from({ length: 6 }, (_, i) => makeProblem({ id: `issue-${i + 1}` }));
  const workflowRecords = {
    "issue-1": { issue_id: "issue-1", status: "confirmed" },
    "issue-2": { issue_id: "issue-2", status: "confirmed" },
    "issue-3": { issue_id: "issue-3", status: "no_issue" },
    // issue-4/5/6 未在 workflowRecords 里，视为待处理
  };
  assert.deepEqual(
    computeWorkflowStatusCounts(sixProblems, workflowRecords),
    { confirmed: 2, ignored: 1, pending: 3 },
    "6 个问题：2 已确认 + 1 已忽略 + 3 待处理（含未操作过的），与原型图 2/1/3 的数字巧合但来自真实计算，不是硬编码",
  );

  // 反例：降级问题不计入底部状态条的任何计数（既不算待处理也不算已确认/已忽略）
  const withDegraded: Problem[] = [
    ...sixProblems,
    makeProblem({ id: "degraded-1", evidenceStatus: "degraded_missing_evidence" }),
  ];
  assert.deepEqual(
    computeWorkflowStatusCounts(withDegraded, workflowRecords),
    { confirmed: 2, ignored: 1, pending: 3 },
    "REGRESSION: 降级问题不应计入待处理数，否则底部计数会比审核问题tab的正式问题数更大，造成口径分裂",
  );
}

async function main(): Promise<void> {
  await testConcurrencyCapIsRespected();
  await testCachingAvoidsDuplicateRequests();
  await testLazyLoadDoesNotFetchUnrequestedPages();
  await testFailedPageIsCachedAsErrorNotRetriedInLoop();
  runSyncAssertions();
  console.log("reviewWorkbenchAdapters.test.ts passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
