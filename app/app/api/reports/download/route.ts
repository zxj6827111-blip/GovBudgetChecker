import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(req: NextRequest) {
  const jobId = req.nextUrl.searchParams.get("job_id");
  const format = req.nextUrl.searchParams.get("format");
  if (!jobId) {
    return NextResponse.json({ error: "job_id is required" }, { status: 400 });
  }

  try {
    const params = new URLSearchParams({
      job_id: jobId,
    });
    if (format) {
      params.set("format", format);
    }
    const upstream = await fetch(
      `${apiBase}/api/reports/download?${params.toString()}`,
      {
        cache: "no-store",
        headers: backendAuthHeaders(),
      }
    );

    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        { error: "report not available", detail: text || upstream.statusText },
        { status: upstream.status }
      );
    }

    const blob = await upstream.blob();
    const disposition =
      upstream.headers.get("content-disposition") ||
      `attachment; filename="${jobId}.${format || "pdf"}"`;
    return new NextResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "application/pdf",
        "Content-Disposition": disposition,
      },
    });
  } catch (e: any) {
    return NextResponse.json(
      { error: "download failed", detail: e?.message || String(e) },
      { status: 502 }
    );
  }
}
