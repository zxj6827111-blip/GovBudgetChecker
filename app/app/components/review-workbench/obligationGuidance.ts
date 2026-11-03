/**
 * 「未能自动核对」条目（补核义务）的人工提示：把义务编号 / 引擎原因翻译成"要你确认什么、怎么判断、证据怎么写"。
 *
 * 为什么单独一层
 * --------------
 * 补核页原来的主文案只有「义务标题 + 引擎原因」。用户看到的是
 * "封面要素：报告年度、单位全称、文种" 和 "封面年度缺少正文独立依据"，
 * 很容易误读成"封面没写年度"——实际上系统是在说"正文里找不到独立的年份锚点"。
 * 标题是检查项名称，不是错误内容。本模块只做展示层翻译，不改变任何判定：
 * 三个按钮的语义、服务端门禁、证据纪律都保持原样。
 *
 * 两条不可越界的纪律
 * ------------------
 * 1. **不把"系统取数限制"说成"材料错误"。** 分类只是提示，不是结论；
 *    真正的结论仍由人工点按钮写入。分类不确定时一律回落到「需人工判断」。
 * 2. **同源只做提示，不自动合并表态。** 两条补核可能指向同一处空白/旧科目名，
 *    但每条义务仍有独立的乐观锁 revision；本模块只提示"建议同结论"，
 *    不替用户批量写入——那会绕开并发红线。
 */

import type { ObligationReviewItemRecord } from "./reviewLifecycleAdapters";

export type ObligationGuidanceCategory = "material" | "limitation" | "review";

export type ObligationGuidanceTone = "review" | "processing" | "neutral";

export interface ObligationSameSourceHint {
  /** 同一份补核清单里的其他义务编号。 */
  obligationIds: string[];
  /** 已在问题清单里单独存在的同源 checker/问题编号。 */
  findingIds: string[];
  /** 一句话说明为什么同源、建议怎么处理。 */
  note: string;
}

export interface ObligationGuidance {
  category: ObligationGuidanceCategory;
  categoryLabel: string;
  categoryTone: ObligationGuidanceTone;
  /** 义务的检查依据（后端 basis）；旧后端没有时为 null。 */
  requirement: string | null;
  /** 系统卡点（后端 detail 原样透出）。 */
  blocker: string | null;
  /** 这条要你确认什么。 */
  whatToConfirm: string;
  /** 怎么判断该点哪个按钮。 */
  howToDecide: string;
  /** 证据位置/引用怎么写。 */
  evidenceExample: string;
  /** 同源提示；没有同源时为 null。 */
  sameSource: ObligationSameSourceHint | null;
}

const CATEGORY_META: Record<
  ObligationGuidanceCategory,
  { label: string; tone: ObligationGuidanceTone }
> = {
  material: { label: "疑似材料问题", tone: "review" },
  limitation: { label: "疑似取数限制", tone: "processing" },
  review: { label: "需人工判断", tone: "neutral" },
};

interface KnownGuidance {
  category: ObligationGuidanceCategory;
  whatToConfirm: string;
  howToDecide: string;
  evidenceExample: string;
}

/**
 * 已知义务的专用提示。键必须与 `src/engine/check_obligations.py` 的
 * `OBLIGATION_CATALOG` 一致；新增义务没在这里登记时走通用提示，
 * 不会因为漏登记而无法补核。
 */
