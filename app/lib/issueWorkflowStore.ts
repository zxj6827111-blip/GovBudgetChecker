import "server-only";

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import {
  buildIssueWorkflowKey,
  type IssueWorkflowRecord,
  type IssueWorkflowState,
  type IssueWorkflowStatus,
  type RemediationPackageRecord,
} from "@/lib/issueWorkflowTypes";

type UpdateIssueInput = Omit<IssueWorkflowRecord, "key" | "updated_at">;
type CreatePackageInput = {
  name?: string;
  organization_id?: string | null;
  organization_name?: string | null;
  job_ids?: string[];
  issue_keys?: string[];
};

const STORE_PATH = resolve(process.cwd(), "..", "data", "issue_workflow.json");
let fileLock: Promise<unknown> = Promise.resolve();

function withFileLock<T>(fn: () => Promise<T>): Promise<T> {
  const next = fileLock.then(fn, fn);
  fileLock = next.then(
    () => undefined,
    () => undefined,
  );
  return next;
}

function nowIso(): string {
  return new Date().toISOString();
}

function normalizeIssueStatus(value: unknown): IssueWorkflowStatus {
  const text = String(value ?? "").trim();
  if (
    text === "confirmed" ||
    text === "no_issue" ||
    text === "needs_review" ||
    text === "in_package"
  ) {
    return text;
  }
  return "pending";
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    const text = String(item ?? "").trim();
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    result.push(text);
  }
  return result;
}

function normalizeState(value: unknown): IssueWorkflowState {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const rawIssues =
    raw.issues && typeof raw.issues === "object" && !Array.isArray(raw.issues)
      ? (raw.issues as Record<string, unknown>)
      : {};
  const issues: Record<string, IssueWorkflowRecord> = {};

  for (const [key, item] of Object.entries(rawIssues)) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const row = item as Record<string, unknown>;
    const jobId = String(row.job_id ?? "").trim();
    const issueId = String(row.issue_id ?? "").trim();
    if (!jobId || !issueId) {
      continue;
    }
    const resolvedKey = key || buildIssueWorkflowKey(jobId, issueId);
    issues[resolvedKey] = {
      key: resolvedKey,
      job_id: jobId,
      issue_id: issueId,
      status: normalizeIssueStatus(row.status),
      title: String(row.title ?? "").trim() || undefined,
      severity: String(row.severity ?? "").trim() || undefined,
      page: Number.isFinite(Number(row.page)) ? Number(row.page) : null,
      organization_id: row.organization_id == null ? null : String(row.organization_id),
      organization_name: row.organization_name == null ? null : String(row.organization_name),
      note: String(row.note ?? "").trim() || undefined,
      updated_at: String(row.updated_at ?? "") || nowIso(),
    };
  }

  const packages = Array.isArray(raw.packages)
    ? raw.packages
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
        .map((item) => {
          const id = String(item.id ?? "").trim() || `pkg-${Date.now()}`;
          const status = String(item.status ?? "").trim();
          return {
            id,
            name: String(item.name ?? "").trim() || "未命名整改包",
            organization_id: item.organization_id == null ? null : String(item.organization_id),
            organization_name: item.organization_name == null ? null : String(item.organization_name),
            job_ids: normalizeStringArray(item.job_ids),
            issue_keys: normalizeStringArray(item.issue_keys),
            status: status === "ready" || status === "submitted" ? status : "draft",
            created_at: String(item.created_at ?? "") || nowIso(),
            updated_at: String(item.updated_at ?? "") || nowIso(),
          } satisfies RemediationPackageRecord;
        })
    : [];

  return {
    issues,
    packages,
    updated_at: String(raw.updated_at ?? "") || undefined,
  };
}

async function readStateUnlocked(): Promise<IssueWorkflowState> {
  try {
    const raw = await readFile(STORE_PATH, "utf-8");
    return normalizeState(JSON.parse(raw));
  } catch {
    return { issues: {}, packages: [] };
  }
}

async function writeStateUnlocked(state: IssueWorkflowState): Promise<IssueWorkflowState> {
  await mkdir(dirname(STORE_PATH), { recursive: true });
  const payload = {
    issues: state.issues,
    packages: state.packages,
    updated_at: nowIso(),
  };
  const tempPath = `${STORE_PATH}.tmp`;
  await writeFile(tempPath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
  await rename(tempPath, STORE_PATH);
  return payload;
}

export async function readIssueWorkflowState(): Promise<IssueWorkflowState> {
  return withFileLock(readStateUnlocked);
}

export async function updateIssueWorkflow(input: UpdateIssueInput): Promise<IssueWorkflowState> {
  return withFileLock(async () => {
    const jobId = String(input.job_id ?? "").trim();
    const issueId = String(input.issue_id ?? "").trim();
    if (!jobId || !issueId) {
      throw new Error("job_id and issue_id are required");
    }

    const state = await readStateUnlocked();
    const key = buildIssueWorkflowKey(jobId, issueId);
    state.issues[key] = {
      key,
      job_id: jobId,
      issue_id: issueId,
      status: normalizeIssueStatus(input.status),
      title: input.title,
      severity: input.severity,
      page: input.page ?? null,
      organization_id: input.organization_id ?? null,
      organization_name: input.organization_name ?? null,
      note: input.note,
      updated_at: nowIso(),
    };
    return writeStateUnlocked(state);
  });
}

export async function createRemediationPackage(
  input: CreatePackageInput,
): Promise<{ state: IssueWorkflowState; package: RemediationPackageRecord }> {
  return withFileLock(async () => {
    const state = await readStateUnlocked();
    const issueKeys = normalizeStringArray(input.issue_keys);
    const jobIds = normalizeStringArray(input.job_ids);
    const now = nowIso();
    const record: RemediationPackageRecord = {
      id: `pkg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: String(input.name ?? "").trim() || "整改包",
      organization_id: input.organization_id ?? null,
      organization_name: input.organization_name ?? null,
      job_ids: jobIds,
      issue_keys: issueKeys,
      status: "ready",
      created_at: now,
      updated_at: now,
    };

    for (const key of issueKeys) {
      const current = state.issues[key];
      if (current) {
        state.issues[key] = {
          ...current,
          status: "in_package",
          updated_at: now,
        };
      }
    }

    state.packages = [record, ...state.packages];
    const nextState = await writeStateUnlocked(state);
    return { state: nextState, package: record };
  });
}
