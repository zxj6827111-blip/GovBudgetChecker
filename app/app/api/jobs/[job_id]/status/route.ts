import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

async function parseUpstream(res: Response) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: "non_json_from_backend", status: res.status, body: text };
  }
}

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const job = encodeURIComponent((await params).job_id);
  const candidates = [
    `${apiBase}/api/jobs/${job}/status`,
    `${apiBase}/jobs/${job}/status`,
  ];
  let lastError: { status: number; data: unknown; source: string } | null = null;
  let lastException: string | null = null;

  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        cache: "no-store",
        headers: auth.headers,
      });
      const data = await parseUpstream(res);
      if (res.ok) {
        return NextResponse.json(data, { status: res.status });
      }
      lastError = { status: res.status, data, source: url };
    } catch {
      lastException = `fetch_failed:${url}`;
    }
  }

  if (lastError) {
    return NextResponse.json(
      {
        error: "backend_request_failed",
        job_id: (await params).job_id,
        upstream_status: lastError.status,
        upstream_source: lastError.source,
        upstream_body: lastError.data,
      },
      { status: lastError.status || 502 }
    );
  }

  return NextResponse.json(
    {
      error: "backend_unavailable",
      job_id: (await params).job_id,
      detail: lastException || "all upstream candidates failed",
    },
    { status: 502 }
  );
}