const KNOWN_GUIDANCE: Record<string, KnownGuidance> = {
  "OBL-STRUCT-COVER": {
    category: "limitation",
    whatToConfirm:
      "封面三项：①报告年度；②单位全称；③文种（单位决算不得误写“部门决算”）。本次系统只卡在①的“正文独立年份锚点”，不是封面没写年度。",
    howToDecide:
      "封面年度与目录/部分标题一致、单位全称一致、文种正确 → 核对无误；任一项确实不对 → 确认有问题。",
    evidenceExample: "第1页封面；目录页；写明年度的部分标题页",
  },
  "OBL-STRUCT-EMPTY-STATEMENT": {
    category: "review",
    whatToConfirm:
      "总表里有一处空白金额格，系统不把空白当 0；空表说明已经有了不是问题，问题是总表空白格能不能由说明证明为零。",
    howToDecide:
      "空表说明（如第15/16页）能证明该空白格确为零 → 核对无误；说明缺失、表名不符或金额对不上 → 确认有问题。",
    evidenceExample: "总表空白行所在页 + 对应空表说明页",
  },
  "OBL-CROSS-FISCAL-TOTAL": {
    category: "review",
    whatToConfirm:
      "总表该行的「合计 = 一般公共 + 政府性基金 + 国有资本」三个分项是否都能取到数。",
    howToDecide:
      "分项空白但有总计行/空表说明证明为零 → 核对无误；分项金额缺失且无法证明 → 确认有问题。",
    evidenceExample: "总表所在页 + 行号；对应基金表/说明页",
  },
  "OBL-TXT-T5": {
    category: "material",
    whatToConfirm:
      "说明里的科目名称、金额层级是否与表内同一编码的科目一致。",
    howToDecide:
      "先查是否旧科目名、漏字或错字，再看金额是「类/款合计」还是「项」的金额；表内确无该科目但说明金额为 0、合计能对上，可按零值漏列核对无误；否则确认有问题。",
    evidenceExample: "说明所在页 + 表内对应科目编码行（如 20805/2080505）",
  },
  "OBL-CROSS-SAN-GONG-ECON": {
    category: "material",
    whatToConfirm:
      "基本支出表的科目编码与名称是否指向同一业务项。",
    howToDecide:
      "若连续多行编码整体错位（常见为缺一行） → 确认有问题；若只是个别名称差异 → 说明里写清后确认问题。",
    evidenceExample: "基本支出表页码 + 错位起止行/科目编码",
  },
  "OBL-TXT-FINAL-DETAIL": {
    category: "review",
    whatToConfirm:
      "支出决算具体情况是否逐项列到「项」，每项是否有年初预算和支出决算金额。",
    howToDecide:
      "确实漏项/漏金额 → 确认有问题；只是跨行、表述差异或系统未识别到 → 核对后可核对无误。",
    evidenceExample: "说明所在页 + 表内项级科目行",
  },
  "OBL-TXT-T4": {
    category: "material",
    whatToConfirm:
      "财政拨款收支总体情况说明是否有可绑定的总计金额和单位。",
    howToDecide:
      "缺标题/缺金额/缺单位 → 确认有问题；金额在但表述跨行 → 核对无误。",
    evidenceExample: "说明所在页 + 财政拨款收支总表页",
  },
  "OBL-TREND-COMPLETION-RATE": {
    category: "review",
    whatToConfirm:
      "完成率的分子、分母是否与同口径预算数/决算数对得上。",
    howToDecide:
      "同口径能复算 → 核对无误；分子分母口径不同或算不出 → 确认有问题。",
    evidenceExample: "完成率说明所在页 + 对应预算/决算表页",
  },
  "OBL-NARRATIVE-AMOUNT": {
    category: "review",
    whatToConfirm:
      "说明中的金额/占比能否按原文复算（尤其是「总额，其中：分项」）。",
    howToDecide:
      "分项能唯一列出且加总一致 → 核对无误；确实漏项或加总不符 → 确认有问题。",
    evidenceExample: "说明所在页 + 对应表页",
  },
  "OBL-STRUCT-ORG-NAME": {
    category: "review",
    whatToConfirm:
      "全篇单位名称是否一致、是否为规范全称，表头「编制单位」是否为空。",
    howToDecide:
      "名称一致且为全称 → 核对无误；漏字/多字/简称混用 → 确认有问题。",
    evidenceExample: "封面；各表表头；说明首段",
  },
  "OBL-STRUCT-TOC-ALIGN": {
    category: "review",
    whatToConfirm:
      "目录标题与正文标题是否逐项一致（名称、编号、顺序）。",
    howToDecide:
      "只差模板措辞或跨行 → 核对无误；编号/名称/顺序确实不一致 → 确认有问题。",
    evidenceExample: "目录页 + 对应正文标题页",
  },
};

/** 强材料信号：出现这些词时，原文大概率真的有问题。 */
const MATERIAL_PATTERNS: RegExp[] = [
  /身份不一致/,
  /行内编码与名称指向不同业务项/,
  /未披露/,
  /漏列/,
  /未列示/,
  /找不到同名科目/,
  /名称不一致/,
];

/** 取数限制信号：出现这些词时，更可能是引擎没取到数，而不是材料错。 */
const LIMITATION_PATTERNS: RegExp[] = [
  /缺少正文独立依据/,
  /未识别到/,
  /未找到同业务口径/,
  /未唯一列出/,
  /无法绑定/,
  /无可解析行/,
];

const DEFAULT_GUIDANCE: Omit<KnownGuidance, "category"> = {
  whatToConfirm:
    "核对原文里这项检查本身是否成立；系统卡在哪一步见上方“为什么没能自动核对”。",
  howToDecide:
    "无问题 → 核对无误；确有问题 → 确认有问题；本材料不涉及 → 不适用。",
  evidenceExample: "页码 + 表名/章节 + 科目编码（越具体越好）",
};

