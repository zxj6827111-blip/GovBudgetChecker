import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

/**
 * 硬编码颜色守卫（修复 C 的防回归手段）。
 *
 * 背景：登录页的蓝色曾以 rgba(59,130,246)（= Tailwind blue-500）硬编码在
 * className 任意值里，既躲过 blue-* 类名扫描、也不受 primary 令牌控制。
 * 本守卫对新 UI 目录做两层扫描：
 * 1. 硬编码颜色值：rgb()/rgba() 函数、# 十六进制（className 任意值里出现即违规，
 *    无论是否套在渐变/阴影的方括号里）；
 * 2. 禁用调色板类名：blue-* / indigo-* / sky-* / cyan-* / violet-* / purple-*。
 *
 * 守卫范围 = 本批新 UI 自有文件（登录页 + (workspace) 路由组 + 新组件目录），
 * 以及已收敛完成、纳入防回归的存量组件（GUARDED_FILES）。
 * 系统设置页的面板（SETTINGS_SURFACE_FILES）另加第三层：原始语义色、font-black、
 * 超规格圆角和近黑底色也不得回退，这些写法曾让设置页明显偏离其他页面。
 * 不在范围内：app/components/admin/ 下其余未收敛的文件、旧页面 viewer/*、
 * task-review/*（随 Task 10 删除自然消失）、tailwind.config.ts
 * （令牌定义本身必须用色值，不属于硬编码债务）。
 */

const APP_ROOT = path.resolve(__dirname, "..");

const GUARDED_DIRS = [
  "app/login",
  "app/(workspace)",
  "app/components/workspace",
  "app/components/ui",
  "app/components/review-workbench",
  "app/components/queue",
  "app/components/history",
  "app/components/quality",
  "app/components/rules",
  "app/components/settings",
  "app/components/materials",
];

/** 存量组件逐文件纳入守卫（收敛完成后即锁定，防止回退）。 */
const GUARDED_FILES = [
  "app/components/StructuredCleanupDialog.tsx",
  "app/components/BatchUploadModal.tsx",
  "app/components/ReanalyzeProgressDialog.tsx",
  "app/components/OrganizationTree.tsx",
  "app/components/admin/SystemManagementPanel.tsx",
  "app/components/admin/UserManagementPanel.tsx",
  "app/components/admin/AnalysisResultsPanel.tsx",
  "app/components/admin/AIModelSettingsPanel.tsx",
];

/** 系统设置页的面板：已统一到设计令牌，比基础守卫多锁语义色、字重、圆角和近黑底色。 */
const SETTINGS_SURFACE_FILES = [
  "app/components/BatchUploadModal.tsx",
  "app/components/ReanalyzeProgressDialog.tsx",
  "app/components/StructuredCleanupDialog.tsx",
  "app/components/OrganizationTree.tsx",
  "app/components/admin/SystemManagementPanel.tsx",
  "app/components/admin/UserManagementPanel.tsx",
  "app/components/admin/AnalysisResultsPanel.tsx",
  "app/components/admin/AIModelSettingsPanel.tsx",
  "app/components/settings/SettingsPage.tsx",
  "app/components/settings/AnnualTemplatePanel.tsx",
  "app/components/settings/DisclosureCalendarPanel.tsx",
  "app/components/settings/TemplateAnchorSlots.tsx",
];

const SETTINGS_SURFACE_BANNED: Array<[RegExp, string]> = [
  [/\b(?:red|rose|emerald|green|amber|yellow|gray)-\d{2,3}\b/, "原始调色板类名（应改用 danger/success/warning/slate）"],
  [/\bfont-black\b/, "font-black（其他页面不用这一档字重）"],
  [/\brounded-(?:lg|xl|2xl|3xl)\b/, "超规格圆角（卡片用 rounded-card，控件用 rounded-md）"],
  [/\bbg-slate-9\d{2}\b/, "近黑底色（主操作用 primary，代码块用 surface）"],
];

