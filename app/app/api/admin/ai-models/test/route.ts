import { NextRequest } from "next/server";
import { proxyAiModelRequest } from "@/lib/aiModelProxy";
export const runtime = "nodejs";
export const maxDuration = 310;
export const POST = (req: NextRequest) => proxyAiModelRequest(req, "/api/admin/ai-models/test");
