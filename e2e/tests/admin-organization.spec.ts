import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

type OrgLevel = "department" | "unit";

type OrgRecord = {
  id: string;
  name: string;
  level: OrgLevel;
  parent_id: string | null;
  job_count: number;
  issue_count: number;
};

type MockState = {
  orgs: OrgRecord[];
  createDepartmentCalls: number;
  createUnitCalls: number;
  renameCalls: number;
  deletePreviewCalls: number;
  deleteCalls: number;
  importCalls: number;
};

function sortByName<T extends { name: string }>(items: T[]) {
  return [...items].sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

function buildTree(orgs: OrgRecord[], parentId: string | null = null): Array<Record<string, unknown>> {
  return sortByName(orgs.filter((org) => org.parent_id === parentId)).map((org) => ({
    id: org.id,
    name: org.name,
    level: org.level,
    level_name: org.level,
    parent_id: org.parent_id,
    job_count: org.job_count,
    issue_count: org.issue_count,
    children: buildTree(orgs, org.id),
  }));
}

function buildDepartments(orgs: OrgRecord[]) {
  return buildTree(orgs).filter((node) => node.level === "department");
}

function collectDescendantIds(orgs: OrgRecord[], rootId: string) {
  const result = new Set<string>();
  const queue = [rootId];

  while (queue.length > 0) {
    const currentId = queue.shift();
    if (!currentId || result.has(currentId)) {
      continue;
    }
    result.add(currentId);

    for (const org of orgs) {
      if (org.parent_id === currentId) {
        queue.push(org.id);
      }
    }
  }

  return [...result];
}

async function installOrganizationApiMocks(page: Page, state: MockState) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            username: "e2e-admin",
            display_name: "E2E Admin",
            is_admin: true,
          },
        }),
      });
      return;
    }

    if (path === "/api/departments" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          departments: buildDepartments(state.orgs),
          total: state.orgs.filter((org) => org.level === "department").length,
        }),
      });
      return;
    }

    if (path === "/api/organizations/list" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          organizations: state.orgs.map((org) => ({
            id: org.id,
            name: org.name,
            level: org.level,
            level_name: org.level,
            parent_id: org.parent_id,
          })),
          total: state.orgs.length,
        }),
      });
      return;
    }

    if (path === "/api/organizations" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tree: buildTree(state.orgs),
          total: state.orgs.length,
        }),
      });
      return;
    }

    if (path === "/api/organizations" && method === "POST") {
      state.createDepartmentCalls += 1;
      const payload = req.postDataJSON() as { name?: string };
      const id = state.createDepartmentCalls === 1 ? "dept-created" : `dept-created-${state.createDepartmentCalls}`;
      const created: OrgRecord = {
        id,
        name: String(payload.name ?? "").trim(),
        level: "department",
        parent_id: null,
        job_count: 0,
        issue_count: 0,
      };
      state.orgs.push(created);

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: created.id,
          name: created.name,
          level: created.level,
          parent_id: created.parent_id,
        }),
      });
      return;
    }

    const createUnitMatch = path.match(/^\/api\/departments\/([^/]+)\/units$/);
    if (createUnitMatch && method === "POST") {
      state.createUnitCalls += 1;
      const payload = req.postDataJSON() as { name?: string };
      const parentId = decodeURIComponent(createUnitMatch[1]);
      const id = state.createUnitCalls === 1 ? "unit-created" : `unit-created-${state.createUnitCalls}`;
      const created: OrgRecord = {
        id,
        name: String(payload.name ?? "").trim(),
        level: "unit",
        parent_id: parentId,
        job_count: 0,
        issue_count: 0,
      };
      state.orgs.push(created);

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: created.id,
          name: created.name,
          level: created.level,
          parent_id: created.parent_id,
        }),
      });
      return;
    }

    const updateOrgMatch = path.match(/^\/api\/organizations\/([^/]+)$/);
    if (updateOrgMatch && method === "PUT") {
      state.renameCalls += 1;
      const orgId = decodeURIComponent(updateOrgMatch[1]);
      const payload = req.postDataJSON() as { name?: string };
      const target = state.orgs.find((org) => org.id === orgId);
      if (target) {
        target.name = String(payload.name ?? "").trim();
      }

      await route.fulfill({
        status: target ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(
          target
            ? {
                id: target.id,
                name: target.name,
                level: target.level,
                parent_id: target.parent_id,
              }
            : { detail: "organization not found" },
        ),
      });
      return;
    }

    const deletePreviewMatch = path.match(/^\/api\/organizations\/([^/]+)\/delete-preview$/);
    if (deletePreviewMatch && method === "GET") {
      state.deletePreviewCalls += 1;
      const orgId = decodeURIComponent(deletePreviewMatch[1]);
      const descendantIds = collectDescendantIds(state.orgs, orgId);
      const organizations = state.orgs.filter((org) => descendantIds.includes(org.id));

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          summary: {
            organization_count: organizations.length,
            unit_count: organizations.filter((org) => org.level === "unit").length,
            job_count: organizations.reduce((sum, org) => sum + org.job_count, 0),
          },
        }),
      });
      return;
    }

    const deleteMatch = path.match(/^\/api\/organizations\/([^/]+)\/delete$/);
    if (deleteMatch && method === "POST") {
      state.deleteCalls += 1;
      const orgId = decodeURIComponent(deleteMatch[1]);
      const descendantIds = collectDescendantIds(state.orgs, orgId);
      state.orgs = state.orgs.filter((org) => !descendantIds.includes(org.id));

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          deleted_ids: descendantIds,
        }),
      });
      return;
    }

    if (path === "/api/organizations/import" && method === "POST") {
      state.importCalls += 1;

      if (!state.orgs.some((org) => org.id === "dept-imported")) {
        state.orgs.push(
          {
            id: "dept-imported",
            name: "导入部门",
            level: "department",
            parent_id: null,
            job_count: 0,
            issue_count: 0,
          },
          {
            id: "unit-imported",
            name: "导入单位",
            level: "unit",
            parent_id: "dept-imported",
            job_count: 0,
            issue_count: 0,
          },
        );
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          imported: 2,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    });
  });
}

