import assert from "node:assert/strict";

import {
  DEFAULT_REANALYZE_METHOD,
  buildReanalyzeRequestBody,
  canSubmitReanalyze,
  describeCheckMethod,
  formatInheritanceWarnings,
  inheritanceWarningsFromResponse,
  inheritBlockedReason,
  lastExecutionHint,
  lastRunSummary,
  type ReanalyzeCapabilities,
  type ReanalyzeOptionsPreview,
} from "../app/components/reanalyzeOptions";

const capabilities: ReanalyzeCapabilities = {
  enabled: true,
  active_profile_id: "model-b",
  profiles: [
    { id: "model-a", name: "模型A", model: "m-a" },
    { id: "model-b", name: "模型B", model: "m-b" },
  ],
};

function preview(overrides?: {
  inherit?: Partial<ReanalyzeOptionsPreview["inherit"]>;
  model?: Partial<ReanalyzeOptionsPreview["model"]>;
  last_execution?: Partial<ReanalyzeOptionsPreview["last_execution"]>;
}): ReanalyzeOptionsPreview {
  return {
    job_id: "job-1",
    inherit: {
      available: true,
      unknown_fields: [],
      message: null,
      mode: "dual",
      use_local_rules: true,
      use_ai_assist: true,
      ai_profile_id: "model-a",
      ai_configuration: { id: "model-a", name: "模型A", model: "m-a", revision: 1 },
      ...overrides?.inherit,
    },
    last_execution: {
      status: "done",
      analysis_conclusion: null,
      ai_requested: true,
      local_state: "completed",
      ai_state: "completed",
      ...overrides?.last_execution,
    },
    model: { state: "ok", message: null, profile: { id: "model-a", name: "模型A", model: "m-a" }, ...overrides?.model },
    capabilities,
  };
}

// --- 默认选项与请求体契约 -------------------------------------------------

assert.equal(DEFAULT_REANALYZE_METHOD.checkMethod, "inherit", "默认选中按上次检查方式");
assert.deepEqual(buildReanalyzeRequestBody(DEFAULT_REANALYZE_METHOD), { check_method: "inherit" });
assert.deepEqual(
  buildReanalyzeRequestBody({ checkMethod: "ai", aiProfileId: "model-b" }),
  { check_method: "ai", ai_profile_id: "model-b" },
);
assert.deepEqual(
  buildReanalyzeRequestBody({ checkMethod: "ai", aiProfileId: "" }),
  { check_method: "ai" },
  "空模型 = 系统当前模型，由后端解析，前端不静默填别的 id",
);

// --- 继承可用性判断 -------------------------------------------------------

assert.equal(inheritBlockedReason(preview()), null);
assert.equal(inheritBlockedReason(null), null, "批量无预览时继承恒可用（后端逐条继承）");
assert.match(
  inheritBlockedReason(preview({ inherit: { available: false, message: "该材料没有已记录的上次检查方式" } })) || "",
  /没有已记录/,
);
assert.match(
  inheritBlockedReason(preview({ model: { state: "deleted", message: "上次使用的模型「模型A」已删除" } })) || "",
  /已删除/,
);
assert.match(inheritBlockedReason(preview({ model: { state: "disabled", message: "大模型检查已停用" } })) || "", /停用/);
assert.equal(
  inheritBlockedReason(preview({ model: { state: "changed", message: "原模型配置已变化" } })),
  null,
  "配置变化不阻断继承，只提示",
);

// --- 上轮摘要与执行状态 ---------------------------------------------------

assert.equal(lastRunSummary(preview()), "上次：本地规则 + 大模型（模型A）");
assert.equal(lastRunSummary(preview({ inherit: { use_ai_assist: false, ai_profile_id: null, ai_configuration: null } })), "上次：仅本地规则");
assert.equal(lastRunSummary(preview({ inherit: { mode: "structured" } })), "上次：仅结构化入库");
assert.equal(
  lastRunSummary(preview({ inherit: { available: false, use_ai_assist: null, ai_profile_id: null, ai_configuration: null } })),
  "上次检查方式未知",
);
assert.equal(lastExecutionHint(preview()), null);
assert.match(
  lastExecutionHint(preview({ last_execution: { ai_state: "failed" } })) || "",
  /上次大模型检查失败.*仍按原选择启用/,
);
assert.match(
  lastExecutionHint(preview({ last_execution: { ai_state: "degraded" } })) || "",
  /部分完成/,
);
assert.match(lastExecutionHint(preview({ last_execution: { status: "error", ai_requested: false } })) || "", /上次分析失败/);

// --- 提交门禁 -------------------------------------------------------------

assert.equal(canSubmitReanalyze(DEFAULT_REANALYZE_METHOD, preview(), capabilities, false), true);
assert.equal(canSubmitReanalyze(DEFAULT_REANALYZE_METHOD, null, capabilities, false), false, "预览未加载不得提交继承");
assert.equal(canSubmitReanalyze(DEFAULT_REANALYZE_METHOD, null, capabilities, true), true, "批量继承无需预览");
assert.equal(
  canSubmitReanalyze(DEFAULT_REANALYZE_METHOD, preview({ model: { state: "deleted", message: "已删除" } }), capabilities, false),
  false,
);
assert.equal(canSubmitReanalyze({ checkMethod: "ai", aiProfileId: "model-b" }, null, capabilities, false), true);
assert.equal(
  canSubmitReanalyze({ checkMethod: "ai", aiProfileId: "" }, null, { ...capabilities, enabled: false }, false),
  false,
  "系统未启用大模型时显式 AI 不可提交",
);

// --- 提交文案 -------------------------------------------------------------

assert.match(describeCheckMethod(DEFAULT_REANALYZE_METHOD, capabilities), /按上次检查方式/);
assert.match(describeCheckMethod({ checkMethod: "ai", aiProfileId: "model-b" }, capabilities), /模型B · m-b/);
assert.match(describeCheckMethod({ checkMethod: "ai", aiProfileId: "" }, capabilities), /系统当前模型/);

// --- 继承告警解析（成功响应不得丢弃） --------------------------------------

assert.deepEqual(
  inheritanceWarningsFromResponse({ inheritance_warnings: ["原模型配置已变化（model）"] }),
  ["原模型配置已变化（model）"],
);
assert.deepEqual(
  inheritanceWarningsFromResponse({ inheritance_warnings: ["  告警  ", "", 42, null] }),
  ["告警"],
  "只保留非空字符串告警",
);
assert.deepEqual(inheritanceWarningsFromResponse({}), []);
assert.deepEqual(inheritanceWarningsFromResponse(null), []);
assert.deepEqual(inheritanceWarningsFromResponse("not-json-object"), []);
assert.equal(
  formatInheritanceWarnings(["告警一", "告警二"]),
  "告警一；告警二",
);

// 阻断态默认必须保留继承选中且不可提交；只有用户显式选择第二项才放行。
assert.equal(inheritBlockedReason(preview({ inherit: { available: false, message: "历史未知" } })), "历史未知");
assert.equal(
  canSubmitReanalyze(DEFAULT_REANALYZE_METHOD, preview({ inherit: { available: false, message: "历史未知" } }), capabilities, false),
  false,
);
assert.equal(
  canSubmitReanalyze({ checkMethod: "ai", aiProfileId: "model-b" }, preview({ inherit: { available: false, message: "历史未知" } }), capabilities, false),
  true,
  "用户主动选择第二项后才允许提交",
);

console.log("reanalyzeOptions: all assertions passed");