import { DistrictDepartmentMatrixPage } from "@/components/materials/DistrictDepartmentMatrixPage";

/**
 * 区级主管部门矩阵。
 *
 * `params` / `searchParams` 在 Next 15 里是 Promise：必须 await 之后再取字段
 * （直接当同步对象用会通不过类型检查，也无法从 URL 上稳定读到年度）。
 * `?year=` 由首页或上一步带入，页面不自行猜测年度。
 */
export default async function DistrictMaterialsRoutePage({
  params,
  searchParams,
}: {
  params: Promise<{ districtId: string }>;
  searchParams: Promise<{ year?: string }>;
}) {
  const { districtId } = await params;
  const { year } = await searchParams;
  return <DistrictDepartmentMatrixPage districtId={districtId} initialFiscalYear={year ?? ""} />;
}
