/**
 * C4（2026-11-14 复验）：机构归属复核请求生命周期。
 *
 * 用 react-test-renderer 挂载**生产 hook 本体**（useOrgValidationLifecycle），
 * 以可控网络时序（手控 resolve 的 fetch 桩）复现审计指出的失控形态：
 * - 无批量预设的单文件手选必须发起带 selected_org_id 的复核；
 * - 一次失败后自动重试成功；重试有上限，不产生无界 effect 循环；
 * - A→B 换选且 B 先返、A 后返：A 的迟到响应必须丢弃，不得覆盖 B 的结果；
 * - 冲突复核（selected_org_validation.ok=false）如实写入，不无限重发；
 * - 删除文件后的迟到响应作废；
 * - 只有当前目标已取得一致复核结论才停止复核。
 */
import assert from "node:assert/strict";

import TestRenderer, { act } from "react-test-renderer";
import React from "react";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// eslint-disable-next-line import/no-unresolved
import { useOrgValidationLifecycle } from "../app/components/workspace/useOrgValidationLifecycle";


interface Pending {
  resolve: (response: unknown) => void;
  target: string | null;
}

interface Call {
  target: string | null;
}

interface HarnessApi {
  results: { id: string; kind: string }[];
  forget: ((id: string) => void) | null;
  setTargets: ((next: Record<string, string | undefined>) => void) | null;
}

interface UploadEntry {
  id: string;
  file: File;
  touched?: number;
}

const FILE = new File(["x"], "a.pdf", { type: "application/pdf" });

function makeResponse(ok: boolean, payload: Record<string, unknown> = {}) {
  return { ok, status: ok ? 200 : 500, json: async () => payload };
}

function okResponse(target: string, conflict = false) {
  return makeResponse(true, {
    report_year: 2025,
    doc_type_confident: true,
    current: { organization_id: target, match_confidence: 0.98 },
    selected_org_validation: { ok: !conflict, selected_org_id: target },
  });
}

/**
 * Harness 复刻组件的状态装配：onResult 的成败都触碰 entries（组件里是
 * setEntries 状态写入），成功另写 preflightResults——这正是 effect 依赖
 * 链条，缺了它失败重试/换选复核就不会发生。
 */
function harness(initialTargets: Record<string, string | undefined>) {
  const api: HarnessApi = { results: [], forget: null, setTargets: null };
  function Harness() {
    const [entries, setEntries] = React.useState<UploadEntry[]>([
      { id: "e1", file: FILE },
    ]);
    const [results, setResults] = React.useState<Record<string, unknown>>({});
    const [targets, setTargets] = React.useState(initialTargets);
    const { runPreflight, forgetEntry } = useOrgValidationLifecycle({
      entries,
      targets,
      preflightResults: results as never,
      onResult: (id, result) => {
        api.results.push({ id, kind: result.kind });
        if (result.kind === "success") {
          setResults((prev) => ({ ...prev, [id]: result.payload }));
        }
        setEntries((prev) =>
          prev.map((entry) =>
            entry.id === id ? { ...entry, touched: (entry.touched ?? 0) + 1 } : entry,
          ),
        );
      },
    });
    api.forget = forgetEntry;
    api.setTargets = setTargets;
    return null;
  }
  return { api, Harness };
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** 场景运行器：挂载 harness → 收集 fetch 调用 → 手控响应时序。 */
async function runScenario(initialTargets: Record<string, string | undefined>) {
  const pending: Pending[] = [];
  const calls: Call[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_url: string, init?: { body?: FormData }) => {
    const target = (init?.body?.get("selected_org_id") as string | null) ?? null;
    calls.push({ target });
    const gate = new Promise<unknown>((resolve) => pending.push({ resolve, target }));
    return gate as unknown as Response;
  }) as typeof fetch;
  const { api, Harness } = harness(initialTargets);
  await act(async () => {
    TestRenderer.create(React.createElement(Harness));
    await Promise.resolve();
  });
  /** 换选目标：模拟组件里批量预设/人工补齐触发的 targets state 变化。 */
  const changeTargets = async (nextTargets: Record<string, string | undefined>) => {
    await act(async () => {
      api.setTargets?.(nextTargets);
      await Promise.resolve();
    });
  };
  return { api, calls, pending, changeTargets, restoreFetch: () => { globalThis.fetch = originalFetch; } };
}

async function testSingleFileManualSelection() {
  const scenario = await runScenario({ e1: "org-b" }); // 无批量预设的单文件手选
  const { api, calls, pending } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  assert.equal(calls.length, 1, "有目标即发起复核");
  assert.equal(calls[0].target, "org-b", "复核请求携带 selected_org_id");
  await act(async () => { pending[0].resolve(okResponse("org-b")); });
  await flushMicrotasks();
  assert.deepEqual(api.results, [{ id: "e1", kind: "success" }]);
  // 结论与目标一致后不再重发。
  assert.equal(calls.length, 1, "当前目标已取得一致复核结论后不得重复复核");
}

