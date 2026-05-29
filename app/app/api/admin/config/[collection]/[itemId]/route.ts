import { NextRequest, NextResponse } from "next/server";

import { requireAdminAccess } from "@/lib/adminAccess";
import {
  deleteAdminConfigItem,
  getAdminConfigItem,
  isAdminConfigCollection,
  updateAdminConfigItem,
} from "@/lib/adminConfigStore";

export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ collection: string; itemId: string }>;
};

function invalidCollection() {
  return NextResponse.json({ detail: "unknown admin config collection" }, { status: 404 });
}

function notFound() {
  return NextResponse.json({ detail: "admin config item not found" }, { status: 404 });
}

export async function GET(_request: NextRequest, { params }: RouteContext) {
  const { collection, itemId } = await params;
  if (!isAdminConfigCollection(collection)) {
    return invalidCollection();
  }

  const auth = await requireAdminAccess({ adminOnly: true });
  if (!auth.ok) {
    return auth.response;
  }

  const item = await getAdminConfigItem(collection, itemId);
  return item ? NextResponse.json(item, { status: 200 }) : notFound();
}

export async function PATCH(request: NextRequest, { params }: RouteContext) {
  const { collection, itemId } = await params;
  if (!isAdminConfigCollection(collection)) {
    return invalidCollection();
  }

  const auth = await requireAdminAccess({ adminOnly: true });
  if (!auth.ok) {
    return auth.response;
  }

  const item = await updateAdminConfigItem(
    collection,
    itemId,
    await request.json().catch(() => ({})),
    auth.actor,
  );
  return item ? NextResponse.json(item, { status: 200 }) : notFound();
}

export async function DELETE(_request: NextRequest, { params }: RouteContext) {
  const { collection, itemId } = await params;
  if (!isAdminConfigCollection(collection)) {
    return invalidCollection();
  }

  const auth = await requireAdminAccess({ adminOnly: true });
  if (!auth.ok) {
    return auth.response;
  }

  const deleted = await deleteAdminConfigItem(collection, itemId, auth.actor);
  return deleted ? NextResponse.json({ success: true }, { status: 200 }) : notFound();
}
