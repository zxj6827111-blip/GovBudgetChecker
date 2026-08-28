import { AdminOnlyGuard } from "@/components/workspace/AdminOnlyGuard";
import { SettingsPage } from "@/components/settings/SettingsPage";

export default function SettingsRoutePage() {
  return (
    <AdminOnlyGuard>
      <SettingsPage />
    </AdminOnlyGuard>
  );
}