const REASON_CODE_LABELS: Record<string, string> = {
  operand_missing: "操作数缺失",
  operand_ambiguous: "操作数不唯一",
  row_code_missing: "缺科目编码",
  row_identity_duplicate: "行身份重复",
  row_level_unresolved: "层级未定",
  column_identity_unconfirmed: "列身份未确认",
  column_width_drift: "跨页列宽漂移",
  cell_blank_semantics_unknown: "空白格语义未知",
  cell_low_confidence: "单元格置信度不足",
  cell_unbound: "单元格未绑定",
  table_missing: "缺表",
  table_mapping_incomplete: "表映射不完整",
  scope_unconfirmed: "汇总/资金口径未确认",
  subject_unconfirmed: "材料主体未确认",
  subject_conflict: "主体冲突",
  text_statement_unparsed: "说明表述未解析",
  text_statement_absent: "原文未见表述",
  text_binding_failed: "说明条目绑不到表内科目",
  name_mismatch_confirmed: "名称不一致",
  code_name_conflict: "编码与名称冲突",
  subject_name_unlocated: "疑似旧科目名无法定位",
  rule_no_reason: "规则未给出具体原因",
  parse_error: "解析失败",
  execution_error: "规则执行异常",
  ai_not_requested: "未请求语义复核",
};

const ACTION_LABELS: Record<string, string> = {
  system_recheck: "系统定向补取",
  tech_fix: "技术修复解析/规则",
  human_confirm: "人工核对原文",
  rerun_ai: "补跑语义复核",
};

const DIMENSION_LABELS: Record<string, string> = {
  organization: "主体",
  year: "年度",
  fund_scope: "资金口径",
  budget_stage: "预算阶段",
  org_scope: "汇总范围",
  instance_uid: "表实例",
  row: "行",
  column: "列",
  cell: "单元格",
};

const MATERIAL_REASON_CODES = new Set([
  "name_mismatch_confirmed",
  "text_statement_absent",
  "table_missing",
  "code_name_conflict",
  "subject_name_unlocated",
]);

function rowKeyLabel(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (value && typeof value === "object") {
    const text = Object.values(value as Record<string, unknown>)
      .filter((part) => typeof part === "string" && part.trim())
      .join(" / ");
    return text || null;
  }
  return null;
}

function pageLabel(value: unknown): string {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? `第${value}页`
    : "页码未确认";
}

function missingOperandLabel(entry: {
  source: string;
  role?: string;
  table_code?: string | null;
  section?: string | null;
  page?: number | null;
  row_key?: unknown;
  column?: string | null;
}): string {
  const role = entry.role?.trim();
  const row = rowKeyLabel(entry.row_key);
  const column = entry.column?.trim();
  if (entry.source === "text") {
    const where = entry.section || "说明章节";
    return [
      `说明「${where}」`,
      pageLabel(entry.page),
      role ? `角色：${role}` : "",
      row ? `行：${row}` : "",
      column ? `列：${column}` : "",
    ].filter(Boolean).join(" / ");
  }
  return [
    `表${entry.table_code || "表格"}`,
    pageLabel(entry.page),
    role ? `角色：${role}` : "",
    row ? `行${row}` : "",
    column ? `列${column}` : "",
  ].filter(Boolean).join(" / ");
}

function candidateLabel(entry: {
  source: string;
  table_code?: string | null;
  section?: string | null;
  page?: number | null;
  cell_id?: string | null;
  value?: unknown;
}): string {
  const value = entry.value === null || entry.value === undefined
    ? ""
    : String(entry.value).trim();
  if (entry.source === "text") {
    return `说明「${entry.section || "未识别章节"}」${pageLabel(entry.page)}${value ? `「${value}」` : ""}`;
  }
  return `表${entry.table_code || "未识别表"} ${pageLabel(entry.page)} 格${entry.cell_id || "未识别"}${value ? `（值：${value}）` : ""}`;
}

/** 后端在 rule_no_reason 时保留的规则业务说明；只做带标签的展示提取。 */
function retainedRuleExplanation(detail: string | null | undefined): string | null {
  const match = /规则说明：([\s\S]*?)(?:。下一步：|$)/.exec(String(detail ?? ""));
  const value = match?.[1]?.trim().replace(/[。；;]+$/, "");
  return value || null;
}

