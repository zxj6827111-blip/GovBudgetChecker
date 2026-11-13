import assert from "node:assert/strict";
import { publishJobList, subscribeJobList } from "../lib/jobListEvents";
import { computeNavBadgeCounts } from "../app/components/workspace/navBadges";
import type { JobSummaryRecord } from "../lib/uiAdapters";

// SSR must not create browser globals or fail.
publishJobList([]);
subscribeJobList(() => assert.fail("SSR callback"))();

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
  let counts = computeNavBadgeCounts(null);
  const unsubscribe = subscribeJobList((jobs) => { counts = computeNavBadgeCounts(jobs); });
  publishJobList([{ status: "processing" }] as JobSummaryRecord[]);
  assert.equal(counts.analyzing, 1);
  publishJobList([{ status: "review_required" }] as JobSummaryRecord[]);
  assert.deepEqual(counts, { analyzing: 0, review_required: 1 });
  unsubscribe();
  publishJobList([]);
  assert.equal(counts.review_required, 1, "unmount must remove the listener");
} finally {
  if (previous) Object.defineProperty(globalThis, "window", previous);
  else Reflect.deleteProperty(globalThis, "window");
  if (previousEvent) Object.defineProperty(globalThis, "CustomEvent", previousEvent);
  else Reflect.deleteProperty(globalThis, "CustomEvent");
}
console.log("jobListEvents.test.ts: all assertions passed");
