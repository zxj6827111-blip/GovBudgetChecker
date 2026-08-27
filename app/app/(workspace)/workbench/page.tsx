import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function WorkbenchPage() {
  return (
    <WorkspacePlaceholderPage
      title="工作台总览"
      desc="集中查看处理队列、需人工复核任务和系统质量告警。"
      implementingTask={4}
    />
  );
}
