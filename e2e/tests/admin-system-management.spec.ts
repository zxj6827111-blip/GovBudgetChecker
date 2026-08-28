import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

type ConfigItem = {
  id: string;
  name: string;
  enabled: boolean;
  description: string;
  updated_at: string;
  updated_by: string;
  created_at: string;
  created_by: string;
  data: Record<string, unknown>;
};

type UserRecord = {
  username: string;
  is_admin: boolean;
  is_active: boolean;
  organization_ids: string[];
  created_at: number;
  updated_at: number;
};

type AnalysisJobSummary = {
  job_uuid: string;
  filename: string;
  display_title: string;
  display_subtitle: string;
  status: string;
  mode: string;
  updated_at: string;
  organization_name: string;
  report_year: number;
  ai_findings_count: number;
  rule_findings_count: number;
  merged_findings_count: number;
};

type AdminState = {
  reanalyzeAllBodies: Array<Record<string, unknown>>;
  repairBodies: Array<Record<string, unknown>>;
  rematchBodies: Array<Record<string, unknown>>;
  cleanupBodies: Array<Record<string, unknown>>;
  users: UserRecord[];
  userCreates: Array<Record<string, unknown>>;
  userUpdates: Array<{ username: string; body: Record<string, unknown> }>;
  userDeletes: string[];
  analysisListRequests: Array<Record<string, string>>;
  analysisDetailRequests: string[];
  configItems: Record<string, ConfigItem[]>;
  configCreates: Array<{ collection: string; body: Record<string, unknown> }>;
  configUpdates: Array<{ collection: string; itemId: string; body: Record<string, unknown> }>;
  configDeletes: Array<{ collection: string; itemId: string }>;
};

const baseOrgTree = {
  tree: [
    {
      id: "dept-001",
      name: "Finance Bureau",
      level: "department",
      level_name: "department",
      parent_id: null,
      issue_count: 2,
      job_count: 1,
      children: [
        {
          id: "unit-001",
          name: "Finance Unit",
          level: "unit",
          level_name: "unit",
          parent_id: "dept-001",
          issue_count: 0,
          job_count: 0,
          children: [],
        },
      ],
    },
  ],
};

const orgList = {
  organizations: [
    { id: "dept-001", name: "Finance Bureau", level: "department", level_name: "department", parent_id: null },
    { id: "unit-001", name: "Finance Unit", level: "unit", level_name: "unit", parent_id: "dept-001" },
  ],
  total: 2,
};

function nowIso() {
  return new Date("2026-06-08T10:00:00.000Z").toISOString();
}

function makeConfig(id: string, name: string): ConfigItem {
  return {
    id,
    name,
    enabled: true,
    description: `${name} description`,
    created_at: nowIso(),
    created_by: "e2e",
    updated_at: nowIso(),
    updated_by: "e2e",
    data: { code: id, threshold: 1 },
  };
}

function initialState(): AdminState {
  return {
    reanalyzeAllBodies: [],
    repairBodies: [],
    rematchBodies: [],
    cleanupBodies: [],
    users: [
      {
        username: "e2e-admin",
        is_admin: true,
        is_active: true,
        organization_ids: [],
        created_at: 1_710_000_000,
        updated_at: 1_710_000_000,
      },
      {
        username: "analyst",
        is_admin: false,
        is_active: true,
        organization_ids: ["dept-001"],
        created_at: 1_710_000_100,
        updated_at: 1_710_000_100,
      },
    ],
    userCreates: [],
    userUpdates: [],
    userDeletes: [],
    analysisListRequests: [],
    analysisDetailRequests: [],
    configItems: {
      "rule-packages": [makeConfig("rule-1", "Default Rule")],
      "material-mappings": [makeConfig("mapping-1", "Default Mapping")],
      "system-settings": [makeConfig("setting-1", "Runtime Setting")],
      "export-templates": [makeConfig("template-1", "Export Template")],
    },
    configCreates: [],
    configUpdates: [],
    configDeletes: [],
  };
}

