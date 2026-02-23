import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(
  _req: Request,
  { params }: { params: { job_id: string } }
) {
  const jobId = encodeURIComponent(params.job_id);
  try {
    const upstream = await fetch(`${apiBase}/api/files/${jobId}/source`, {
      cache: "no-store",
      headers: backendAuthHeaders(),
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
          `inline; filename="${params.job_id}.pdf"`,
      },
    });
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || String(e) },
      { status: 502 }
    );
  }
}

