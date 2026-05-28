import { NextRequest, NextResponse } from "next/server";

import { requireAdminAccess } from "@/lib/adminAccess";
import {
  createAdminConfigItem,
  isAdminConfigCollection,
  listAdminConfigItems,
} from "@/lib/adminConfigStore";

export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ collection: string }>;
};

function invalidCollection() {
  return NextResponse.json({ detail: "unknown admin config collection" }, { status: 404 });
}

export async function GET(_request: NextRequest, { params }: RouteContext) {
  const collection = (await params).collection;
  if (!isAdminConfigCollection(collection)) {
    return invalidCollection();
  }

  const auth = await requireAdminAccess({ adminOnly: true });
  if (!auth.ok) {
    return auth.response;
  }

  const items = await listAdminConfigItems(collection);
  return NextResponse.json({ items }, { status: 200 });
}

export async function POST(request: NextRequest, { params }: RouteContext) {
  const collection = (await params).collection;
  if (!isAdminConfigCollection(collection)) {
    return invalidCollection();
  }

  const auth = await requireAdminAccess({ adminOnly: true });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const item = await createAdminConfigItem(
      collection,
      await request.json().catch(() => ({})),
      auth.actor,
    );
    return NextResponse.json(item, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { detail: error instanceof Error ? error.message : "failed to create admin config item" },
      { status: 400 },
    );
  }
}