async function installAdminMocks(page: Page, state: AdminState) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-admin", display_name: "E2E Admin", is_admin: true } }),
      });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(baseOrgTree) });
      return;
    }

    if (path === "/api/organizations/list") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orgList) });
      return;
    }

    if (path === "/api/users" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ users: state.users }),
      });
      return;
    }

    if (path === "/api/users" && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.userCreates.push(body);
      const username = String(body.username ?? "");
      const next: UserRecord = {
        username,
        is_admin: Boolean(body.is_admin),
        is_active: true,
        organization_ids: Array.isArray(body.organization_ids) ? body.organization_ids.map(String) : [],
        created_at: 1_710_000_200,
        updated_at: 1_710_000_200,
      };
      state.users = [...state.users, next];
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(next) });
      return;
    }

    const userMatch = path.match(/^\/api\/users\/([^/]+)$/);
    if (userMatch) {
      const username = decodeURIComponent(userMatch[1]);
      if (method === "PATCH") {
        const body = req.postDataJSON() as Record<string, unknown>;
        state.userUpdates.push({ username, body });
        state.users = state.users.map((user) =>
          user.username === username
            ? {
                ...user,
                is_admin: typeof body.is_admin === "boolean" ? body.is_admin : user.is_admin,
                is_active: typeof body.is_active === "boolean" ? body.is_active : user.is_active,
                organization_ids: Array.isArray(body.organization_ids) ? body.organization_ids.map(String) : user.organization_ids,
                updated_at: 1_710_000_300,
              }
            : user,
        );
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true }) });
        return;
      }

      if (method === "DELETE") {
        state.userDeletes.push(username);
        state.users = state.users.filter((user) => user.username !== username);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true }) });
        return;
      }
    }

    if (path === "/api/admin/analysis-results" && method === "GET") {
      state.analysisListRequests.push(Object.fromEntries(url.searchParams.entries()));
      const items: AnalysisJobSummary[] = [
        {
          job_uuid: "analysis-001",
          filename: "budget-report.pdf",
          display_title: "Budget Report",
          display_subtitle: "Finance Bureau 2026",
          status: "done",
          mode: "dual",
          updated_at: nowIso(),
          organization_name: "Finance Bureau",
          report_year: 2026,
          ai_findings_count: 1,
          rule_findings_count: 2,
          merged_findings_count: 3,
        },
        {
          job_uuid: "analysis-002",
          filename: "final-report.pdf",
          display_title: "Final Report",
          display_subtitle: "Finance Unit 2025",
          status: "error",
          mode: "legacy",
          updated_at: nowIso(),
          organization_name: "Finance Unit",
          report_year: 2025,
          ai_findings_count: 0,
          rule_findings_count: 1,
          merged_findings_count: 1,
        },
      ];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          available: true,
          summary: {
            total: items.length,
            done: 1,
            processing: 0,
            queued: 0,
            error: 1,
            ai_findings_total: 1,
            rule_findings_total: 3,
          },
          items,
        }),
      });
      return;
    }

    const analysisDetailMatch = path.match(/^\/api\/admin\/analysis-results\/([^/]+)$/);
    if (analysisDetailMatch && method === "GET") {
      const jobUuid = decodeURIComponent(analysisDetailMatch[1]);
      state.analysisDetailRequests.push(jobUuid);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job_uuid: jobUuid,
          filename: `${jobUuid}.pdf`,
          display_title: jobUuid === "analysis-002" ? "Final Report" : "Budget Report",
          display_subtitle: jobUuid === "analysis-002" ? "Finance Unit 2025" : "Finance Bureau 2026",
          status: jobUuid === "analysis-002" ? "error" : "done",
          mode: jobUuid === "analysis-002" ? "legacy" : "dual",
          organization_name: jobUuid === "analysis-002" ? "Finance Unit" : "Finance Bureau",
          report_year: jobUuid === "analysis-002" ? 2025 : 2026,
          ai_findings_count: jobUuid === "analysis-002" ? 0 : 1,
          rule_findings_count: jobUuid === "analysis-002" ? 1 : 2,
          merged_findings_count: jobUuid === "analysis-002" ? 1 : 3,
          updated_at: nowIso(),
          ai_findings: jobUuid === "analysis-002" ? [] : [{ id: "ai-1", source: "ai", severity: "medium", title: "AI finding", message: "AI message", page_number: 1 }],
          rule_findings: [{ id: "rule-1", source: "rule", rule_id: "R001", severity: "high", title: "Rule finding", message: "Rule message", page_number: 2 }],
          structured_ingest: { status: "done", document_version_id: 1, facts_count: 8, ps_sync: { report_id: "ps-1" } },
          result_meta: { elapsed_ms: { total: 1200 } },
        }),
      });
      return;
    }

    if (path === "/api/jobs" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0 }) });
      return;
    }

    if (path === "/api/gbc-ui-demo/workflow" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ issues: {}, packages: [] }) });
      return;
    }

    if (path === "/api/jobs/reanalyze-all" && method === "POST") {
      state.reanalyzeAllBodies.push(req.postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          requested_count: 1,
          selected_count: 1,
          created_count: 1,
          skipped_count: 0,
          failed_count: 0,
          created: [{ job_id: "reanalyze-job-1", source_job_id: "source-job-1", scope_name: "Finance Bureau", scope_level: "department", status: "queued" }],
          skipped: [],
          failed: [],
        }),
      });
      return;
    }

    if (path === "/api/jobs/reanalyze-job-1" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ job_id: "reanalyze-job-1", status: "done", progress: 100 }),
      });
      return;
    }

    if (path === "/api/jobs/repair-missing-links" && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.repairBodies.push(body);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidate_count: 1,
          repaired_count: body.dry_run ? 0 : 1,
          linked_from_status_count: 1,
          matched_from_pdf_count: 0,
          skipped_count: 0,
          failed_count: 0,
          repairs: [{ job_id: "source-job-1", organization_name: "Finance Bureau" }],
          skipped: [],
          failed: [],
        }),
      });
      return;
    }

    if (path === "/api/jobs/rematch-organizations" && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.rematchBodies.push(body);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidate_count: 1,
          updated_count: body.dry_run ? 0 : 1,
          skipped_count: 0,
          failed_count: 0,
          fast_path_hits: 1,
          pdf_text_fallback_hits: 0,
          matches: [{ job_id: "source-job-1", organization_name: "Finance Bureau" }],
          skipped: [],
          failed: [],
        }),
      });
      return;
    }

    if (path === "/api/jobs/structured-ingest-cleanup" && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.cleanupBodies.push(body);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: body.dry_run,
          scanned_job_count: 2,
          scope_count: 1,
          matched_job_count: 2,
          kept_job_count: 1,
          cleanup_document_version_count: body.dry_run ? 1 : 0,
          cleanup_job_count: 1,
          blocked_document_version_count: 0,
          skipped_job_count: 0,
          deleted_document_version_count: body.dry_run ? 0 : 1,
          updated_job_count: body.dry_run ? 0 : 1,
          kept_jobs: [{ job_id: "job-latest", filename: "latest.pdf", document_version_id: 2 }],
          cleanup_document_versions: body.dry_run ? [{ document_version_id: 1, filename: "old.pdf", jobs: [] }] : [],
          blocked_document_versions: [],
          skipped_jobs: [],
        }),
      });
      return;
    }

    const collectionMatch = path.match(/^\/api\/admin\/config\/([^/]+)$/);
    if (collectionMatch) {
      const collection = decodeURIComponent(collectionMatch[1]);
      if (method === "GET") {
        const items = state.configItems[collection] ?? [];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, total: items.length }) });
        return;
      }

      if (method === "POST") {
        const body = req.postDataJSON() as Record<string, unknown>;
        state.configCreates.push({ collection, body });
        const next: ConfigItem = {
          id: `${collection}-created-${state.configCreates.length}`,
          name: String(body.name ?? ""),
          enabled: Boolean(body.enabled),
          description: String(body.description ?? ""),
          created_at: nowIso(),
          created_by: "e2e",
          updated_at: nowIso(),
          updated_by: "e2e",
          data: (body.data && typeof body.data === "object" && !Array.isArray(body.data)) ? body.data as Record<string, unknown> : {},
        };
        state.configItems[collection] = [...(state.configItems[collection] ?? []), next];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(next) });
        return;
      }
    }

    const itemMatch = path.match(/^\/api\/admin\/config\/([^/]+)\/([^/]+)$/);
    if (itemMatch) {
      const collection = decodeURIComponent(itemMatch[1]);
      const itemId = decodeURIComponent(itemMatch[2]);

      if (method === "PATCH") {
        const body = req.postDataJSON() as Record<string, unknown>;
        state.configUpdates.push({ collection, itemId, body });
        state.configItems[collection] = (state.configItems[collection] ?? []).map((item) =>
          item.id === itemId
            ? { ...item, name: String(body.name ?? item.name), description: String(body.description ?? item.description), enabled: Boolean(body.enabled), data: body.data as Record<string, unknown>, updated_at: nowIso() }
            : item,
        );
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
        return;
      }

      if (method === "DELETE") {
        state.configDeletes.push({ collection, itemId });
        state.configItems[collection] = (state.configItems[collection] ?? []).filter((item) => item.id !== itemId);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
        return;
      }
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

async function openSettings(page: Page, state: AdminState) {
  await page.context().addCookies([sessionCookie]);
  await installAdminMocks(page, state);
  await page.goto("/viewer/gbc-ui-demo?page=settings");
  await expect(page.getByTestId("admin-system-management")).toBeVisible({ timeout: 20_000 });
}

test.describe("Admin system management actions", () => {
  test("user management buttons create update reset scope and delete users", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    page.on("dialog", (dialog) => {
      if (dialog.type() === "prompt") {
        void dialog.accept("new-pass-123");
      } else {
        void dialog.accept();
      }
    });
    await openSettings(page, state);

    await page.getByTestId("admin-section-users").click();
    await expect(page.getByTestId("admin-users-panel")).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("admin-users-new-username").fill("new-user");
    await page.getByTestId("admin-users-new-password").fill("secret123");
    await page.getByTestId("admin-users-new-org-unit-001").check();
    await page.getByTestId("admin-users-create").click();

    await expect.poll(() => state.userCreates).toEqual([
      { username: "new-user", password: "secret123", is_admin: false, organization_ids: ["unit-001"] },
    ]);
    await expect(page.getByTestId("admin-users-row-new-user")).toBeVisible();

    await page.getByTestId("admin-users-role-new-user").click();
    await expect.poll(() => state.userUpdates.at(-1)).toMatchObject({
      username: "new-user",
      body: { is_admin: true },
    });

    await page.getByTestId("admin-users-active-new-user").click();
    await expect.poll(() => state.userUpdates.at(-1)).toMatchObject({
      username: "new-user",
      body: { is_active: false },
    });

    await page.getByTestId("admin-users-role-new-user").click();
    await expect.poll(() => state.userUpdates.at(-1)).toMatchObject({
      username: "new-user",
      body: { is_admin: false },
    });

    await page.getByTestId("admin-users-scope-open-new-user").click();
    await page.getByTestId("admin-users-scope-org-new-user-dept-001").check();
    await page.getByTestId("admin-users-scope-save-new-user").click();
    await expect.poll(() => state.userUpdates.at(-1)).toMatchObject({
      username: "new-user",
      body: { organization_ids: ["unit-001", "dept-001"] },
    });

    await page.getByTestId("admin-users-password-new-user").click();
    await expect.poll(() => state.userUpdates.at(-1)).toMatchObject({
      username: "new-user",
      body: { password: "new-pass-123" },
    });

    await page.getByTestId("admin-users-delete-new-user").click();
    await expect.poll(() => state.userDeletes).toEqual(["new-user"]);
    await expect(page.getByTestId("admin-users-row-new-user")).toHaveCount(0);
  });

  test("analysis results panel searches filters refreshes and switches detail", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    await openSettings(page, state);

    await page.getByTestId("admin-section-analysis").click();
    await expect(page.getByTestId("admin-analysis-panel")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("admin-analysis-job-analysis-001")).toBeVisible();
    await expect.poll(() => state.analysisDetailRequests).toContain("analysis-001");

    await page.getByTestId("admin-analysis-search").fill("Finance");
    await page.getByTestId("admin-analysis-status").selectOption("done");
    await page.getByTestId("admin-analysis-mode").selectOption("dual");
    await page.getByTestId("admin-analysis-submit").click();

    await expect.poll(() => state.analysisListRequests.at(-1)).toEqual({
      limit: "50",
      search: "Finance",
      status: "done",
      mode: "dual",
    });

    await page.getByTestId("admin-analysis-job-analysis-002").click();
    await expect.poll(() => state.analysisDetailRequests).toContain("analysis-002");
    await expect(page.getByRole("heading", { name: "Final Report" })).toBeVisible();

    const requestCount = state.analysisListRequests.length;
    await page.getByTestId("admin-analysis-refresh").click();
    await expect.poll(() => state.analysisListRequests.length).toBeGreaterThan(requestCount);
  });

  test("operations buttons call the expected maintenance APIs", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    await openSettings(page, state);

    await page.getByTestId("admin-section-operations").click();
    await expect(page.getByTestId("admin-operations-open-upload")).toBeVisible();

    await page.getByTestId("admin-operations-open-upload").click();
    await expect(page.getByTestId("batch-upload-modal")).toBeVisible();
    await page.getByTestId("batch-upload-close").click();
    await expect(page.getByTestId("batch-upload-modal")).toHaveCount(0);

    await page.getByTestId("admin-reanalyze-ai-toggle").uncheck();
    await page.getByTestId("admin-operations-reanalyze-all").click();
    await expect.poll(() => state.reanalyzeAllBodies).toEqual([
      { latest_per_department: true, use_local_rules: true, use_ai_assist: false },
    ]);
    await expect(page.getByTestId("reanalyze-progress-dialog")).toBeVisible();
    await page.getByTestId("reanalyze-progress-close").click();

    await page.getByTestId("admin-operations-repair-preview").click();
    await expect.poll(() => state.repairBodies.at(-1)).toEqual({ dry_run: true });
    await expect(page.getByTestId("admin-operations-repair-results")).toBeVisible();

    await page.getByTestId("admin-operations-repair-execute").click();
    await expect.poll(() => state.repairBodies.at(-1)).toEqual({ dry_run: false });

    await page.getByTestId("admin-operations-rematch-preview").click();
    await expect.poll(() => state.rematchBodies.at(-1)).toEqual({ dry_run: true });
    await expect(page.getByTestId("admin-operations-rematch-results")).toBeVisible();

    await page.getByTestId("admin-operations-rematch-execute").click();
    await expect.poll(() => state.rematchBodies.at(-1)).toEqual({ dry_run: false });

    await page.getByTestId("admin-operations-cleanup-preview").click();
    await expect.poll(() => state.cleanupBodies.at(-1)).toEqual({ dry_run: true });
    await expect(page.getByTestId("structured-cleanup-dialog")).toBeVisible();
    await page.getByTestId("structured-cleanup-confirm").click();
    await expect.poll(() => state.cleanupBodies.at(-1)).toEqual({ dry_run: false });
  });

  test("config panels create edit cancel and delete records", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    page.on("dialog", (dialog) => void dialog.accept());
    await openSettings(page, state);

    for (const [index, spec] of [
      { section: "rules", collection: "rule-packages", label: "Rule Package" },
      { section: "mappings", collection: "material-mappings", label: "Material Mapping" },
      { section: "settings", collection: "system-settings", label: "System Setting" },
      { section: "settings", collection: "export-templates", label: "Export Template" },
    ].entries()) {
      await page.getByTestId(`admin-section-${spec.section}`).click();
      await expect(page.getByTestId(`admin-config-panel-${spec.collection}`)).toBeVisible({ timeout: 20_000 });

      const createdName = `New ${spec.label}`;
      const updatedName = `Updated ${spec.label}`;
      const createdData = { version: `e2e-${index}`, collection: spec.collection };
      const createCount = state.configCreates.length;

      await page.getByTestId(`admin-config-new-${spec.collection}`).click();
      await page.getByTestId(`admin-config-name-${spec.collection}`).fill(createdName);
      await page.getByTestId(`admin-config-description-${spec.collection}`).fill("Created by e2e");
      await page.getByTestId(`admin-config-enabled-${spec.collection}`).uncheck();
      await page.getByTestId(`admin-config-data-${spec.collection}`).fill(JSON.stringify(createdData, null, 2));
      await page.getByTestId(`admin-config-save-${spec.collection}`).click();

      await expect.poll(() => state.configCreates.length).toBe(createCount + 1);
      expect(state.configCreates.at(-1)).toMatchObject({
        collection: spec.collection,
        body: { name: createdName, description: "Created by e2e", enabled: false, data: createdData },
      });
      const createdId = state.configItems[spec.collection].find((item) => item.name === createdName)?.id;
      expect(createdId).toBeTruthy();
      await expect(page.getByTestId(`admin-config-item-${spec.collection}-${createdId}`)).toBeVisible();

      const updateCount = state.configUpdates.length;
      await page.getByTestId(`admin-config-edit-${spec.collection}-${createdId}`).click();
      await expect(page.getByTestId(`admin-config-cancel-${spec.collection}`)).toBeVisible();
      await page.getByTestId(`admin-config-name-${spec.collection}`).fill(updatedName);
      await page.getByTestId(`admin-config-enabled-${spec.collection}`).check();
      await page.getByTestId(`admin-config-save-${spec.collection}`).click();

      await expect.poll(() => state.configUpdates.length).toBe(updateCount + 1);
      expect(state.configUpdates.at(-1)).toMatchObject({
        collection: spec.collection,
        itemId: createdId,
        body: { name: updatedName, enabled: true },
      });

      await page.getByTestId(`admin-config-edit-${spec.collection}-${createdId}`).click();
      await page.getByTestId(`admin-config-cancel-${spec.collection}`).click();
      await expect(page.getByTestId(`admin-config-cancel-${spec.collection}`)).toHaveCount(0);

      const deleteCount = state.configDeletes.length;
      await page.getByTestId(`admin-config-delete-${spec.collection}-${createdId}`).click();
      await expect.poll(() => state.configDeletes.length).toBe(deleteCount + 1);
      expect(state.configDeletes.at(-1)).toEqual({ collection: spec.collection, itemId: createdId });
      await expect(page.getByTestId(`admin-config-item-${spec.collection}-${createdId}`)).toHaveCount(0);
    }
  });
});
