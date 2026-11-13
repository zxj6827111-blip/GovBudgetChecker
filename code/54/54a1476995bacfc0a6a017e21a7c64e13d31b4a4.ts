export const REVIEW_WORKLIST_UPDATED = "govbudgetchecker:review-worklist-updated";
export const REVIEW_WORKLIST_INVALIDATED = "govbudgetchecker:review-worklist-invalidated";

export function publishReviewPendingCount(count: number | null) {
  window.dispatchEvent(new CustomEvent(REVIEW_WORKLIST_UPDATED, { detail: count }));
}

/** 写动作成功后重新读取持久化状态，不在客户端自行扣减待办。 */
export function invalidateReviewWorklist() {
  window.dispatchEvent(new Event(REVIEW_WORKLIST_INVALIDATED));
}
