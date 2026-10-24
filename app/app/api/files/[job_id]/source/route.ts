import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const jobId = encodeURIComponent((await params).job_id);
  try {
    const upstreamUrl = new URL(`${apiBase}/api/files/${jobId}/source`);
    const checksum = new URL(req.url).searchParams.get("checksum");
    if (checksum) upstreamUrl.searchParams.set("checksum", checksum);
    const upstream = await fetch(upstreamUrl.toString(), {
      cache: "no-store",
      headers: auth.headers,
    });
    if (!upstream.ok) {
      const error = await upstream.json().catch(() => ({ detail: "原件暂时不可用" }));
      return NextResponse.json(
        error,
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
