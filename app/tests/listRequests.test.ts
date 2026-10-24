import assert from "node:assert/strict";
import { createSharedListRequest } from "../lib/listRequests";

async function main() {
  let calls = 0;
  let resolveRead!: (value: number) => void;
  const shared = createSharedListRequest<number>(() => {
    calls += 1;
    return new Promise(resolve => { resolveRead = resolve; });
  });
  const first = shared.load();
  const second = shared.load();
  assert.equal(first, second, "侧栏和清单必须共享同一个在途请求");
  await Promise.resolve();
  assert.equal(calls, 1);
  resolveRead(23);
  assert.deepEqual(await Promise.all([first, second]), [23, 23]);
  const next = shared.load();
  await Promise.resolve();
  assert.equal(calls, 2, "响应不持久缓存，后续读取重新鉴权");
  resolveRead(22);
  assert.equal(await next, 22);

  let observedSignal!: AbortSignal;
  const abortable = createSharedListRequest<number>(signal => {
    observedSignal = signal;
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))));
  });
  const stale = abortable.load();
  const staleRejected = assert.rejects(stale, /aborted/);
  await Promise.resolve();
  abortable.reset();
  assert.equal(observedSignal.aborted, true, "复核写入后必须取消旧读取");
  await staleRejected;
  const fresh = abortable.load();
  assert.notEqual(fresh, stale);
  const freshRejected = assert.rejects(fresh, /aborted/);
  await Promise.resolve();
  abortable.reset();
  await freshRejected;

  const timeout = createSharedListRequest<number>(signal => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("timeout")));
  }), 10);
  await assert.rejects(timeout.load(), /timeout/, "失败必须结束等待，可再次发起请求");
  await assert.rejects(timeout.load(), /timeout/);
  console.log("listRequests.test.ts passed");
}
void main();
