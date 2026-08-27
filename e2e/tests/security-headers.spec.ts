import { test, expect } from "../../app/node_modules/playwright/test";

/**
 * Task 12 / 缺口 B-08：前端安全响应头。
 *
 * 前端的 CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy 由
 * `app/middleware.ts` 注入，HSTS 只在生产构建下发。
 *
 * 这里同时验证两件事，缺一不可：
 * 1. 响应头确实存在且值正确；
 * 2. 页面在该 CSP 下能正常渲染，且**没有产生任何 CSP 违规**
 *    ——只测"头存在"是不够的，CSP 打断渲染时头一样存在。
 */
test.describe("Security response headers", () => {
  test("login page carries security headers and renders without CSP violations", async ({
    page,
  }) => {
    const cspViolations: string[] = [];
    // CSP 违规会以 console error 形式出现（"Refused to ..."）
    page.on("console", (message) => {
      const text = message.text();
      if (
        message.type() === "error" &&
        (text.includes("Content Security Policy") || text.includes("Refused to"))
      ) {
        cspViolations.push(text);
      }
    });

    const response = await page.goto("/login");
    expect(response).not.toBeNull();

    const headers = response!.headers();
    const csp = headers["content-security-policy"] ?? "";
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'self'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-frame-options"]).toBe("SAMEORIGIN");
    expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
    expect(headers["permissions-policy"]).toContain("camera=()");

    // 渲染没有被 CSP 打断：登录表单可见、无 CSP 违规
    await expect(page.locator("form").first()).toBeVisible();
    await expect(page.locator('input[type="password"]')).toHaveCount(1);
    expect(cspViolations).toEqual([]);
  });

  test("demo workbench renders under CSP without violations", async ({ page }) => {
    const cspViolations: string[] = [];
    page.on("console", (message) => {
      const text = message.text();
      if (
        message.type() === "error" &&
        (text.includes("Content Security Policy") || text.includes("Refused to"))
      ) {
        cspViolations.push(text);
      }
    });

    const response = await page.goto("/e2e/batch-upload");
    expect(response).not.toBeNull();
    expect(response!.headers()["content-security-policy"]).toContain("default-src 'self'");

    // 有交互控件说明 hydration 完成（CSP 挡掉脚本时这里会失败）
    await expect(page.locator('input[type="file"]')).toHaveCount(2);
    await expect(page.locator("button").first()).toBeEnabled();
    expect(cspViolations).toEqual([]);
  });
});
