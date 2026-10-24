import { NextRequest } from "next/server";
import { proxyMaterialReports } from "@/lib/materialReportsProxy";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) { return proxyMaterialReports(request, "/api/materials/reports"); }
