import { SupervisorMaterialPage } from "@/components/supervision/SupervisorMaterialPage";
export default async function Page({ params }: { params: Promise<{ slotId: string }> }) {
  return <SupervisorMaterialPage slotId={(await params).slotId} />;
}
