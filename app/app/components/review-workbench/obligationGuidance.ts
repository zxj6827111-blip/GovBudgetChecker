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

function classify(
  item: ObligationReviewItemRecord,
  known: KnownGuidance | undefined,
): ObligationGuidanceCategory {
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

/** 历史匹配器：财政总表侧看 title+detail；空表说明侧只看 detail。 */
function mentionsFiscalGrantTotal(item: ObligationReviewItemRecord): boolean {
  return /财政拨款收支总表|财政拨款收入支出决算总表/.test(
    `${item.title ?? ""} ${item.detail ?? ""}`,
  );
}

function mentionsEmptyStatement(item: ObligationReviewItemRecord): boolean {
  return /财政拨款收入支出决算总表/.test(String(item.detail ?? ""));
}

const FIN_04_KEY = "FIN_04_fiscal_grant_total";

/** 财政总表侧的稳定表题（义务目录里的表题，不是材料原文）。 */
const LEGACY_TOTAL_TITLES: ReadonlySet<string> = new Set([
  "财政拨款收支总表",
  "财政拨款收入支出决算总表",
]);

/** 新字段：列表（含 []）为权威；缺失/null 表示历史或过渡任务。 */
function sameSourceKeys(item: ObligationReviewItemRecord): string[] | null {
  const value = item.legacy_same_source_keys;
  if (!Array.isArray(value)) {
    return null;
  }
  return value.filter((entry): entry is string => typeof entry === "string");
}

function coverageTerms(item: ObligationReviewItemRecord): string[] | null {
  const value = item.legacy_coverage_terms;
  if (!Array.isArray(value)) {
    return null;
  }
  return value.filter((entry): entry is string => typeof entry === "string");
}

/** 结构化证据：缺数/候选的表代码，或阻断项 ref 里精确的 FIN_04（数字边界）。 */
function structuredFin04(item: ObligationReviewItemRecord): boolean {
  for (const operand of item.missing_operands ?? []) {
    if (operand && typeof operand === "object" && (operand as { table_code?: unknown }).table_code === FIN_04_KEY) {
      return true;
    }
  }
  for (const candidate of item.candidates ?? []) {
    if (candidate && typeof candidate === "object" && (candidate as { table_code?: unknown }).table_code === FIN_04_KEY) {
      return true;
    }
  }
  for (const entry of item.blocked_by ?? []) {
    const ref = entry && typeof entry === "object" && typeof (entry as { ref?: unknown }).ref === "string"
      ? String((entry as { ref: string }).ref)
      : "";
    if (/(^|[^0-9])FIN[ _]?04([^0-9]|$)/.test(ref)) {
      return true;
    }
  }
  return false;
}

function stableTotalTitle(item: ObligationReviewItemRecord): boolean {
  return LEGACY_TOTAL_TITLES.has(String(item.title ?? "").trim());
}

/** 历史任务：第一阶段旧匹配器（只看 detail/title 的欠款表名）。 */
function historicalSameSource(
  item: ObligationReviewItemRecord,
  other: ObligationReviewItemRecord,
  itemIsEmptyStatement: boolean,
): boolean {
  if (itemIsEmptyStatement) {
    return mentionsEmptyStatement(item) && mentionsFiscalGrantTotal(other);
  }
  return mentionsFiscalGrantTotal(item) && mentionsEmptyStatement(other);
}

/** 过渡任务：只读结构化字段；空表说明侧必须有表代码/阻断证据。 */
function structuredSameSource(
  item: ObligationReviewItemRecord,
  other: ObligationReviewItemRecord,
  itemIsEmptyStatement: boolean,
): boolean {
  const selfEvidence = itemIsEmptyStatement
    ? structuredFin04(item)
    : structuredFin04(item) || stableTotalTitle(item);
  const otherEvidence = itemIsEmptyStatement
    ? structuredFin04(other) || stableTotalTitle(other)
    : structuredFin04(other);
  return selfEvidence && otherEvidence;
}

/**
 * 同源提示（方案 B 真值表）。
 *
 * - 双方都提供 legacy_same_source_keys 列表：列表为权威，只看交集；[] 不回退；
 * - 双方都没有列表且都没有 legacy_coverage_terms：历史任务，逐字保留旧匹配器；
 * - 其余（只有一侧有列表，或任一侧有覆盖短语列表）：过渡任务，只读
 *   missing_operands / candidates.table_code / blocked_by.ref 与财政总表
 *   侧的稳定表题，不读 detail 或“规则说明”。
 */
function buildSameSource(
  item: ObligationReviewItemRecord,
  allItems: ObligationReviewItemRecord[],
): ObligationSameSourceHint | null {
  const obligationIds: string[] = [];
  const findingIds: string[] = [];
  const notes: string[] = [];

  const pairs: Array<[string, string, (self: ObligationReviewItemRecord) => boolean]> = [
    ["OBL-STRUCT-EMPTY-STATEMENT", "OBL-CROSS-FISCAL-TOTAL", mentionsEmptyStatement],
    ["OBL-CROSS-FISCAL-TOTAL", "OBL-STRUCT-EMPTY-STATEMENT", mentionsFiscalGrantTotal],
  ];
  for (const [selfId, otherId, legacyMatcher] of pairs) {
    if (item.obligation_id !== selfId) {
      continue;
    }
    const other = allItems.find((candidate) => candidate.obligation_id === otherId);
    if (!other) {
      continue;
    }
    const selfKeys = sameSourceKeys(item);
    const otherKeys = sameSourceKeys(other);
    const historical =
      selfKeys === null &&
      otherKeys === null &&
      coverageTerms(item) === null &&
      coverageTerms(other) === null;
    let linked: boolean;
    if (selfKeys !== null && otherKeys !== null) {
      linked = selfKeys.includes(FIN_04_KEY) && otherKeys.includes(FIN_04_KEY);
    } else if (historical) {
      linked = legacyMatcher(item) && historicalSameSource(item, other, selfId === "OBL-STRUCT-EMPTY-STATEMENT");
    } else {
      linked = structuredSameSource(item, other, selfId === "OBL-STRUCT-EMPTY-STATEMENT");
    }
    if (!linked) {
      continue;
    }
    obligationIds.push(otherId);
    notes.push(
      selfId === "OBL-STRUCT-EMPTY-STATEMENT"
        ? "与「财政拨款收支总表合计一致性」指向同一处空白金额格，建议两条保持同一结论。"
        : "与「空表/无此项说明与总表零值确认」指向同一处空白金额格，建议两条保持同一结论。",
    );
  }

  if (item.obligation_id === "OBL-TXT-T5" && /找不到同名科目/.test(String(item.detail ?? ""))) {
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
    blocker: item.detail?.trim() || null,
    whatToConfirm: template.whatToConfirm,
    howToDecide: template.howToDecide,
    evidenceExample: template.evidenceExample,
    sameSource: buildSameSource(item, allItems),
  };
}
