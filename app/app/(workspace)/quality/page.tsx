import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function QualityPage() {
  return (
    <AdminOnlyGuard>
      <WorkspacePlaceholderPage
        title="质量管理"
        desc="用真实处理指标与结构性发布门禁判断系统是否可信（仅呈现有真实数据的指标）。"
        implementingTask={7}
      />
    </AdminOnlyGuard>
  );
}
