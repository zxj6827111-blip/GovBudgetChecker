import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";

export async function GET(
  req: Request,
  { params }: { params: { job_id: string } }
) {
  const jobId = encodeURIComponent(params.job_id);
  const url = new URL(req.url);
  const search = url.searchParams.toString();
  const upstreamUrl = `${apiBase}/api/files/${jobId}/preview${search ? `?${search}` : ""}`;
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    process.env.BACKEND_API_KEY ||
    "change_me_to_a_strong_secret";

  try {
    const upstream = await fetch(upstreamUrl, {
      cache: "no-store",
      headers: {
        "X-API-Key": apiKey,
      },
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      return new NextResponse(text || "preview not available", {
        status: upstream.status,
        headers: {
          "Content-Type": upstream.headers.get("content-type") || "text/plain; charset=utf-8",
        },
      });
    }

    const blob = await upstream.blob();
    return new NextResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "image/png",
        "Cache-Control": upstream.headers.get("cache-control") || "no-store",
      },
    });
  } catch (error: any) {
    return NextResponse.json(
      { error: error?.message || String(error) },
      { status: 502 }
    );
  }
}
