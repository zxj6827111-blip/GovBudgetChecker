import type { OrganizationRecordLike, PreflightResponseLike } from "./uploadCenterAdapters";

export interface UploadOrganizationOption extends OrganizationRecordLike {
  path: string;
  scopeId: string;
  scopeLabel: string;
}

/** 展示完整路径；只让服务端明确授权的部门、单位成为可选项。 */
export function buildUploadOrganizationOptions(organizations: OrganizationRecordLike[]): UploadOrganizationOption[] {
  const byId = new Map(organizations.map((org) => [org.id, org]));
  return organizations
    .filter((org) => org.selectable === true && (org.level === "department" || org.level === "unit"))
    .map((org) => {
      const ancestry: OrganizationRecordLike[] = [];
      const visited = new Set<string>();
      let node: OrganizationRecordLike | undefined = org;
      while (node && !visited.has(node.id)) {
        ancestry.unshift(node);
        visited.add(node.id);
        node = node.parent_id ? byId.get(node.parent_id) : undefined;
      }
      const district = ancestry.find((item) => item.level === "district");
      const city = ancestry.find((item) => item.level === "city");
      const scope = district ?? city ?? ancestry[0];
      return {
        ...org,
        path: ancestry.map((item) => item.name).join(" › "),
        scopeId: scope.id,
        scopeLabel: district ? district.name : city ? `${city.name}（市级）` : scope.name,
      };
    })
    .sort((a, b) => a.path.localeCompare(b.path, "zh-CN"));
}

/** 候选仅用于展示，绝不自动选中；陌生/越权 ID 不得借候选响应进入选择列表。 */
export function selectUploadOrganizationCandidates(
  options: UploadOrganizationOption[],
  preflight: PreflightResponseLike | null | undefined,
): UploadOrganizationOption[] {
  const ids = new Set([
    ...(preflight?.suggestions ?? []).map((item) => item.organization_id),
    ...(preflight?.binding_decision?.candidates ?? []).map((item) => item.organization_id),
    ...(preflight?.binding_decision?.reasons ?? []).flatMap((reason) => (reason.candidates ?? []).map((item) => item.id)),
  ]);
  const byId = new Map(options.map((org) => [org.id, org]));
  const candidates = Array.from(ids).flatMap((id) => byId.get(id) ? [byId.get(id)!] : []);
  if (preflight?.binding_decision?.reasons?.some((reason) => reason.code === "scope_undetermined")) {
    // 只缺部门/单位口径时，把候选部门的真实直属单位放在一起供确认，不按名字伪造本级节点。
    for (const org of [...candidates]) {
      const related = org.level === "department"
        ? options.filter((item) => item.level === "unit" && item.parent_id === org.id)
        : options.filter((item) => item.id === org.parent_id);
      for (const item of related) {
        if (!candidates.some((candidate) => candidate.id === item.id)) candidates.push(item);
      }
    }
  }
  return candidates.slice(0, 6);
}

export function searchUploadOrganizations(
  options: UploadOrganizationOption[],
  query: string,
  level = "",
  scopeId = "",
): UploadOrganizationOption[] {
  const normalize = (value: string) => value.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, "");
  const terms = query.trim().split(/\s+/).filter(Boolean).map(normalize);
  return options.filter((org) => {
    if ((level && org.level !== level) || (scopeId && org.scopeId !== scopeId)) return false;
    const haystack = normalize([org.path, ...(org.keywords ?? [])].join(" "));
    return terms.every((term) => haystack.includes(term));
  });
}