test.describe("Admin organization regression", () => {
  test("organization panel supports create unit delete and static assets stay healthy", async ({ page }) => {
    test.setTimeout(60_000);

    const state: MockState = {
      orgs: [
        {
          id: "dept-001",
          name: "财政局",
          level: "department",
          parent_id: null,
          job_count: 2,
          issue_count: 1,
        },
        {
          id: "unit-001",
          name: "财政局下属单位",
          level: "unit",
          parent_id: "dept-001",
          job_count: 0,
          issue_count: 0,
        },
      ],
      createDepartmentCalls: 0,
      createUnitCalls: 0,
      renameCalls: 0,
      deletePreviewCalls: 0,
      deleteCalls: 0,
      importCalls: 0,
    };
    const staticFailures: string[] = [];

    await page.context().addCookies([sessionCookie]);
    await installOrganizationApiMocks(page, state);

    page.on("response", (response) => {
      if (response.url().includes("/_next/static/") && response.status() >= 400) {
        staticFailures.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.goto("/admin?tab=organization");

    await expect(page.getByTestId("admin-org-panel")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("admin-org-create-department")).toBeVisible();
    await expect(page.getByTestId("admin-org-create-unit")).toBeDisabled();
    await expect(page.getByTestId("admin-org-delete-current")).toBeDisabled();
    expect(staticFailures).toEqual([]);

    await page.getByTestId("organization-tree-node-dept-001").click();
    await expect(page.getByTestId("admin-org-create-unit")).toBeEnabled();
    await expect(page.getByTestId("admin-org-delete-current")).toBeEnabled();

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("prompt");
      expect(dialog.message()).toContain("请输入新部门名称");
      await dialog.accept("审计局");
    });
    await page.getByTestId("admin-org-create-department").click();

    await expect.poll(() => state.createDepartmentCalls).toBe(1);
    await expect(page.getByTestId("organization-tree-node-dept-created")).toBeVisible();
    await expect(page.getByTestId("admin-org-selected-name")).toHaveText("审计局");
    await expect(page.getByTestId("admin-org-selection")).toContainText("部门");

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("prompt");
      expect(dialog.message()).toContain("审计局");
      await dialog.accept("审计局下属单位");
    });
    await page.getByTestId("admin-org-create-unit").click();

    await expect.poll(() => state.createUnitCalls).toBe(1);
    await expect(page.getByTestId("admin-org-selected-name")).toHaveText("审计局下属单位");
    await expect(page.getByTestId("admin-org-selection")).toContainText("单位");
    await expect(page.getByTestId("admin-org-create-unit")).toBeDisabled();

    page.once("dialog", async (dialog) => {
      expect(dialog.type()).toBe("confirm");
      expect(dialog.message()).toContain("审计局下属单位");
      await dialog.accept();
    });
    await page.getByTestId("admin-org-delete-current").click();

    await expect.poll(() => state.deletePreviewCalls).toBe(1);
    await expect.poll(() => state.deleteCalls).toBe(1);
    await expect(page.getByTestId("admin-org-selection-empty")).toBeVisible();
    expect(state.orgs.some((org) => org.id === "unit-created")).toBe(false);
  });

  test("organization tree supports create rename import flows", async ({ page }) => {
    test.setTimeout(60_000);

    const state: MockState = {
      orgs: [
        {
          id: "dept-001",
          name: "财政局",
          level: "department",
          parent_id: null,
          job_count: 1,
          issue_count: 0,
        },
        {
          id: "unit-001",
          name: "财政局下属单位",
          level: "unit",
          parent_id: "dept-001",
          job_count: 0,
          issue_count: 0,
        },
      ],
      createDepartmentCalls: 0,
      createUnitCalls: 0,
      renameCalls: 0,
      deletePreviewCalls: 0,
      deleteCalls: 0,
      importCalls: 0,
    };
    const dialogMessages: string[] = [];

    await page.context().addCookies([sessionCookie]);
    await installOrganizationApiMocks(page, state);

    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.goto("/admin?tab=organization");

    await expect(page.getByTestId("organization-tree-create-department")).toBeVisible({
      timeout: 20_000,
    });

    await page.getByTestId("organization-tree-create-department").click();
    await expect(page.getByTestId("organization-tree-modal-input")).toBeVisible();
    await page.getByTestId("organization-tree-modal-input").fill("审计局");
    await page.getByTestId("organization-tree-modal-submit").click();

    await expect.poll(() => state.createDepartmentCalls).toBe(1);
    await expect(page.getByTestId("organization-tree-node-dept-created")).toBeVisible();

    await page.getByTestId("organization-tree-edit-dept-001").click();
    await expect(page.getByTestId("organization-tree-modal-input")).toBeVisible();
    await page.getByTestId("organization-tree-modal-input").fill("财政局（已更名）");
    await page.getByTestId("organization-tree-modal-submit").click();

    await expect.poll(() => state.renameCalls).toBe(1);
    await expect(page.getByTestId("organization-tree-node-dept-001")).toContainText("财政局（已更名）");

    await page.getByTestId("organization-tree-import-button").click();
    await expect(page.getByTestId("organization-tree-importer")).toBeVisible();
    await page.getByTestId("organization-tree-import-file").setInputFiles({
      name: "organizations.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("department_name,unit_name\n导入部门,导入单位\n", "utf8"),
    });

    await expect.poll(() => state.importCalls).toBe(1);
    await expect(page.getByTestId("organization-tree-node-dept-imported")).toBeVisible();
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });
});
