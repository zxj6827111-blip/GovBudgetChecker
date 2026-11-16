/**
 * 机构归属复核请求生命周期（C4，2026-11-14 复验）。
 *
 * 修复此前 UploadCenterPage 内联实现的两类缺陷：
 * 1. in-flight 目标记录在请求发出前写入，失败/完成后从不清理，同目标因去重
 *    直接跳过——失败后永远不会按注释声称再次触发；
 * 2. 响应不核对请求身份与当前目标，A→B 换选时旧 A 响应可覆盖 B 的结果
 *    （in-flight 仍锁在 B，B 的结论既丢失也不再复核）。
 *
 * 现在的契约：
 * - 请求身份（代次）+ 当前目标一致校验：代次不是最新、或当前目标已不再是
 *   发起该请求的目标时，响应整体丢弃（不写结果、不改状态）；
 * - 失败清理 in-flight 并有限自动重试（同一目标最多 2 次），超过后停止自动
 *   重试，留给人工重试入口（重新换选组织/重新选择文件），无无界 effect 循环；
 * - 换选/移除/提交成功通过 forgetEntry 清理在途、失败与代次状态，使迟到
 *   响应作废；
 * - 只有当前目标已取得一致复核结论（或如实写入的冲突结论）才算稳定，徽章
 *   语义由 derivePreflightStatus 依据 selected_org_validation 判定。
 */
"use client";

import { useCallback, useEffect, useRef } from "react";

import type { PreflightResponseLike } from "./uploadCenterAdapters";

export interface OrgValidationEntryLike {
  id: string;
  file: File;
}

export interface OrgValidationFailure {
  org: string;
  attempts: number;
}

interface Options {
  /** 参与复核的文件（含 File，供复核请求使用）。 */
  entries: OrgValidationEntryLike[];
  /** entryId -> 当前有效复核目标（单文件人工覆盖优先，其次批量预设）。 */
  targets: Record<string, string | undefined>;
  /** 已有预检结果；带 selected_org_validation 的一致结论可阻止重复复核。 */
  preflightResults: Record<string, PreflightResponseLike>;
  /** 结果与状态写入回调（失败/成功都由组件落到自己的 state）。 */
  onResult: (
    entryId: string,
    result:
      | { kind: "success"; payload: PreflightResponseLike & { page_count?: number | null } }
      | { kind: "failure" },
  ) => void;
}

/** 同一目标最多自动重试次数（首次 + 1 次重试），超过留给人工。 */
const MAX_AUTO_ATTEMPTS = 2;

