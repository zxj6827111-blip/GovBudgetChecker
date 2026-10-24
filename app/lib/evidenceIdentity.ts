/** Source authentication is separate from a human decision about the finding. */
export function hasVerifiedSourceIdentity(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const issue = value as Record<string, unknown>;
  const verification = issue.evidence_verification;
  if (!verification || typeof verification !== "object" || Array.isArray(verification)) return false;
  const source = verification as Record<string, unknown>;
  return (issue.evidence_status === "complete" || issue.evidence_status === "verified")
    && source.status === "verified" && source.source_identity_complete === true;
}

export function evidenceExcerptLabel(sourceVerified: boolean): string {
  return sourceVerified ? "原文证据" : "候选证据/计算说明（原文绑定未完成）";
}
