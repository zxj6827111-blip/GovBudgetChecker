import assert from "node:assert/strict";

import { METRIC_TONE_VALUE_CLASSES } from "../app/components/ui/metricStyles";
import { cn } from "../lib/utils";
import config from "../tailwind.config";

// cn() 依赖 tailwind-merge，而 tailwind-merge 不读 tailwind.config.ts：
// 没有登记的自定义令牌会被猜成错误的分组。本文件既钉住已知回归，
// 也从配置读令牌逐个校验，这样以后新增令牌但忘了登记时会直接失败。

// --- 已知回归：Metric 的 KPI 数值类被文字颜色类吞掉 -------------------------

assert.equal(
  cn("mt-2 text-kpi-value", "text-slate-900"),
  "mt-2 text-kpi-value text-slate-900",
  "REGRESSION: text-kpi-value is a font size and must survive a text color class",
);
for (const [tone, toneClass] of Object.entries(METRIC_TONE_VALUE_CLASSES)) {
  assert.equal(
    cn("mt-2 text-kpi-value", toneClass),
    `mt-2 text-kpi-value ${toneClass}`,
    `Metric tone=${tone} must keep both the size token and its color`,
  );
}
assert.equal(
  cn("mt-2 text-kpi-value", "text-slate-400"),
  "mt-2 text-kpi-value text-slate-400",
  "the empty-value color of Metric must keep the size token too",
);

// --- 逐个配置令牌校验：新增令牌没登记时在这里失败 ----------------------------

const extend = config.theme?.extend ?? {};
const tokensOf = (values: object | undefined, group: string) => {
  const tokens = Object.keys(values ?? {});
  assert.ok(tokens.length > 0, `tailwind.config.ts should declare ${group} tokens`);
  return tokens;
};

for (const token of tokensOf(extend.fontSize, "fontSize")) {
  const size = `text-${token}`;
  assert.equal(cn(size, "text-slate-900"), `${size} text-slate-900`, `${size} must not be read as a text color`);
  assert.equal(cn("text-sm", size), size, `${size} must override another font size`);
  assert.equal(cn(size, "text-sm"), "text-sm", `another font size must override ${size}`);
}

for (const token of tokensOf(extend.borderRadius, "borderRadius")) {
  const radius = `rounded-${token}`;
  assert.equal(cn("rounded-md", radius), radius, `${radius} must override another radius`);
  assert.equal(cn(radius, "rounded-none"), "rounded-none", `another radius must override ${radius}`);
}

for (const token of tokensOf(extend.boxShadow, "boxShadow")) {
  const shadow = `shadow-${token}`;
  assert.equal(cn("shadow-md", shadow), shadow, `${shadow} must override another shadow`);
  assert.equal(cn(shadow, "shadow-none"), "shadow-none", `shadow-none must override ${shadow}`);
  assert.equal(
    cn(shadow, "shadow-slate-200"),
    `${shadow} shadow-slate-200`,
    `${shadow} is a shadow, not a shadow color, and must survive a shadow color class`,
  );
}

for (const token of tokensOf(extend.backgroundImage, "backgroundImage")) {
  const image = `bg-${token}`;
  assert.equal(cn(image, "bg-white"), `${image} bg-white`, `${image} must not be read as a background color`);
  assert.equal(cn(image, "bg-none"), "bg-none", `bg-none must override ${image}`);
}

// --- 反向钉住：颜色令牌仍按颜色合并，不能被上面的登记带偏 ---------------------

for (const [token, value] of Object.entries(extend.colors ?? {})) {
  const shade = typeof value === "string" ? "" : `-${Object.keys(value as object)[0]}`;
  const color = `${token}${shade}`;
  assert.equal(cn(`text-${color}`, "text-slate-900"), "text-slate-900", `text-${color} must stay a text color`);
  assert.equal(cn(`bg-${color}`, "bg-white"), "bg-white", `bg-${color} must stay a background color`);
  assert.equal(
    cn(`border-${color}`, "border-slate-200"),
    "border-slate-200",
    `border-${color} must stay a border color`,
  );
}
assert.equal(cn("border-2", "border-border"), "border-2 border-border", "border width and color must coexist");

// --- cn 本身的既有行为不变 ----------------------------------------------------

assert.equal(cn("px-2", "px-4"), "px-4", "standard Tailwind conflicts must still merge");
assert.equal(cn("p-5", false, undefined, null, "mt-1"), "p-5 mt-1", "falsy inputs must still be dropped");

console.log("cnTailwindMerge tests passed");
