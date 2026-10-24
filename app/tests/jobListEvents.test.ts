import assert from "node:assert/strict";
import { publishJobCounts, subscribeJobCounts } from "../lib/jobListEvents";
import { countsFromJobs, type JobCountsView } from "../lib/jobListViews";
import { navBadgeCountsFromJobCounts, type NavBadgeCounts } from "../app/components/workspace/navBadges";
import type { JobSummaryRecord } from "../lib/uiAdapters";

const countsOf = (jobs: Array<Partial<JobSummaryRecord>>): JobCountsView => countsFromJobs(jobs as JobSummaryRecord[]);

// SSR must not create browser globals or fail.
publishJobCounts(countsOf([]));
subscribeJobCounts(() => assert.fail("SSR callback"))();

const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
const previousEvent = Object.getOwnPropertyDescriptor(globalThis, "CustomEvent");
class BrowserEvent extends Event {
  detail: unknown;
  constructor(type: string, options: { detail?: unknown } = {}) {
    super(type);
    this.detail = options.detail;
  }
}
Object.defineProperty(globalThis, "window", { configurable: true, value: new EventTarget() });
Object.defineProperty(globalThis, "CustomEvent", { configurable: true, value: BrowserEvent });
try {
  let counts: NavBadgeCounts = navBadgeCountsFromJobCounts(null);
  assert.deepEqual(counts, {}, "未拿到计数时不渲染角标，不显示 0");
  const unsubscribe = subscribeJobCounts((next) => { counts = navBadgeCountsFromJobCounts(next); });
  publishJobCounts(countsOf([{ status: "processing" }]));
  assert.equal(counts.analyzing, 1);
  publishJobCounts(countsOf([{ status: "review_required" }]));
  assert.deepEqual(counts, { analyzing: 0 }, "任务分析状态不能推算人工复核待办");
  window.dispatchEvent(new CustomEvent("govbudgetchecker:job-counts-updated", { detail: [{ status: "processing" }] }));
  assert.deepEqual(counts, { analyzing: 0 }, "形状不对的事件不得改写角标");
  unsubscribe();
  publishJobCounts(countsOf([{ status: "processing" }]));
  assert.deepEqual(counts, { analyzing: 0 }, "unmount must remove the listener");

  // 本地计数（后端未升级时）与服务端 build_counts_view 同口径。
  assert.deepEqual(countsOf([{ status: "done" }, { status: "processing" }, { status: "uploaded" }, { status: "error" }]), {
    total: 4,
    status_counts: { analyzing: 1, review_required: 0, pending_analysis: 1, failed: 1, completed: 1 },
    activity: { running: 1, waiting: 1 },
  });
} finally {
  if (previous) Object.defineProperty(globalThis, "window", previous);
  else Reflect.deleteProperty(globalThis, "window");
  if (previousEvent) Object.defineProperty(globalThis, "CustomEvent", previousEvent);
  else Reflect.deleteProperty(globalThis, "CustomEvent");
}
console.log("jobListEvents.test.ts: all assertions passed");
