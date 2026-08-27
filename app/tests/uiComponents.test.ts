import assert from "node:assert/strict";

import { BADGE_BASE_CLASSES, BADGE_TONE_CLASSES } from "../app/components/ui/badgeStyles";
import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from "../app/components/ui/buttonStyles";
import { isMetricValueEmpty, METRIC_TONE_VALUE_CLASSES } from "../app/components/ui/metricStyles";
import {
  clampProgress,
  formatProgressText,
  isProgressUnknown,
  STAGE_PROGRESS_TONE_FILL_CLASSES,
} from "../app/components/ui/stageProgressStyles";

// 本文件测试 Task 1 基础组件层的纯逻辑（tone/variant -> className 映射、
// 空值与未知态判定），这部分逻辑被有意拆到无 JSX 依赖的 .ts 文件中
// （见 app/components/ui/buttonStyles.ts 顶部注释），使其可以用本项目既有的
// jiti 直跑测试脚本验证，不必新增 React Testing Library / jsdom 等重型依赖。
//
// 组件本体（.tsx 文件）的 JSX 结构、可访问属性拼装是否正确，由以下两层验证：
// 1. `npm run build` 走完整 Next.js/TypeScript 编译链路，任何 JSX 语法错误、
//    prop 类型不匹配都会在此处失败；
// 2. e2e 补的 ui-preview-guard 用例在真实浏览器中渲染组件预览页，
//    这是唯一真正验证"浏览器里渲染出来的 DOM 是否正确"的层次。

// --- Button：四个变体 + 全部尺寸都必须有对应的类名，且不能互相混淆 ------------

const buttonVariants = ["primary", "secondary", "ghost", "danger"] as const;
for (const variant of buttonVariants) {
  const classes = BUTTON_VARIANT_CLASSES[variant];
  assert.ok(classes && classes.length > 0, `variant=${variant} must map to a non-empty className`);
}
assert.match(BUTTON_VARIANT_CLASSES.primary, /bg-primary-600/, "primary must use primary-600 background");
assert.match(BUTTON_VARIANT_CLASSES.danger, /bg-danger-600/, "danger must use danger-600 background");
assert.doesNotMatch(
  BUTTON_VARIANT_CLASSES.ghost,
  /bg-primary-600|bg-danger-600/,
  "ghost must not carry any solid semantic background color",
);
assert.notEqual(
  BUTTON_VARIANT_CLASSES.primary,
  BUTTON_VARIANT_CLASSES.secondary,
  "primary and secondary must not resolve to the identical className (would defeat the point of having two variants)",
);

for (const size of ["sm", "md"] as const) {
  assert.ok(BUTTON_SIZE_CLASSES[size]?.length > 0, `size=${size} must map to a non-empty className`);
}

// 无障碍：全部变体共享同一条焦点可见环样式，键盘 Tab 到按钮时不能"看不见焦点在哪"
assert.match(
  BUTTON_BASE_CLASSES,
  /focus-visible:ring-2/,
  "the shared base className must render a visible focus ring for keyboard users",
);

// --- Badge：原型图出现的全部质量状态 tone，且不同 tone 不能共享同一份底色+文字色 ---

const badgeExpectations: Array<[string, RegExp]> = [
  ["review", /bg-warning-100/],
  ["processing", /bg-primary-100/],
  ["lowconf", /bg-warning-100/],
  ["failed", /bg-danger-100/],
  ["done", /bg-success-100/],
  ["neutral", /bg-slate-100/],
];
for (const [tone, expectedPattern] of badgeExpectations) {
  const classes = BADGE_TONE_CLASSES[tone as keyof typeof BADGE_TONE_CLASSES];
  assert.match(classes, expectedPattern, `tone=${tone} must match ${expectedPattern}`);
}
// review 与 lowconf 在原型图中确实同色（语义靠文案区分），但二者都不能与 failed/done 撞色
assert.notEqual(BADGE_TONE_CLASSES.review, BADGE_TONE_CLASSES.failed);
assert.notEqual(BADGE_TONE_CLASSES.review, BADGE_TONE_CLASSES.done);
assert.ok(BADGE_BASE_CLASSES.includes("rounded-full"), "badge shell must be pill-shaped per prototype");

// --- Metric：空值判定的核心反例——null/undefined/"" 是空，0 和正常字符串不是空 -------

assert.equal(isMetricValueEmpty(null), true, "REGRESSION: null must be treated as empty");
assert.equal(isMetricValueEmpty(undefined), true, "REGRESSION: undefined must be treated as empty");
assert.equal(isMetricValueEmpty(""), true, "empty string must be treated as empty");
assert.equal(isMetricValueEmpty(0), false, "REGRESSION: the real value 0 must NOT be treated as empty");
assert.equal(isMetricValueEmpty(18), false, "a normal positive number must not be treated as empty");
assert.equal(isMetricValueEmpty("98.7%"), false, "a normal string value must not be treated as empty");

for (const tone of ["primary", "success", "warning", "danger", "info", "neutral"] as const) {
  assert.ok(METRIC_TONE_VALUE_CLASSES[tone]?.length > 0, `tone=${tone} must map to a non-empty className`);
}

// --- StageProgress：核心反例——未知进度必须格式化为 "—"，绝不能是 "0%" 或其它猜测值 ---

assert.equal(isProgressUnknown(null), true, "REGRESSION: null progress must be treated as unknown");
assert.equal(isProgressUnknown(undefined), true, "REGRESSION: undefined progress must be treated as unknown");
assert.equal(isProgressUnknown(0), false, "REGRESSION: the real value 0 must NOT be treated as unknown");
assert.equal(isProgressUnknown(50), false);
assert.equal(isProgressUnknown(100), false);

assert.equal(formatProgressText(null), "—", "REGRESSION: null progress must format to em dash");
assert.equal(formatProgressText(undefined), "—", "REGRESSION: undefined progress must format to em dash");
assert.notEqual(formatProgressText(null), "0%", "REGRESSION: null must never format to the string 0%");
assert.equal(formatProgressText(0), "0%", "a genuine 0 progress must format to the literal 0%");
assert.equal(formatProgressText(92), "92%");
assert.equal(formatProgressText(92.6), "93%", "progress text must round to the nearest integer percent");

// 边界值：越界输入必须被夹紧到 [0,100]，防止进度条视觉溢出容器
assert.equal(clampProgress(150), 100, "REGRESSION: values above 100 must be clamped to 100");
assert.equal(clampProgress(-20), 0, "REGRESSION: negative values must be clamped to 0");
assert.equal(clampProgress(0), 0);
assert.equal(clampProgress(100), 100);
assert.equal(clampProgress(NaN), 0, "NaN must degrade to 0 rather than propagate into a broken width style");
assert.equal(formatProgressText(150), "100%", "formatted text must reflect the clamped value, not the raw 150");

for (const tone of ["primary", "success", "warning", "danger", "info"] as const) {
  assert.ok(
    STAGE_PROGRESS_TONE_FILL_CLASSES[tone]?.length > 0,
    `tone=${tone} must map to a non-empty fill className`,
  );
}

console.log("uiComponents.test.ts passed");
