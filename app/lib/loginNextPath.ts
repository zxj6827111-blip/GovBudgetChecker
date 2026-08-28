/**
 * 登录后跳转目标的解析与安全校验（修复 D：入口切换到新 UI）。
 *
 * 从登录页组件里抽出为独立 lib 的原因：这里的判定承载开放重定向防护
 * （安全红线），必须能被 jiti 单测直接断言，不能只靠 e2e。
 *
 * 规则：
 * 1. 默认落地 = /workbench（新版工作台总览）。旧默认 "/" 在入口切换后
 *    会再跳一次 /workbench，不如直接落到目标（少一跳，语义也更清晰）；
 * 2. 深链保留：?next=/queue 这类站内路径登录后原样返回，不被无脑改写成
 *    /workbench——中间件未登录时携带的 next 参数就是这个用途；
 * 3. 安全校验（不得削弱）：
 *    - 非以 "/" 开头的一律拒绝（https://evil.com、相对协议等）；
 *    - 以 "//" 开头的一律拒绝（协议相对 URL，如 //evil.com——这是旧实现
 *      漏掉的一条，会被 window.location.assign 解析成外部站点）；
 *    - 指向 /login 自身的拒绝（否则登录成功又弹回登录页，形成死循环）。
 *    被拒绝的值一律回落到默认 /workbench，不会把用户带到站外。
 */

export const DEFAULT_NEXT_PATH = "/workbench";

export function normalizeNextPath(rawPath: string | null): string {
  if (!rawPath || !rawPath.startsWith("/") || rawPath.startsWith("//")) {
    return DEFAULT_NEXT_PATH;
  }
  if (rawPath.startsWith("/login")) {
    return DEFAULT_NEXT_PATH;
  }
  return rawPath;
}
