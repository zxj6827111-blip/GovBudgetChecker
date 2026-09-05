import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { QualityPage } from "@/components/quality/QualityPage";

export default function QualityRoutePage() {
  return (
    <AdminOnlyGuard>
      <QualityPage />
    </AdminOnlyGuard>
  );
}
