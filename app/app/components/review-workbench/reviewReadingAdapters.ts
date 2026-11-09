/**
 * 问题解释区的纯数据整理：计算明细、金额对照、摘要去重、证据角色名。
 *
 * 计算明细只来自问题自带的证据引用（table_refs 里 role=parent/child 且带
 * current_value 的勾稽操作数），不从标题文字里抠数字、不补造明细：规则没有
 * 输出逐项操作数时，界面明确说明“本条未提供逐项明细”。
 */
import type { Problem, ProblemEvidenceRef } from "../../../lib/mock";

export interface CalculationRow {
  key: string;
  label: string;
  page: number;
  valueText: string;
  located: boolean;
  blankAsZero?: boolean;
}

export interface CalculationGroup {
  relationId: string;
  total: CalculationRow;
  items: CalculationRow[];
  /** 明细合计；任一金额无法解析时为 null，不给出半截加总。 */
  itemsSum: string | null;
  /** 表内合计 − 明细合计。 */
  difference: string | null;
  roundingSummary: string | null;
}

export interface AmountComparison {
  labels: [string, string, string];
  values: [string, string, string];
}

const AMOUNT_RE = /^-?\d+(?:\.\d+)?$/;
const SCALE = 10000;

function parseAmount(text: string): { scaled: number; decimals: number } | null {
  const normalized = text.replace(/[,，\s]/g, "").replace(/万元$/, "");
  if (!AMOUNT_RE.test(normalized)) return null;
  const decimals = normalized.includes(".") ? normalized.split(".")[1].length : 0;
  if (decimals > 4) return null;
  return { scaled: Math.round(Number(normalized) * SCALE), decimals };
}

function formatScaled(value: number, decimals: number): string {
  return (value / SCALE).toFixed(decimals);
}

export function evidenceRoleLabel(role: string): string {
  if (role === "parent") return "合计项";
  if (role === "child") return "明细项";
  // 规则按序号命名的“原文位置1/2”对审核员没有区分意义。
  if (/^原文位置\d*$/.test(role)) return "原文";
  return role;
}

/** 位置文字去掉页码，再去掉同组各项都相同的部分（表名、口径），只留区分各项的名称。 */
function distinctLabels(refs: ProblemEvidenceRef[]): string[] {
  const parts = refs.map((ref) => ref.locationText.split(" · ").filter((part) => !/^第 \d+ 页$/.test(part)));
  const shared = parts.length > 1
    ? parts[0].filter((part) => parts.every((list) => list.includes(part)))
    : [];
  return parts.map((list, index) => {
    const own = list.filter((part) => !shared.includes(part));
    return (own.length > 0 ? own : list).join(" · ") || `第 ${refs[index].page} 页`;
  });
}

export function buildCalculationGroups(problem: Problem): CalculationGroup[] {
  const byRelation = new Map<string, ProblemEvidenceRef[]>();
  for (const ref of problem.evidenceRefs ?? []) {
    if (!ref.relationId || (ref.role !== "parent" && ref.role !== "child") || !ref.valueText) continue;
    const refs = byRelation.get(ref.relationId) ?? [];
    // 同一操作数被多条引用指向时只算一次。
    if (!refs.some((item) => (item.operandId && item.operandId === ref.operandId) || item.key === ref.key)) refs.push(ref);
    byRelation.set(ref.relationId, refs);
  }
  const groups: CalculationGroup[] = [];
  const seenCalculations = new Set<string>();
  byRelation.forEach((refs, relationId) => {
    const parents = refs.filter((ref) => ref.role === "parent");
    const children = refs.filter((ref) => ref.role === "child");
    if (parents.length !== 1 || children.length === 0) return;
    const ordered = [parents[0], ...children];
    // 同一组原格被不同规则引用时只展示一遍；相同金额不能作为同一算式依据。
    if (ordered.every((ref) => ref.verificationStatus === "verified" && ref.bbox)) {
      const identity = ordered.map((ref) => JSON.stringify([
        ref.role, ref.page, ref.sourceCellId || ref.bbox, ref.locationText, ref.valueText, Boolean(ref.blankAsZero),
      ]));
      const signature = JSON.stringify([identity[0], identity.slice(1).sort()]);
      if (seenCalculations.has(signature)) return;
      seenCalculations.add(signature);
    }
    const labels = distinctLabels(ordered);
    const rows = ordered.map((ref, index): CalculationRow => ({
      key: ref.key,
      label: labels[index],
      page: ref.page,
      valueText: ref.valueText ?? "",
      located: Boolean(ref.bbox),
      blankAsZero: ref.blankAsZero,
    }));
    const total = parseAmount(rows[0].valueText);
    const items = rows.slice(1).map((row) => parseAmount(row.valueText));
    let itemsSum: string | null = null;
    let difference: string | null = null;
    if (total && items.every((item) => item !== null)) {
      const parsed = items as Array<{ scaled: number; decimals: number }>;
      const decimals = Math.max(total.decimals, ...parsed.map((item) => item.decimals));
      const sum = parsed.reduce((acc, item) => acc + item.scaled, 0);
      itemsSum = formatScaled(sum, decimals);
      difference = formatScaled(total.scaled - sum, decimals);
    }
    const rounding = problem.roundingCases?.find((item) => item.relationId === relationId);
    groups.push({
      relationId,
      total: rows[0],
      items: rows.slice(1),
      itemsSum,
      difference,
      roundingSummary: rounding?.summary ?? null,
    });
  });
  return groups;
}

/** 金额三栏：优先用问题自带的当前值/应为/差额；否则只有一组勾稽时用表内合计/明细合计/差额。 */
export function buildAmountComparison(problem: Problem, groups: CalculationGroup[]): AmountComparison | null {
  if (problem.actual || problem.expected || problem.difference) {
    return {
      labels: ["当前值", "应为", "差额"],
      values: [problem.actual || "未提供", problem.expected || "未提供", problem.difference || "未提供"],
    };
  }
  const [group] = groups;
  if (groups.length !== 1 || group.itemsSum === null || group.difference === null) return null;
  return {
    labels: ["表内合计", "明细合计", "差额"],
    values: [group.total.valueText, group.itemsSum, group.difference],
  };
}

/** 说明与标题逐字重复时不再整段重复，只留下标题之外多出的部分。 */
export function detailSummary(title: string, description: string): string {
  const head = title.trim().replace(/[。.]$/, "");
  const body = description.trim();
  if (!body || body.replace(/[。.]$/, "") === head) return "";
  if (!head || !body.startsWith(head)) return body;
  return body.slice(head.length)
    .replace(/^[，,；;：:\s]+/, "")
    .replace(/[。.]$/, "")
    .replace(/^[（(](.*)[)）]$/, "$1")
    .trim();
}
