import { SectionTitle } from "@/components/ui";

/**
 * Task 2 阶段的占位页面内容：只放标题 + "本页将在 Task N 实现" 说明。
 * 对照任务书要求"内容先占位（放页面标题 + 本页将在 Task N 实现）"，
 * 不在本批实现具体业务逻辑，避免 Task 2 范围蔓延到后续 Task。
 */
export interface WorkspacePlaceholderPageProps {
  title: string;
  desc: string;
  implementingTask: number;
}

export function WorkspacePlaceholderPage({ title, desc, implementingTask }: WorkspacePlaceholderPageProps) {
  return (
    <div className="p-8">
      <SectionTitle title={title} desc={desc} />
      <div className="mt-6 rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">
        本页将在 Task {implementingTask} 实现。
      </div>
    </div>
  );
}

export default WorkspacePlaceholderPage;
