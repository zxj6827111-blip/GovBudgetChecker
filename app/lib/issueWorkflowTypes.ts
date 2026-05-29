export type IssueWorkflowStatus =
  | "pending"
  | "confirmed"
  | "no_issue"
  | "needs_review"
  | "in_package";

export type IssueWorkflowRecord = {
  key: string;
  job_id: string;
  issue_id: string;
  status: IssueWorkflowStatus;
  title?: string;
  severity?: string;
  page?: number | null;
  organization_id?: string | null;
  organization_name?: string | null;
  note?: string;
  updated_at: string;
};

export type RemediationPackageRecord = {
  id: string;
  name: string;
  organization_id?: string | null;
  organization_name?: string | null;
  job_ids: string[];
  issue_keys: string[];
  status: "draft" | "ready" | "submitted";
  created_at: string;
  updated_at: string;
};

export type IssueWorkflowState = {
  issues: Record<string, IssueWorkflowRecord>;
  packages: RemediationPackageRecord[];
  updated_at?: string;
};

export function buildIssueWorkflowKey(jobId: string, issueId: string): string {
  return `${jobId}::${issueId}`;
}
