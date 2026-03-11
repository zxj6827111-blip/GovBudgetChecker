import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function PUT(
  request: NextRequest,
  { params }: { params: { org_id: string } }
) {
  try {
    const body = await request.json();
    const orgId = encodeURIComponent(params.org_id);
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/${orgId}`, {
      method: "PUT",
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
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
    console.error("Failed to update organization:", error);
    return NextResponse.json(
      { error: "Failed to update organization" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { org_id: string } }
) {
  try {
    const orgId = encodeURIComponent(params.org_id);
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/${orgId}`, {
      method: "DELETE",
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
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
    console.error("Failed to delete organization:", error);
    return NextResponse.json(
      { error: "Failed to delete organization" },
      { status: 500 }
    );
  }
}
