import { NextRequest } from "next/server";
import { proxyAiModelRequest } from "@/lib/aiModelProxy";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const GET = (req: NextRequest) => proxyAiModelRequest(req, "/api/ai/capabilities");
