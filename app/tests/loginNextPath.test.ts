import assert from "node:assert/strict";

import { DEFAULT_NEXT_PATH, normalizeNextPath } from "../lib/loginNextPath";

// 断言意图（修复 D：入口切换到新 UI）：
// 1. 默认落地 /workbench（新版工作台总览），不再是旧单体所在的 "/"；
// 2. ?next= 深链能力保留（登录后回到目标页，不被无脑改写）；
// 3. 开放重定向防护不得削弱：非 "/" 开头、协议相对 URL（//）、/login 自指
//    一律拒绝并回落默认值——其中 "//" 是旧实现真实存在的漏洞（旧代码
//    startsWith("/") 放行了 //evil.com，会被 assign 解析成外部站点）。

// --- 正例：默认落地是新版工作台 ----------------------------------------------

assert.equal(DEFAULT_NEXT_PATH, "/workbench", "默认落地必须是 /workbench（新版工作台总览）");

// --- 正例：合法深链原样保留 ----------------------------------------------------

assert.equal(normalizeNextPath("/queue"), "/queue", "站内深链必须原样返回，不得被改写成 /workbench");
assert.equal(normalizeNextPath("/review?job=job-1"), "/review?job=job-1", "带查询参数的深链同样保留");
assert.equal(normalizeNextPath("/archive"), "/archive");
assert.equal(normalizeNextPath("/viewer/gbc-ui-demo"), "/viewer/gbc-ui-demo", "旧入口深链也保留（Task 10 前仍可访问）");

// --- 反例：开放重定向防护 -------------------------------------------------------

assert.equal(
  normalizeNextPath(null),
  DEFAULT_NEXT_PATH,
  "缺失 next 时回落默认（登录后进新工作台）",
);
assert.equal(
  normalizeNextPath(""),
  DEFAULT_NEXT_PATH,
);
assert.equal(
  normalizeNextPath("https://evil.com"),
  DEFAULT_NEXT_PATH,
  "REGRESSION: 绝对 URL 必须被拒（开放重定向防护）",
);
assert.equal(
  normalizeNextPath("http://evil.com/path"),
  DEFAULT_NEXT_PATH,
);
assert.equal(
  normalizeNextPath("//evil.com"),
  DEFAULT_NEXT_PATH,
  "REGRESSION: 协议相对 URL（//evil.com）必须被拒——旧实现只判 startsWith(\"/\")，这条会漏",
);
assert.equal(
  normalizeNextPath("//evil.com/x"),
  DEFAULT_NEXT_PATH,
);
assert.equal(
  normalizeNextPath("javascript:alert(1)"),
  DEFAULT_NEXT_PATH,
  "非 / 开头的任意 scheme 一律拒绝",
);
assert.equal(
  normalizeNextPath("relative/path"),
  DEFAULT_NEXT_PATH,
  "相对路径拒绝（assign 会把它拼到当前目录，行为不可预期）",
);

// --- 反例：/login 自指拒绝（防登录成功又弹回登录页的死循环） ----------------------

assert.equal(normalizeNextPath("/login"), DEFAULT_NEXT_PATH);
assert.equal(normalizeNextPath("/login?next=/queue"), DEFAULT_NEXT_PATH, "/login 前缀的任何变体都拒绝");

// --- 反例与正例的对照（防止恒真实现）----------------------------------------------

assert.notEqual(
  normalizeNextPath("//evil.com"),
  "//evil.com",
  "被拒绝的值绝不能原样返回",
);
assert.notEqual(
  normalizeNextPath("/queue"),
  DEFAULT_NEXT_PATH,
  "合法深链绝不能被吞成默认值（否则深链能力形同虚设）",
);

console.log("loginNextPath.test.ts passed");
