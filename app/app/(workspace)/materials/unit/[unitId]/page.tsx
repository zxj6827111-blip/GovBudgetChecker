import { UnitTimelinePage } from "@/components/materials/UnitTimelinePage";

/**
 * `/materials/unit/[unitId]`（页面 09）：单位多年度材料时间轴。
 *
 * 与区县/部门页同一形态：服务端组件只做参数解析，取数交给客户端组件
 * （需要带浏览器会话 cookie 走同源代理）。
 */
export default async function MaterialUnitPage({
  params,
}: {
  params: Promise<{ unitId: string }>;
}) {
  const { unitId } = await params;
  return <UnitTimelinePage unitId={unitId} />;
}
