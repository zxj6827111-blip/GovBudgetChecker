import { notFound } from "next/navigation";

import { Badge, Button, Card, Metric, SectionTitle, StageProgress, Th, Td } from "@/components/ui";

import { shouldBlockUiPreviewPage } from "./guard";

/**
 * 组件预览页：Task 1 要求的"仅开发环境可见"页面，用于与原型图逐项对照校验。
 *
 * 生产环境门控沿用本仓库既有约定（见 app/app/e2e/batch-upload/page.tsx 的同一模式）：
 * `NODE_ENV === "production"` 且未显式设置 `GBC_ENABLE_E2E_PAGES=true` 时调用
 * `notFound()`，使该页在生产构建下返回 404、完全不可达。这与 middleware.ts 对
 * `/e2e/*` 路径的生产环境门控是同一套开关，不另立第二套环境变量。
 *
 * 判定逻辑抽在 guard.ts 的 shouldBlockUiPreviewPage()，供单测直接验证该判定函数
 * （原因：当前测试环境无法直接 require 含 JSX 的 .tsx 页面文件，见该文件顶部注释）。
 *
 * 本页不接任何真实业务数据，仅用静态示例值展示各组件变体，避免被误认成真实指标。
 */
export default function UiPreviewPage() {
  if (shouldBlockUiPreviewPage(process.env)) {
    notFound();
  }

  return (
    <div className="min-h-screen bg-surface-50 p-8">
      <SectionTitle
        title="组件预览（仅开发环境）"
        desc="与原型图逐项对照校验 Task 1 基础组件层，不接真实业务数据。"
      />

      <div className="mt-6 space-y-8">
        <Card title="Button 变体">
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary">主操作 primary</Button>
            <Button variant="secondary">次要操作 secondary</Button>
            <Button variant="ghost">辅助操作 ghost</Button>
            <Button variant="danger">危险操作 danger</Button>
            <Button variant="primary" disabled>
              禁用态 disabled
            </Button>
          </div>
        </Card>

        <Card title="Badge 质量状态徽章">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone="review">需要人工复核</Badge>
            <Badge tone="processing">正在分析</Badge>
            <Badge tone="lowconf">低置信度</Badge>
            <Badge tone="failed">处理失败</Badge>
            <Badge tone="done">分析完成</Badge>
            <Badge tone="neutral">未知状态</Badge>
          </div>
        </Card>

        <Card title="Metric KPI 卡">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Metric label="待人工复核" value={18} desc="其中 6 项为高风险问题" corner="今日" tone="warning" />
            <Metric label="正在处理" value={7} desc="队列平均等待 1 分 32 秒" corner="实时" tone="primary" />
            <Metric label="页面覆盖率" value={null} desc="数据不足时必须显示 —，不得显示 0%" tone="neutral" />
          </div>
        </Card>

        <Card title="StageProgress 阶段进度（含未知态反例）">
          <div className="space-y-4">
            <StageProgress
              data-testid="ui-preview-stage-known"
              stageLabel="质量门禁 · 92%"
              progress={92}
              tone="primary"
            />
            <StageProgress stageLabel="元数据识别 · 54%" progress={54} tone="warning" />
            <StageProgress stageLabel="OCR 处理失败 · 第 8 页" progress={18} tone="danger" />
            <StageProgress
              data-testid="ui-preview-stage-unknown"
              stageLabel="进度未知（反例：不得显示 0%）"
              progress={null}
            />
          </div>
        </Card>

        <Card title="Table 表格单元格">
          <table className="w-full">
            <thead>
              <tr>
                <Th>文档</Th>
                <Th>当前阶段</Th>
                <Th>质量状态</Th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <Td>2026年度部门预算公开说明.pdf</Td>
                <Td>质量门禁 · 92%</Td>
                <Td>
                  <Badge tone="review">需要人工复核</Badge>
                </Td>
              </tr>
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
