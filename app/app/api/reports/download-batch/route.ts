import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function POST(request: NextRequest) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const body = await request.json().catch(() => ({}));
    const upstream = await fetch(`${apiBase}/api/reports/download-batch`, {
      method: "POST",
      cache: "no-store",
      headers: auth.headers,
      body: JSON.stringify(body ?? {}),
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        { error: "batch download failed", detail: text || upstream.statusText },
        { status: upstream.status },
      );
    }

    const blob = await upstream.blob();
    const disposition =
      upstream.headers.get("content-disposition") ||
      'attachment; filename="reports-batch.zip"';

    return new NextResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "application/zip",
        "Content-Disposition": disposition,
      },
    });
  } catch (error: any) {
    return NextResponse.json(
      { error: "batch download failed", detail: error?.message || String(error) },
      { status: 502 },
    );
  }
}
