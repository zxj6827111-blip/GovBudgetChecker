import { MaterialDetailPage } from "@/components/materials/MaterialDetailPage";

/**
 * `/materials/slots/[slotId]`（页面 10-13）：材料详情。
 *
 * `?tab=` 决定落在哪个 Tab（overview / findings / coverage / versions / runs）。
 * Tab key 写进查询串而不是拆成子路由，是为了让"以后全局搜索直接落到某个 Tab"
 * 只需要一个链接，不必再维护五条路由。
 */
export default async function MaterialSlotDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ slotId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { slotId } = await params;
  const { tab } = await searchParams;
  return <MaterialDetailPage slotId={slotId} tab={tab ?? "overview"} />;
}
