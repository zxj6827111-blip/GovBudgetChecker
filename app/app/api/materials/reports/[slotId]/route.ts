import { NextRequest } from "next/server";
import { proxyMaterialReportsMutation } from "@/lib/materialReportsProxy";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string }> },
) {
  const { slotId } = await params;
  return proxyMaterialReportsMutation(
    request,
    `/api/materials/reports/${encodeURIComponent(slotId)}`,
    "DELETE",
  );
}
