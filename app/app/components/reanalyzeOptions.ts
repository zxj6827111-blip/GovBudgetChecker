/**
 * 重新分析检查方式选择（2026-11-08 任务书）：纯逻辑适配器。
 *
 * 两个选项：按上次检查方式（默认，逐条继承）/ 使用大模型参与（可选本次模型）。
 * 组件只负责渲染；请求体构建、继承可用性判断、上轮执行状态文案都在这里，便于单测。
 */

export type ReanalyzeMethodValue = {
  checkMethod: "inherit" | "ai";
  /** 选项二的本次模型；空串 = 系统当前模型（界面已展示）。 */
  aiProfileId: string;
};

export const DEFAULT_REANALYZE_METHOD: ReanalyzeMethodValue = {
  checkMethod: "inherit",
  aiProfileId: "",
};

export type ReanalyzeModelSummary = { id: string; name: string; model: string };

export type ReanalyzeCapabilities = {
  enabled: boolean;
  configured?: boolean;
  default_use_ai?: boolean;
  active_profile_id: string | null;
  model?: string | null;
  profiles: ReanalyzeModelSummary[];
};

export type ReanalyzeOptionsPreview = {
  job_id: string;
  inherit: {
    available: boolean;
    unknown_fields: string[];
    message: string | null;
    mode: string | null;
    use_local_rules: boolean | null;
    use_ai_assist: boolean | null;
    ai_profile_id: string | null;
    ai_configuration: { id?: string; name?: string; model?: string; revision?: number } | null;
  };
  last_execution: {
    status: string | null;
    analysis_conclusion: string | null;
    ai_requested: boolean | null;
    local_state: string | null;
    ai_state: string | null;
  };
  model: {
    state: "ok" | "changed" | "deleted" | "disabled" | "no_key" | "key_invalid" | "unknown" | "not_applicable";
    message: string | null;
    profile: ReanalyzeModelSummary | null;
    changed_fields?: string[];
  };
  capabilities: ReanalyzeCapabilities;
};

/** 构建重分析请求体；业务键（如 document_scope_request_id）由调用方自行合并。 */
export function buildReanalyzeRequestBody(value: ReanalyzeMethodValue): Record<string, unknown> {
  if (value.checkMethod === "ai") {
    return value.aiProfileId
      ? { check_method: "ai", ai_profile_id: value.aiProfileId }
      : { check_method: "ai" };
  }
  return { check_method: "inherit" };
}

/** 选项一不可用时给出原因；可用返回 null。批量无预览时恒可用（后端逐条继承）。 */
export function inheritBlockedReason(preview: ReanalyzeOptionsPreview | null): string | null {
  if (!preview) return null;
  if (!preview.inherit.available) {
    return preview.inherit.message || "上次检查方式未记录，请显式选择本次检查方式";
  }
  if (["deleted", "disabled", "no_key", "key_invalid", "unknown"].includes(preview.model.state)) {
    return preview.model.message || "原模型当前不可用，请显式选择本次模型";
  }
  return null;
}

/** 上轮检查方式摘要（选项一的说明行）。 */
export function lastRunSummary(preview: ReanalyzeOptionsPreview | null): string {
  if (!preview) return "正在读取上次检查方式…";
  const { inherit } = preview;
  if (!inherit.available && inherit.use_ai_assist === null) return "上次检查方式未知";
  if (inherit.mode === "structured") return "上次：仅结构化入库";
  const modelName = inherit.ai_configuration?.name || preview.model.profile?.name || inherit.ai_profile_id;
  if (inherit.use_ai_assist === true) {
    const local = inherit.use_local_rules === false ? "仅大模型" : "本地规则 + 大模型";
    return `上次：${local}${modelName ? `（${modelName}）` : "（模型未记录）"}`;
  }
  if (inherit.use_ai_assist === false) return "上次：仅本地规则";
  return "上次检查方式部分未知";
}

/** 上轮执行状态提示：AI 失败/部分完成仍保留启用选择，但必须展示状态。 */
export function lastExecutionHint(preview: ReanalyzeOptionsPreview | null): string | null {
  if (!preview) return null;
  const { last_execution: execution } = preview;
  if (execution.ai_requested === true && execution.ai_state === "failed") {
    return "上次大模型检查失败，本次仍按原选择启用大模型";
  }
  if (execution.ai_requested === true && execution.ai_state === "degraded") {
    return "上次大模型检查部分完成，本次仍按原选择启用大模型";
  }
  if (execution.status === "error") return "上次分析失败，本次按原检查方式重试";
  if (execution.status === "cancelled") return "上次分析已取消，本次按原检查方式重试";
  return null;
}

/** 提交是否可用：继承需历史已知且原模型可执行；显式 AI 需系统已启用大模型。 */
export function canSubmitReanalyze(
  value: ReanalyzeMethodValue,
  preview: ReanalyzeOptionsPreview | null,
  capabilities: ReanalyzeCapabilities | null,
  batch: boolean,
): boolean {
  if (value.checkMethod === "inherit") {
    return batch ? true : preview !== null && inheritBlockedReason(preview) === null;
  }
  return Boolean(capabilities?.enabled);
}

/** 成功响应里的继承告警（模型配置变化等）；非数组/空串一律丢弃。 */
export function inheritanceWarningsFromResponse(payload: unknown): string[] {
  if (!payload || typeof payload !== "object") return [];
  const raw = (payload as Record<string, unknown>).inheritance_warnings;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** 多条继承告警合成一行文案，供提交回执/批量结果展示。 */
export function formatInheritanceWarnings(warnings: string[]): string {
  return warnings.join("；");
}

/** 确认按钮/提交前的本次检查方式文案。 */
export function describeCheckMethod(
  value: ReanalyzeMethodValue,
  capabilities: ReanalyzeCapabilities | null,
): string {
  if (value.checkMethod === "inherit") return "按上次检查方式重新分析（逐条继承本地规则、大模型开关与原模型）";
  const profile = capabilities?.profiles.find((item) => item.id === value.aiProfileId);
  const modelText = profile ? `${profile.name} · ${profile.model}` : capabilities?.model || "系统当前模型";
  return `本地规则 + 大模型参与（本次模型：${modelText}）`;
}