/** 去掉块注释与整行 // 注释后再扫描，避免"注释里描述历史问题"被误报。 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => line.replace(/^\s*\/\/.*$/, ""))
    .join("\n");
}

function collectSourceFiles(dir: string): string[] {
  const absolute = path.join(APP_ROOT, dir);
  const entries = readdirSync(absolute);
  const files: string[] = [];
  for (const entry of entries) {
    const entryPath = path.join(absolute, entry);
    if (statSync(entryPath).isDirectory()) {
      files.push(...collectSourceFiles(path.join(dir, entry)));
      continue;
    }
    if (/\.(tsx|ts)$/.test(entry)) {
      files.push(entryPath);
    }
  }
  return files;
}

const HARDCODED_VALUE_PATTERNS: Array<[RegExp, string]> = [
  [/rgba?\(\s*\d/i, "rgb()/rgba() 颜色函数"],
  [/#[0-9a-fA-F]{6}\b/, "6 位十六进制颜色"],
  [/#[0-9a-fA-F]{3}\b(?![0-9a-fA-F])/, "3 位十六进制颜色"],
];

const BANNED_PALETTE_PATTERN = /\b(?:blue|indigo|sky|cyan|violet|purple)-\d{2,3}\b/;

const violations: string[] = [];
let scannedFileCount = 0;

for (const guardedDir of GUARDED_DIRS) {
  for (const filePath of collectSourceFiles(guardedDir)) {
    scannedFileCount += 1;
    const relative = path.relative(APP_ROOT, filePath).replace(/\\/g, "/");
    const source = stripComments(readFileSync(filePath, "utf-8"));

    for (const [pattern, label] of HARDCODED_VALUE_PATTERNS) {
      const match = source.match(pattern);
      if (match) {
        violations.push(`${relative}: ${label}「${match[0]}」`);
      }
    }

    const paletteMatch = source.match(BANNED_PALETTE_PATTERN);
    if (paletteMatch) {
      violations.push(`${relative}: 禁用调色板类名「${paletteMatch[0]}」`);
    }
  }
}

for (const guardedFile of GUARDED_FILES) {
  const filePath = path.join(APP_ROOT, guardedFile);
  scannedFileCount += 1;
  const source = stripComments(readFileSync(filePath, "utf-8"));

  for (const [pattern, label] of HARDCODED_VALUE_PATTERNS) {
    const match = source.match(pattern);
    if (match) {
      violations.push(`${guardedFile}: ${label}「${match[0]}」`);
    }
  }

  const paletteMatch = source.match(BANNED_PALETTE_PATTERN);
  if (paletteMatch) {
    violations.push(`${guardedFile}: 禁用调色板类名「${paletteMatch[0]}」`);
  }
}

for (const surfaceFile of SETTINGS_SURFACE_FILES) {
  const source = stripComments(readFileSync(path.join(APP_ROOT, surfaceFile), "utf-8"));
  for (const [pattern, label] of SETTINGS_SURFACE_BANNED) {
    const match = source.match(pattern);
    if (match) {
      violations.push(`${surfaceFile}: ${label}「${match[0]}」`);
    }
  }
}

// 正例：守卫必须真的扫到了新 UI 文件（防止目录路径写错导致空扫描恒真）。
assert.ok(
  scannedFileCount >= 20,
  `守卫应扫描到足量新 UI 文件（实际 ${scannedFileCount} 个），目录配置可能写错了`,
);
assert.ok(
  collectSourceFiles("app/login").some((file) => file.endsWith("page.tsx")),
  "登录页必须在守卫范围内（修复 C 的直接目标）",
);

// 反例：登录页绝不允许再出现修复前的硬编码蓝（rgba(59,130,246) = blue-500）。
const loginSource = stripComments(
  readFileSync(path.join(APP_ROOT, "app/login/page.tsx"), "utf-8"),
);
assert.ok(!/rgba?\(\s*59\s*,\s*130\s*,\s*246/.test(loginSource), "登录页不得包含硬编码 blue-500 的 rgba 值");
assert.ok(!loginSource.includes("#3b82f6"), "登录页不得包含硬编码 blue-500 的 hex 值");
assert.ok(loginSource.includes("primary-"), "登录页必须使用 primary 语义令牌（纳入设计系统）");

if (violations.length > 0) {
  assert.fail(`新 UI 目录存在硬编码颜色/禁用调色板类名（共 ${violations.length} 处）：\n${violations.join("\n")}`);
}

console.log(`hardcodedColorGuard.test.ts passed（扫描 ${scannedFileCount} 个新 UI 源文件，0 违规）`);
