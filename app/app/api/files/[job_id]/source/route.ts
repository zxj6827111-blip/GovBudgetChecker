import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";

export async function GET(
  _req: Request,
  { params }: { params: { job_id: string } }
) {
  const jobId = encodeURIComponent(params.job_id);
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    process.env.BACKEND_API_KEY ||
    "change_me_to_a_strong_secret";
  try {
    const upstream = await fetch(`${apiBase}/api/files/${jobId}/source`, {
      cache: "no-store",
      headers: {
        "X-API-Key": apiKey,
      },
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
