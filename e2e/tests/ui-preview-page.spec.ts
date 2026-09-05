import { expect, test } from "../../app/node_modules/playwright/test";

/**
 * Task 1：组件预览页（/dev/ui-preview）的真实浏览器渲染验证。
 *
 * 为什么需要这条 e2e（而不是只靠 tests/uiComponents.test.ts 的纯逻辑单测）：
 * 纯逻辑单测只验证了 tone/variant -> className 的映射表本身是否正确，
 * 不能验证 .tsx 组件文件的 JSX 结构、React 渲染输出是否真的把这些 className
 * 拼到了正确的 DOM 节点上（原因：当前 Node/jiti 组合无法直接 require .tsx 文件，
 * 详见 app/components/ui/buttonStyles.ts 顶部注释）。这里在真实浏览器里渲染，
 * 是唯一能验证"浏览器最终看到的 DOM 是否正确"的层次。
 *
 * 生产环境下该路由完全不可达（返回 404）已经由 `npm run build` 产出的静态构建
 * 产物验证（.next/server/app/dev/ui-preview.meta 里 "status": 404，
 * .html 里内嵌 digest": "NEXT_HTTP_ERROR_FALLBACK;404"），比 e2e 更强的证据
 * ——e2e 跑在 next dev 上，本身就无法验证"生产构建"这件事，所以这里不重复验证
 * 生产 404，只验证开发环境下的正常渲染。
 */
const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

test.describe("UI component preview page (dev only)", () => {
  test("renders all Task 1 base components and the StageProgress null-progress counter-example", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);

    const response = await page.goto("/dev/ui-preview");
    expect(response).not.toBeNull();
    expect(response!.status()).toBe(200);

    // Button 四个变体均可见
    await expect(page.getByRole("button", { name: "主操作 primary" })).toBeVisible();
    await expect(page.getByRole("button", { name: "次要操作 secondary" })).toBeVisible();
    await expect(page.getByRole("button", { name: "辅助操作 ghost" })).toBeVisible();
    await expect(page.getByRole("button", { name: "危险操作 danger" })).toBeVisible();
    await expect(page.getByRole("button", { name: "禁用态 disabled" })).toBeDisabled();

    // Badge 全部质量状态可见（页面里表格示例行复用了"需要人工复核"文案，
    // 用 .first() 只取徽章区域那一个，避免 Playwright strict mode 因多处命中而报错）
    await expect(page.getByText("需要人工复核", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("正在分析", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("处理失败", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("分析完成", { exact: true }).first()).toBeVisible();

    // Metric：正常值原样显示，null 值显示 "—"
    await expect(page.getByText("18", { exact: true })).toBeVisible();

    // StageProgress 核心反例：未知进度那一行必须显示 "—"，进度条不能带 aria-valuenow，
    // 也不能渲染任何填充色子节点。用 data-testid 精确定位，避免脆弱的 class/xpath 匹配
    // ——注意不能直接对整页文本做 not.toContain("0%")，因为本页自己的说明文案里就写了
    // "不得显示 0%" 这几个字（那是给人看的提示语，不是被测的渲染结果）。
    const unknownRow = page.getByTestId("ui-preview-stage-unknown");
    await expect(unknownRow).toBeVisible();
    await expect(unknownRow.getByText("—", { exact: true })).toBeVisible();
    const unknownProgressBar = unknownRow.locator('[role="progressbar"]');
    await expect(unknownProgressBar).toHaveAttribute("aria-valuetext", "进度未知");
    await expect(unknownProgressBar).not.toHaveAttribute("aria-valuenow", /.+/);
    // 未知态的进度条内部不应有任何填充色子节点（已知态才会渲染 filled div）
    await expect(unknownProgressBar.locator("div")).toHaveCount(0);

    // 已知进度必须正常显示百分比数字，且对应进度条确实带正确的 aria-valuenow
    const knownRow = page.getByTestId("ui-preview-stage-known");
    await expect(knownRow.getByText("92%", { exact: true })).toBeVisible();
    await expect(knownRow.locator('[role="progressbar"]')).toHaveAttribute("aria-valuenow", "92");
    await expect(page.getByText("54%", { exact: true })).toBeVisible();
  });
});
