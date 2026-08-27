import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function QueuePage() {
  return (
    <WorkspacePlaceholderPage
      title="处理队列"
      desc="工作台队列面板的全量版，支持分页与多维筛选。"
      implementingTask={8}
    />
  );
}
