export const ORG_TREE_REFRESH_EVENT = "govbudgetchecker:org-tree-refresh";

export function dispatchOrgTreeRefresh() {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(ORG_TREE_REFRESH_EVENT));
}
