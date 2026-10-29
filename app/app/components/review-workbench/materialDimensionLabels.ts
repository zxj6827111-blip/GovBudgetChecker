// 材料写法与金额范围面板的业务文案；主界面只显示业务状态，哈希等审计细节放在依据详情。
type Snapshot = Record<string, any> | null | undefined;

export const WRITING_LABELS: Record<string, string> = { unit: "单位", department: "部门", unknown: "暂不确定" };
export const AMOUNT_LABELS: Record<string, string> = { unit_self: "仅本级", department_summary: "汇总所属单位", unknown: "暂不确定" };
const ADOPTION_LABELS: Record<string, string> = {
  issued: "当年正式下发",
  verified_same: "已核对与旧版一致",
  not_received: "未收到新版，沿用参考",
  historical_reference: "历史参考",
};

function bindingBasis(snapshot: Snapshot): Record<string, any> {
  const basis = snapshot?.basis;
  if (typeof basis !== "string") return basis && typeof basis === "object" ? basis : {};
  try {
    const parsed = JSON.parse(basis);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function adoptionLabel(snapshot: Snapshot): string {
  const kind = snapshot?.adoption_kind;
  if (kind === "rollback") {
    const original = ADOPTION_LABELS[bindingBasis(snapshot).original_adoption_kind];
    return original ? `${original}（恢复历史修订）` : "恢复历史修订（原沿用方式待核）";
  }
  return ADOPTION_LABELS[kind] || "沿用方式未记录";
}

export function templateSummary(snapshot: Snapshot): string {
  if (!snapshot?.available) return `模板对照未启用：${snapshot?.reason || "年度模板未绑定"}`;
  const source = snapshot.source_report_year, target = snapshot.target_year;
  const span = source && target && source !== target ? `，${source} 年模板用于 ${target} 年度` : "";
  const reference = snapshot.capability_scope?.writing_auto ? "" : "；仅作参考，未参与写法自动判定";
  return `采用 ${snapshot.version || "未命名"} 版模板（${adoptionLabel(snapshot)}${span}）${reference}`;
}

export function auditDetail(snapshot: Snapshot, algorithm?: string): string {
  if (!snapshot?.available) return `算法：${algorithm || "未记录"}`;
  const hash = String(snapshot.effective_config_sha256 || snapshot.content_sha256 || "").slice(0, 12);
  return `发布包 ${snapshot.package_id || "未记录"} · 内容摘要 ${hash || "未记录"} · 年度绑定修订 ${snapshot.revision ?? "未记录"} · 算法 ${algorithm || "未记录"}`;
}

export function amountNeed(rules: unknown): string {
  const ids = Array.isArray(rules) ? rules.filter((r): r is string => typeof r === "string") : [];
  return ids.length
    ? `仍有 ${ids.length} 项检查因金额范围未确认而未完成：${ids.join("、")}。`
    : "当前没有检查因金额范围未确认而未完成，可暂不填写此项。";
}
