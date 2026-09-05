/**
 * 组件预览页生产环境门控的纯判定逻辑。
 *
 * 拆分原因见 buttonStyles.ts 顶部注释：页面本体是 .tsx（app/dev/ui-preview/page.tsx），
 * 当前测试环境（jiti + Node 24）无法直接 require 含 JSX 的 .tsx 文件，
 * 因此把"是否应该 404"这条判定抽成纯函数单独测试；页面文件本身在调用处直接
 * `if (shouldBlockUiPreviewPage()) notFound();`，保证测试的判定逻辑与页面
 * 实际执行的判定逻辑是同一个函数，不是两份可能失去同步的复制品。
 *
 * 真实 HTTP 请求层面"生产构建下访问该路径确实返回 404"由 e2e 补一条用例验证
 * （不同的验证层次：这里验证判定函数本身，e2e 验证请求到达时的最终效果）。
 */
export function shouldBlockUiPreviewPage(env: {
  NODE_ENV?: string;
  GBC_ENABLE_E2E_PAGES?: string;
}): boolean {
  return env.NODE_ENV === "production" && env.GBC_ENABLE_E2E_PAGES !== "true";
}
