import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function RulesPage() {
  return (
    <AdminOnlyGuard>
      <WorkspacePlaceholderPage
        title="规则与版本"
        desc="暴露当前规则集版本、引擎版本与生效范围。"
        implementingTask={8}
      />
    </AdminOnlyGuard>
  );
}
