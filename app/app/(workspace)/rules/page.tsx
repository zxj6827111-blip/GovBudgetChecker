import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { RulesPage } from "@/components/rules/RulesPage";

export default function RulesRoutePage() {
  return (
    <AdminOnlyGuard>
      <RulesPage />
    </AdminOnlyGuard>
  );
}
