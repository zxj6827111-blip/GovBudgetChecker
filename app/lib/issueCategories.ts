import type { Problem } from "./mock";

/** 业务分类与来源、核实状态分开；规则和 AI 发现同类问题时共用一个入口。 */
export const ISSUE_CATEGORIES = [
  { id: "structure", label: "材料结构与披露", description: "缺表、空表说明、章节及目录" },
  { id: "amount", label: "金额与数量勾稽", description: "表文金额、总分合计及数量关系" },
  { id: "percentage", label: "占比与同比计算", description: "分母、占比、同比和完成率" },
  { id: "change", label: "增减方向与原因", description: "比较方向、原因矛盾及原因充分性" },
  { id: "scope", label: "科目名称与主体口径", description: "类款项名称、科目用途及主体自称" },
  { id: "number_format", label: "金额和比例格式", description: "千分位、小数位及数字书写" },
  { id: "wording", label: "文字与模板规范", description: "重复词、多字漏字、模板残留及序号" },
  { id: "coverage", label: "识别证据与检查范围", description: "识别待核、证据不足和未覆盖范围" },
  { id: "other", label: "其他待核事项", description: "尚不能归入具体业务类别的条目" },
] as const;

export type IssueCategoryId = typeof ISSUE_CATEGORIES[number]["id"];
type CategoryInput = Pick<Problem, "ruleId" | "title" | "description"> & Partial<Pick<Problem, "source" | "category">>;

/** 只分类，不判定真假，不合并 issue 身份，也不改变完成门禁。 */
export function issueCategoryId(problem: CategoryInput): IssueCategoryId {
  if (problem.source === "structured_ingest") return "coverage";
  const text = `${problem.title} ${problem.description}`;
  const rule = problem.ruleId.toUpperCase();
  if (/证据完整率|应检查事项|未实际覆盖|检查范围|未覆盖|规则因数据缺失/.test(text)) return "coverage";
  // 格式规则优先于通用“金额不一致”和 AI-TEXT，避免同一类按发现来源拆开。
  if (rule === "CMM-010" || /千分位|小数位|数字格式|金额书写格式|保留两位小数/.test(text)) return "number_format";
  // 补核卡与正式名称问题按同一业务分类，保留每项金额检查的独立处理身份。
  if (/单位名称.*(?:不一致|冲突)|原文包含其他或未确认主体|(?:项|款|类)“[^”]+”在表内找不到同名科目/.test(text)) return "scope";
  if (["BUD-109", "V33-227", "CMM-018"].includes(rule)
    || rule.startsWith("AI-CODE-") || /主体口径|类款项|科目.*(?:名称|编码|层级|用途).*?(?:错误|不一致|不匹配)|类级科目名称/.test(text)) return "scope";
  if (rule.startsWith("OBL-TREND-") || ["V33-234", "V33-249"].includes(rule)
    || /占比.*(?:分母|不一致|错误)|同比比例|比例.*(?:计算错误|不一致)|占比合计/.test(text)) return "percentage";
  if (rule === "V33-110" || /^AI-(?:DIR|DESC)-/.test(rule)
    || /比较方向|增减方向.*(?:不一致|矛盾)|增减原因|缺少实质|原因.*(?:矛盾|相反)/.test(text)) return "change";
  if (["V33-112", "CMM-005", "CMM-008", "CMM-014"].includes(rule)
    || /^AI-(?:TPL|TEXT)-/.test(rule)
    || /占位符|模板残留|二选一|重复词|重复字|重复短语|重复表述|多余字词|错别字|标点|序号体例|编号.*(?:跳|重)/.test(text)) return "wording";
  if (["V33-002", "V33-109", "V33-114", "V33-201"].includes(rule)
    || /缺失表|缺少表|缺表|空表|无数据说明|缺少.*(?:章节|说明)|必备章节|目录|封面|空白页|表名.*不一致/.test(text)) return "structure";
  if (["V33-105", "V33-107", "V33-117", "V33-222", "V33-223", "V33-251"].includes(rule)
    || /表文|勾稽|合计|总计|分项|之和|平衡|差额|金额.*(?:不一致|错误)|数量.*(?:不一致|错误)|总数/.test(text)
    || /^AI-XSEC-/.test(rule)) return "amount";
  // 兼容已分类的历史条目，不再把“AI 智能分析”当成一种业务问题。
  return ISSUE_CATEGORIES.find((category) => category.label === problem.category)?.id ?? "other";
}

export function issueCategory(problem: CategoryInput) {
  return ISSUE_CATEGORIES.find((category) => category.id === issueCategoryId(problem))!;
}

export interface IssueCategoryGroup {
  id: IssueCategoryId;
  label: string;
  description: string;
  items: Problem[];
}

/** 一类只产生一个展示组；每条原始关系、证据和处理记录仍可独立访问。 */
export function groupProblemsByCategory(problems: readonly Problem[]): IssueCategoryGroup[] {
  const buckets = new Map<IssueCategoryId, Problem[]>();
  for (const problem of problems) {
    const id = issueCategoryId(problem);
    const items = buckets.get(id) ?? [];
    items.push(problem);
    buckets.set(id, items);
  }
  return ISSUE_CATEGORIES.flatMap((category) => {
    const items = buckets.get(category.id);
    return items?.length ? [{ ...category, items }] : [];
  });
}
