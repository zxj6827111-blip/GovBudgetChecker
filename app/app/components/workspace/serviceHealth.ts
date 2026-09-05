/**
 * 顶栏服务状态判定：对照任务书"服务状态必须接 /api/health 真实结果，
 * 不得写死'服务正常'；异常时如实显示"。
 *
 * `/api/health` 是 app/app/api/health/route.ts 对后端 `/health` 的直通代理：
 * - 后端可达且返回 `{status:"ok",...}` -> healthy；
 * - 后端不可达时该代理路由会返回 502 + `{status:"down", error}` -> unhealthy；
 * - 网络异常（fetch 抛错、超时）-> unknown（既不能宣称"正常"也不能宣称"异常"，
 *   因为这种情况下我们甚至不知道后端返回了什么，只知道前端这次请求没成功）。
 *
 * 三态而非二态的原因：把"请求失败"直接归为"异常"会制造过度自信的错误诊断——
 * 用户看到"服务异常"会以为后端确实挂了，但真实原因可能只是网络抖动。
 * 三态给出更诚实的信息："健康" / "异常"（有明确响应）/ "无法确认"（没拿到响应）。
 */
export type ServiceHealthState = "healthy" | "unhealthy" | "unknown";

export interface ServiceHealthResult {
  state: ServiceHealthState;
  label: string;
}

export function resolveServiceHealthState(
  response: { ok: boolean; status: number } | null,
  payload: { status?: unknown } | null,
): ServiceHealthResult {
  if (response === null) {
    return { state: "unknown", label: "服务状态未知" };
  }
  if (response.ok && payload?.status === "ok") {
    return { state: "healthy", label: "服务正常" };
  }
  return { state: "unhealthy", label: "服务异常" };
}
