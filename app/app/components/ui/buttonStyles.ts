/**
 * Button 的纯逻辑层：variant/size -> className 映射，不含任何 JSX。
 *
 * 拆分原因（环境限制，非设计偏好）：本项目前端单测用 jiti 直跑 .ts 文件
 * （见 package.json 的 test:* 脚本），而当前 Node 版本的内置 TypeScript
 * 处理会在 jiti 的 require 钩子之前拦截 .tsx 扩展名文件并尝试用不支持 JSX
 * 语法的原生 ESM 加载器解析，导致 .tsx 组件文件无法被这套测试脚本直接
 * import（复现记录见本次 Task 1 提交说明）。将无 JSX 依赖的纯逻辑抽到 .ts
 * 文件后即可正常单测，.tsx 组件文件本身仍通过 `npm run build` 的完整
 * Next.js/TypeScript 编译链路验证，并有对应 e2e 做真实浏览器渲染验证。
 */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export const BUTTON_VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-primary-600 text-white shadow-sm hover:bg-primary-700 disabled:bg-slate-300 disabled:text-slate-500",
  secondary:
    "border border-slate-200 bg-white text-slate-700 shadow-sm hover:bg-slate-50 disabled:border-slate-100 disabled:text-slate-400",
  ghost: "bg-transparent text-slate-600 hover:bg-slate-100 disabled:text-slate-300",
  danger:
    "bg-danger-600 text-white shadow-sm hover:bg-danger-700 disabled:bg-slate-300 disabled:text-slate-500",
};

export const BUTTON_SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
};

export const BUTTON_BASE_CLASSES =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2 " +
  "disabled:cursor-not-allowed";
