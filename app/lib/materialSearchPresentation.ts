/**
 * 全局材料搜索的展示文案（WP2-C）。
 *
 * 与 WP2-A/B 同一条纪律：**技术码在后端，中文在展示层**。后端给出
 * `matched_fields`（`unit` / `historical_filename` / `job_id` …）与
 * `relationship_resolution`（`resolved` / `unavailable`），本模块负责把它们
 * 翻成用户看得懂的一句话。后端翻译会让同一原因在两端各写一份，必然漂移。
 *
 * 这些文案承担一个具体责任：让每条搜索结果**自证为什么会出现**（§三十一）。
 * 用户搜 `规划和自然资源局 本部 2024 决算` 时，结果里若没有"命中：本部单位"
 * 这类标注，就只能自己猜——而搜索结果最危险的形态正是"看起来毫无关系"。
 */

/** 命中字段码 → 中文标签。未知码原样返回，不吞掉信息（便于发现契约漂移）。 */
export function presentMatchedField(code: unknown): string {
  const key = String(code ?? "");
  const labels: Record<string, string> = {
    unit: "单位名称",
    department: "主管部门",
    jurisdiction: "区县",
    current_filename: "当前文件名",
    historical_filename: "历史文件名",
    job_id: "处理任务 ID",
    fiscal_year: "财政年度",
    report_kind: "文种",
    relationship: "主体关系",
  };
  return labels[key] ?? key;
}

/** 命中原因列表的文案：按后端给的顺序拼接，不做二次排序。 */
export function presentMatchedFields(codes: unknown): string {
  if (!Array.isArray(codes) || codes.length === 0) {
    return "命中原因未知";
  }
  return codes.map((code) => presentMatchedField(code)).join(" · ");
}

/**
 * 「本部/本级」这条关系约束是否真的解析成功。
 *
 * `unavailable` 必须显式告诉用户：**不是"没有这种材料"，而是这次没法按关系筛**。
 * 两者在界面上长得一样，但用户下一步动作完全不同（前者改词、后者找运维）。
 */
export function presentRelationshipResolution(resolution: unknown): {
  resolved: boolean;
  notice: string | null;
} {
  if (String(resolution ?? "") === "unavailable") {
    return {
      resolved: false,
      notice:
        "本次无法解析「本部/本级」：组织目录不可用。该条件已按「不命中任何材料」处理，结果可能不完整。",
    };
  }
  return { resolved: true, notice: null };
}

/** 历史命中提示（历史文件名 / 历史版本任务各一条，不能混为一谈）。 */
export const HISTORICAL_FILENAME_NOTICE = "命中历史文件（不是当前版本）";
export const HISTORICAL_JOB_NOTICE = "命中历史版本的处理任务";

/** 输入为空时的引导文案（§四十三）。 */
export const SEARCH_IDLE_HINT = "可搜索文件名、主管部门、单位、财政年度、预算/决算、任务 ID。";
export const SEARCH_IDLE_EXAMPLE = "规划和自然资源局 本部 2024 决算";

/** 空结果文案：不得暗示"材料存在但你无权"，那会泄露存在性（§四十四）。 */
export const SEARCH_EMPTY_TEXT = "没有找到符合条件且你有权限查看的材料";

/** 查询串长度约束（与后端 `MIN_QUERY_LENGTH` / `MAX_QUERY_LENGTH` 同口径）。 */
export const SEARCH_MIN_QUERY_LENGTH = 2;
export const SEARCH_MAX_QUERY_LENGTH = 200;
