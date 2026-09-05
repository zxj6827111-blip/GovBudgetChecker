import { redirect } from "next/navigation";

/**
 * 修复 D（入口切换）：根路由从"re-export 旧单体"改为重定向到新版工作台
 * 总览 /workbench——登录后与直接敲 localhost:3000 都落到新 UI。
 *
 * 用重定向而不是把工作台代码搬到根路由：保持 (workspace) 路由组结构
 * 清晰（侧栏/顶栏布局都在路由组 layout 里），也便于 Task 10 收尾时统一
 * 处理其余旧入口。旧单体仍可通过 /viewer/gbc-ui-demo 访问（Task 10 下线）。
 */
export default function RootPage() {
  redirect("/workbench");
}
