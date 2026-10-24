import { DepartmentReportsPage } from "@/components/reports/DepartmentReportsPage";

/**
 * 部门报告详情：部门、单位、地区汇总（地区组织 ID）与待确认归属（unassigned）各有独立地址。
 * `?tab=units` 打开部门的下属单位，`?self=1` 只看部门本级的单位口径材料。
 */
export default async function DepartmentReportDetailRoutePage({ params }: { params: Promise<{ organizationId: string }> }) {
  const { organizationId } = await params;
  return <DepartmentReportsPage routeOrganizationId={organizationId} />;
}
