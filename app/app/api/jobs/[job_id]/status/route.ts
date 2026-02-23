import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

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
  { params }: { params: { job_id: string } }
) {
  const job = encodeURIComponent(params.job_id);
  const candidates = [
    `${apiBase}/api/jobs/${job}/status`,
    `${apiBase}/jobs/${job}/status`,
  ];

  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        cache: "no-store",
        headers: backendAuthHeaders(),
      });
      const data = await parseUpstream(res);
      if (res.ok) {
        return NextResponse.json(data, { status: 200 });
      }
    } catch {
      // try next candidate
    }
  }

  return NextResponse.json(
    { status: "unknown", job_id: params.job_id },
    { status: 200 }
  );
}

