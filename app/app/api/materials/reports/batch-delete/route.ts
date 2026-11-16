import { NextRequest } from "next/server";
import { proxyMaterialReportsMutation } from "@/lib/materialReportsProxy";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  return proxyMaterialReportsMutation(
    request,
    "/api/materials/reports/batch-delete",
    "POST",
  );
}
