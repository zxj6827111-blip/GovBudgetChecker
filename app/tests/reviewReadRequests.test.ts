import assert from "node:assert/strict";
import { readReviewJson, invalidateReviewReads } from "../lib/reviewReadRequests";

async function main() {
  const original = globalThis.fetch;
  const requests: Array<{ url: string; resolve: (response: Response) => void; signal: AbortSignal }> = [];
  globalThis.fetch = (input, init) => new Promise((resolve, reject) => {
    const signal = init?.signal as AbortSignal;
    requests.push({ url: String(input), resolve, signal });
    if (signal.aborted) reject(new Error("aborted"));
    else signal.addEventListener("abort", () => reject(new Error("aborted")));
  });
  const reply = (index: number, value: unknown, status = 200) => requests[index].resolve(new Response(JSON.stringify(value), { status }));
  try {
    const first = readReviewJson<{ version: number }>("/api/jobs/a");
    const second = readReviewJson<{ version: number }>("/api/jobs/a");
    assert.equal(first, second);
    await Promise.resolve();
    assert.equal(requests.length, 1);
    reply(0, { version: 1 });
    assert.equal((await second).version, 1);
    const next = readReviewJson<{ version: number }>("/api/jobs/a");
    await Promise.resolve();
    assert.equal(requests.length, 2, "已完成响应不能缓存为复核事实");
    reply(1, { version: 2 });
    assert.equal((await next).version, 2);

    const old = readReviewJson("/api/reviews?job_uuid=a");
    const oldRejected = assert.rejects(old, /aborted/);
    const other = readReviewJson<{ job: string }>("/api/jobs/b");
    await Promise.resolve();
    invalidateReviewReads("a");
    assert.equal(requests[2].signal.aborted, true);
    assert.equal(requests[3].signal.aborted, false, "取消 A 的旧读取不能影响 B");
    await oldRejected;
    const fresh = readReviewJson<{ version: number }>("/api/reviews?job_uuid=a");
    await Promise.resolve();
    reply(4, { version: 3 });
    reply(3, { job: "b" });
    assert.equal((await fresh).version, 3);
    assert.equal((await other).job, "b");

    const failed = readReviewJson("/api/workflow?job_id=a");
    const denied = assert.rejects(failed, /HTTP 403/);
    await Promise.resolve();
    reply(5, {}, 403);
    await denied;
    const retried = readReviewJson("/api/workflow?job_id=a");
    await Promise.resolve();
    reply(6, { issues: {} });
    assert.deepEqual(await retried, { issues: {} });
    console.log("reviewReadRequests.test.ts passed");
  } finally {
    globalThis.fetch = original;
  }
}
void main();
