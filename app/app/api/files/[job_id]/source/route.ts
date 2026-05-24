import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const jobId = encodeURIComponent((await params).job_id);
  try {
    const upstream = await fetch(`${apiBase}/api/files/${jobId}/source`, {
      cache: "no-store",
      headers: auth.headers,
    });
    if (!upstream.ok) {
      return NextResponse.json(
        { error: "source file not found" },
        { status: upstream.status }
      );
    }

    const blob = await upstream.blob();
    return new NextResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "application/pdf",
        "Content-Disposition":
          upstream.headers.get("content-disposition") ||
          `inline; filename="${(await params).job_id}.pdf"`,
      },
    });
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || String(e) },
      { status: 502 }
    );
  }
}
