import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET() {
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/organizations`, {
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { tree: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch organizations:", error);
    return NextResponse.json(
      { error: "backend_unavailable", tree: [], total: 0 },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const response = await fetchWithTimeout(`${apiBase}/api/organizations`, {
      method: "POST",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to create organization:", error);
    return NextResponse.json(
      { error: "Failed to create organization" },
      { status: 500 }
    );
  }
}