/** 用结构化卡点回答“卡在哪、缺什么、下一步谁处理”；旧结果返回 null。 */
export function describeObligationDiagnosis(
  item: ObligationReviewItemRecord,
): string | null {
  const codes = item.reason_codes ?? [];
  const blocked = item.blocked_by ?? [];
  const missing = item.missing_operands ?? [];
  const candidates = item.candidates ?? [];
  const action = item.next_action ?? null;
  if (!codes.length && !blocked.length && !missing.length && !candidates.length && !action) {
    return null;
  }
  const parts: string[] = [];
  if (blocked.length) {
    parts.push(
      `上游未确认：${blocked
        .map((entry) => `${DIMENSION_LABELS[entry.dimension] ?? entry.dimension}（${entry.ref}）`)
        .join("、")}`,
    );
  }
  if (missing.length) {
    parts.push(`缺少：${missing.map((entry) => missingOperandLabel(entry)).join("、")}`);
  }
  if (candidates.length) {
    parts.push(`候选：${candidates.map((entry) => candidateLabel(entry)).join("；")}`);
  }
  if (codes.length) {
    parts.push(`原因码：${codes.map((code) => REASON_CODE_LABELS[code] ?? code).join("、")}`);
  }
  if (codes.includes("rule_no_reason")) {
    const retained = retainedRuleExplanation(item.detail);
    if (retained) {
      parts.push(`规则说明：${retained}`);
    }
  }
  if (action) {
    parts.push(`下一步：${ACTION_LABELS[action] ?? action}`);
  }
  return parts.join("；") || null;
}

function structuredCategory(item: ObligationReviewItemRecord): ObligationGuidanceCategory | null {
  const codes = item.reason_codes ?? [];
  const action = item.next_action;
  if (!action && codes.length === 0) return null;
  if (action === "human_confirm") {
    return codes.some((code) => MATERIAL_REASON_CODES.has(code)) ? "material" : "review";
  }
  if (action === "tech_fix" || action === "system_recheck" || action === "rerun_ai") {
    return "limitation";
  }
  return null;
}

function classify(
  item: ObligationReviewItemRecord,
  known: KnownGuidance | undefined,
): ObligationGuidanceCategory {
  const structured = structuredCategory(item);
  if (structured) return structured;
  const detail = String(item.detail ?? "");
  // 强材料信号优先：即使已知义务被登记为 review，也提示"疑似材料问题"。
  if (MATERIAL_PATTERNS.some((pattern) => pattern.test(detail))) {
    return "material";
  }
  if (known?.category === "material") {
    return "material";
  }
  if (LIMITATION_PATTERNS.some((pattern) => pattern.test(detail))) {
    return "limitation";
  }
  return known?.category ?? "review";
}

/** 旧结果 = 缺 `legacy_coverage_terms` 字段或为 null；列表（含 []）是新结果。 */
function isLegacyResult(item: ObligationReviewItemRecord): boolean {
  return item.legacy_coverage_terms === undefined || item.legacy_coverage_terms === null;
}

/**
 * 同源判断的可追溯文本。
 *
 * - 旧结果：沿用第一阶段的 title/detail 语义；
 * - 新结果：优先使用 DH-03b 保留的“规则说明”冻结业务文本，不使用被改写后的
 *   结构化 detail；只有冻结文本缺失时才回退完整 detail（旧语义兜底）。
 * `includeTitle` 只给财政拨款总表一侧使用：义务标题本身就是稳定的表身份。
 */
function sameSourceEvidenceText(
  item: ObligationReviewItemRecord,
  { includeTitle = false }: { includeTitle?: boolean } = {},
): string {
  const title = includeTitle ? String(item.title ?? "") : "";
  if (isLegacyResult(item)) {
    return `${title} ${item.detail ?? ""}`;
  }
  const frozen = retainedRuleExplanation(item.detail);
  const fallback = frozen === null ? String(item.detail ?? "") : "";
  return `${title} ${frozen ?? ""} ${fallback}`;
}

/** FIN_04 的稳定表身份：中文表名、简称或冻结表代号，不依赖叙述句式。 */
const FIN04_IDENTITY_RE = /财政拨款收入支出决算总表|财政拨款收支总表|FIN_04_fiscal_grant_total/;

function mentionsFiscalGrantTotal(item: ObligationReviewItemRecord): boolean {
  return FIN04_IDENTITY_RE.test(
    sameSourceEvidenceText(item, { includeTitle: true }),
  );
}

function mentionsEmptyStatement(item: ObligationReviewItemRecord): boolean {
  return FIN04_IDENTITY_RE.test(
    sameSourceEvidenceText(item),
  );
}

