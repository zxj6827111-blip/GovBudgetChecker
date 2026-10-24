import { NextRequest } from "next/server";
import { proxyMaterialReports } from "@/lib/materialReportsProxy";

export const dynamic = "force-dynamic";
export async function GET(request: NextRequest, { params }: { params: Promise<{ slotId: string }> }) {
  return proxyMaterialReports(request, `/api/materials/reports/${encodeURIComponent((await params).slotId)}/history`);
}