export function useOrgValidationLifecycle({ entries, targets, preflightResults, onResult }: Options) {
  /** entryId -> 在途复核目标（响应返回/失败后清理）。 */
  const inFlightRef = useRef<Record<string, string>>({});
  /** entryId -> 最新请求代次；过期响应按代次丢弃。 */
  const requestGenRef = useRef<Record<string, number>>({});
  /** entryId -> 最近失败目标与已尝试次数（限制**同一选择周期**内的自动重试）。
   *  换选到不同目标即开启新周期：effect 检测到目标变化时清掉旧台账，
   *  换回原目标可以重新获得有限额度（C4 收口：有限重试与可恢复并存）。 */
  const failuresRef = useRef<Record<string, OrgValidationFailure>>({});
  /** entryId -> effect 最近一次发起复核的目标（识别"换选开新周期"）。 */
  const lastDispatchTargetRef = useRef<Record<string, string>>({});
  /** targets 的实时镜像：响应回调里判定"当前目标"用，不依赖闭包快照。 */
  const targetsRef = useRef(targets);
  targetsRef.current = targets;
  /** onResult 实时镜像（回调可能带闭包 state）。 */
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  /** 失败台账登记：同目标累计尝试次数，限制自动重试。 */
  const registerFailure = useCallback((entryId: string, org: string) => {
    const previous = failuresRef.current[entryId];
    failuresRef.current[entryId] = previous && previous.org === org
      ? { org, attempts: previous.attempts + 1 }
      : { org, attempts: 1 };
  }, []);

  const runPreflight = useCallback(
    async (entryId: string, file: File, selectedOrgId?: string) => {
      // 请求身份：代次 + 发起时的目标。响应返回后两者任一不再成立即过期。
      const gen = (requestGenRef.current[entryId] ?? 0) + 1;
      requestGenRef.current[entryId] = gen;
      const claimedTarget = selectedOrgId ?? "";
      const stale = () =>
        requestGenRef.current[entryId] !== gen
        || (Boolean(selectedOrgId) && targetsRef.current[entryId] !== claimedTarget);
      try {
        const formData = new FormData();
        formData.set("file", file);
        if (selectedOrgId) {
          formData.set("selected_org_id", selectedOrgId);
        }
        const response = await fetch("/api/documents/preflight", { method: "POST", body: formData });
        if (stale()) {
          return; // 丢弃过期响应：不覆盖新目标的任何状态
        }
        if (!response.ok) {
          registerFailure(entryId, claimedTarget);
          onResultRef.current(entryId, { kind: "failure" });
          return;
        }
        const payload = (await response.json()) as PreflightResponseLike & {
          page_count?: number | null;
        };
        // C4 收口（第二轮复验）：response.json 是第二个异步边界——头先到、
        // 正文延迟时，换选可能发生在 await json 期间。写状态前必须重判过期。
        if (stale()) {
          return; // 丢弃过期响应体：不得覆盖新目标的任何结果
        }
        onResultRef.current(entryId, { kind: "success", payload });
      } catch {
        if (!stale()) {
          registerFailure(entryId, claimedTarget);
          onResultRef.current(entryId, { kind: "failure" });
        }
      } finally {
        // 在途结束（无论成败）：失败后的重发由 effect 依据失败台账决定。
        // 清理按**请求身份**（代次+目标）双重核对——旧请求的 finally 不得
        // 清掉同目标新请求（A→A 重发）或新目标请求的 in-flight 记录。
        if (requestGenRef.current[entryId] === gen
            && inFlightRef.current[entryId] === claimedTarget) {
          delete inFlightRef.current[entryId];
        }
      }
    },
    [registerFailure],
  );

  useEffect(() => {
    for (const entry of entries) {
      const targetOrg = targets[entry.id];
      if (!targetOrg) {
        continue;
      }
      if (inFlightRef.current[entry.id] === targetOrg) {
        continue; // 同一目标的复核在途
      }
      const conclusion = preflightResults[entry.id]?.selected_org_validation?.selected_org_id;
      if (conclusion === targetOrg) {
        continue; // 当前目标已取得一致复核结论（含如实写入的冲突结论）
      }
      // 换选（与上一周期目标不同）= 新选择周期：旧目标的失败台账清零，
      // 换回原目标同样重新获得有限额度。同周期内重试仍受 MAX 限制。
      if (lastDispatchTargetRef.current[entry.id]
          && lastDispatchTargetRef.current[entry.id] !== targetOrg) {
        delete failuresRef.current[entry.id];
      }
      const failure = failuresRef.current[entry.id];
      if (failure && failure.org === targetOrg && failure.attempts >= MAX_AUTO_ATTEMPTS) {
        continue; // 同一选择周期重试上限：停止自动重发，留人工重试入口
      }
      lastDispatchTargetRef.current[entry.id] = targetOrg;
      inFlightRef.current[entry.id] = targetOrg;
      void runPreflight(entry.id, entry.file, targetOrg);
    }
  }, [entries, preflightResults, runPreflight, targets]);

  /** 换选/移除/提交成功后清理：在途记录删除、失败台账删除、代次递增使
   *  该条目一切迟到响应作废。 */
  const forgetEntry = useCallback((entryId: string) => {
    delete inFlightRef.current[entryId];
    delete failuresRef.current[entryId];
    delete lastDispatchTargetRef.current[entryId];
    requestGenRef.current[entryId] = (requestGenRef.current[entryId] ?? 0) + 1;
  }, []);

  return { runPreflight, forgetEntry };
}
