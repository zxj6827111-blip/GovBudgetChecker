import { DepartmentMatrixPage } from "@/components/materials/DepartmentMatrixPage";

/**
 * 部门材料矩阵。
 *
 * `?year=` 必填：材料按财政年度建槽位，缺少年度时页面提示用户选择，
 * 而不是替用户取当前自然年（那正是把 2024 年度决算当成 2025 年度的成因）。
 */
export default async function DepartmentMaterialsRoutePage({
  params,
  searchParams,
}: {
  params: Promise<{ departmentId: string }>;
  searchParams: Promise<{ year?: string }>;
}) {
  const { departmentId } = await params;
  const { year } = await searchParams;
  return <DepartmentMatrixPage departmentId={departmentId} fiscalYear={year ?? ""} />;
}
