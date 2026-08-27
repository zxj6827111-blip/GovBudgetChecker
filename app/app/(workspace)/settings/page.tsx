import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function SettingsPage() {
  return (
    <AdminOnlyGuard>
      <WorkspacePlaceholderPage
        title="系统设置"
        desc="组织架构、规则包与用户管理等系统级配置。"
        implementingTask={8}
      />
    </AdminOnlyGuard>
  );
}