async function testFailureThenRetrySuccess() {
  const scenario = await runScenario({ e1: "org-b" });
  const { api, calls, pending } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  assert.equal(calls.length, 1);
  await act(async () => { pending[0].resolve(makeResponse(false)); });
  await flushMicrotasks();
  assert.deepEqual(api.results, [{ id: "e1", kind: "failure" }],
    "失败必须如实落账，不得静默");
  // effect 因 entries/结果变化重跑：in-flight 已清理 → 自动重试一次。
  await act(async () => { await Promise.resolve(); });
  assert.equal(calls.length, 2, "失败后同目标自动重试一次");
  await act(async () => { pending[1].resolve(okResponse("org-b")); });
  await flushMicrotasks();
  assert.deepEqual(api.results.map(r => r.kind), ["failure", "success"],
    "重试成功后写入结论");
}

async function testRetryCapThenStop() {
  const scenario = await runScenario({ e1: "org-b" });
  const { calls, pending } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  await act(async () => { pending[0].resolve(makeResponse(false)); });
  await flushMicrotasks();
  await act(async () => { await Promise.resolve(); });
  assert.equal(calls.length, 2, "第一次重试");
  await act(async () => { pending[1].resolve(makeResponse(false)); });
  await flushMicrotasks();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(calls.length, 2, "重试上限后停止自动重发（无无界循环）");
}

async function testStaleResponseDiscarded() {
  // A→B 且 B 先返、A 后返：A 的迟到响应不得覆盖 B 的结果。
  const scenario = await runScenario({ e1: "org-a" });
  const { api, calls, pending, changeTargets } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].target, "org-a");
  // 换选 B：目标变化 → effect 重新复核（gen2）。
  await changeTargets({ e1: "org-b" });
  assert.equal(calls.length, 2, "换选目标后重新复核");
  assert.equal(calls[1].target, "org-b");
  // B 先返。
  await act(async () => { pending[1].resolve(okResponse("org-b")); });
  await flushMicrotasks();
  // A 后返（迟到响应，gen1）。
  await act(async () => { pending[0].resolve(okResponse("org-a")); });
  await flushMicrotasks();
  assert.deepEqual(api.results, [{ id: "e1", kind: "success" }],
    "A 的迟到响应被丢弃：只有 B 的结果写入一次");
}

async function testConflictWrittenNotRefetched() {
  const scenario = await runScenario({ e1: "org-b" });
  const { api, calls, pending } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  await act(async () => { pending[0].resolve(okResponse("org-b", true)); });
  await flushMicrotasks();
  assert.deepEqual(api.results, [{ id: "e1", kind: "success" }],
    "冲突结论也是当前目标的复核结论：如实写入");
  await act(async () => { await Promise.resolve(); });
  assert.equal(calls.length, 1, "冲突结论稳定，不无限重发");
}

async function testForgetEntryInvalidatesLateResponse() {
  const scenario = await runScenario({ e1: "org-b" });
  const { api, calls, pending } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  assert.equal(calls.length, 1);
  await act(async () => {
    assert.ok(api.forget, "harness 已装配 forgetEntry");
    api.forget("e1"); // 删除文件：代次递增使迟到响应作废
  });
  await act(async () => { pending[0].resolve(okResponse("org-b")); });
  await flushMicrotasks();
  assert.deepEqual(api.results, [], "删除文件后的迟到响应必须丢弃");
}

async function testNoTargetNoRequest() {
  const scenario = await runScenario({});
  const { calls } = scenario;
  restoreFetchAfterScenario = scenario.restoreFetch;
  assert.equal(calls.length, 0, "无有效目标时不发起复核");
}

let restoreFetchAfterScenario: () => void = () => {};

const tests = [
  testSingleFileManualSelection,
  testFailureThenRetrySuccess,
  testRetryCapThenStop,
  testStaleResponseDiscarded,
  testConflictWrittenNotRefetched,
  testForgetEntryInvalidatesLateResponse,
  testNoTargetNoRequest,
];

async function main() {
  let failed = 0;
  for (const test of tests) {
    try {
      await test();
      console.log(`PASS ${test.name}`);
    } catch (error) {
      failed += 1;
      console.error(`FAIL ${test.name}\n${error}`);
    } finally {
      restoreFetchAfterScenario();
    }
  }
  if (failed) {
    console.error(`${failed} 个生命周期场景未通过`);
    process.exit(1);
  }
  console.log(`机构归属复核生命周期：${tests.length} 场景全部通过`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