function subjectBindingUnlocated(item: ObligationReviewItemRecord): boolean {
  // 旧/新结果不能靠 reason_codes 是否为空判断：当前 API 会把旧任务的
  // reason_codes 投影成 []。以 legacy_coverage_terms 的 null/缺失（旧）
  // 与列表（新，含 []）为契约；旧结果回退 detail，新结果只读原因码。
  if (isLegacyResult(item)) {
    return /找不到同名科目/.test(String(item.detail ?? ""));
  }
  if (item.reason_codes?.includes("subject_name_unlocated")) {
    return true;
  }
  // text_binding_failed 比“找不到同名科目”宽；只有同时保留了旧覆盖词
  // （说明确实定位到了具体科目名）才按同源处理，避免把金额绑定失败误报成
  // 科目名称问题。
  return (item.reason_codes ?? []).includes("text_binding_failed")
    && (item.legacy_coverage_terms?.length ?? 0) > 0;
}

/**
 * 同源提示。
 *
 * 只登记**能由现有字段可靠判断**的两类：
 * - 空表说明 + 财政拨款总表合计：同一张 FIN_04 的空白金额格；
 * - T5 名称绑定 + V33-227 / 支出决算具体情况漏列：同一科目名称/漏列根因。
 *
 * 不做"同表即同源"的泛化：同一张表上的两条义务可能完全无关，
 * 泛化会把用户引到错误的结论上。
 */
function buildSameSource(
  item: ObligationReviewItemRecord,
  allItems: ObligationReviewItemRecord[],
): ObligationSameSourceHint | null {
  const obligationIds: string[] = [];
  const findingIds: string[] = [];
  const notes: string[] = [];

  if (item.obligation_id === "OBL-STRUCT-EMPTY-STATEMENT" && mentionsEmptyStatement(item)) {
    const other = allItems.find(
      (candidate) =>
        candidate.obligation_id === "OBL-CROSS-FISCAL-TOTAL" &&
        mentionsFiscalGrantTotal(candidate),
    );
    if (other) {
      obligationIds.push("OBL-CROSS-FISCAL-TOTAL");
      notes.push(
        "与「财政拨款收支总表合计一致性」指向同一处空白金额格，建议两条保持同一结论。",
      );
    }
  }

  if (item.obligation_id === "OBL-CROSS-FISCAL-TOTAL" && mentionsFiscalGrantTotal(item)) {
    const other = allItems.find(
      (candidate) =>
        candidate.obligation_id === "OBL-STRUCT-EMPTY-STATEMENT" &&
        mentionsEmptyStatement(candidate),
    );
    if (other) {
      obligationIds.push("OBL-STRUCT-EMPTY-STATEMENT");
      notes.push(
        "与「空表/无此项说明与总表零值确认」指向同一处空白金额格，建议两条保持同一结论。",
      );
    }
  }

  if (item.obligation_id === "OBL-TXT-T5" && subjectBindingUnlocated(item)) {
    if ((item.checkers ?? []).includes("V33-227")) {
      findingIds.push("V33-227");
      notes.push(
        "V33-227 已单独记录科目名称不一致，补核说明可直接引用该问题的页码/科目。",
      );
    }
    const detailObligation = allItems.find(
      (candidate) => candidate.obligation_id === "OBL-TXT-FINAL-DETAIL",
    );
    if (detailObligation) {
      obligationIds.push("OBL-TXT-FINAL-DETAIL");
      notes.push(
        "与「支出决算具体情况漏列」可能同源，建议互相引用页码/科目。",
      );
    }
  }

  if (obligationIds.length === 0 && findingIds.length === 0) {
    return null;
  }
  return {
    obligationIds: Array.from(new Set(obligationIds)),
    findingIds: Array.from(new Set(findingIds)),
    note: notes.join(" "),
  };
}

/**
 * 生成一条补核的展示提示。
 *
 * @param item 当前义务（含后端透出的 basis / checkers）
 * @param allItems 同一份补核清单，用于判断同源义务；缺省时不做同源提示
 */
export function obligationGuidance(
  item: ObligationReviewItemRecord,
  allItems: ObligationReviewItemRecord[] = [],
): ObligationGuidance {
  const known = KNOWN_GUIDANCE[item.obligation_id];
  const category = classify(item, known);
  const meta = CATEGORY_META[category];
  const template = known ?? { ...DEFAULT_GUIDANCE, category };
  return {
    category,
    categoryLabel: meta.label,
    categoryTone: meta.tone,
    requirement: item.basis?.trim() || null,
    blocker: (describeObligationDiagnosis(item) ?? item.detail?.trim()) || null,
    whatToConfirm: template.whatToConfirm,
    howToDecide: template.howToDecide,
    evidenceExample: template.evidenceExample,
    sameSource: buildSameSource(item, allItems),
  };
}
