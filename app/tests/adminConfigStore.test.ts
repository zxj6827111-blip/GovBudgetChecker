import assert from "node:assert/strict";
import Module from "node:module";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

type ModuleInternals = typeof Module & {
  _initPaths: () => void;
  _resolveFilename: (
    request: string,
    parent?: unknown,
    isMain?: boolean,
    options?: unknown,
  ) => string;
};

async function main() {
  const tempDir = await mkdtemp(join(tmpdir(), "gbc-admin-config-"));
  const configPath = join(tempDir, "admin_config.json");
  process.env.GBC_ADMIN_CONFIG_PATH = configPath;
  process.env.NODE_PATH = join(process.cwd(), "node_modules", "next", "dist", "compiled");
  const moduleInternals = Module as ModuleInternals;
  moduleInternals._initPaths();
  const originalResolveFilename = moduleInternals._resolveFilename;
  moduleInternals._resolveFilename = function resolveWithServerOnlyMock(
    request,
    parent,
    isMain,
    options,
  ) {
    if (request === "server-only") {
      return join(process.cwd(), "node_modules", "next", "dist", "compiled", "server-only", "empty.js");
    }
    return originalResolveFilename.call(this, request, parent, isMain, options);
  };

  const {
    createAdminConfigItem,
    deleteAdminConfigItem,
    getAdminConfigItem,
    listAdminConfigItems,
    updateAdminConfigItem,
  } = await import("../lib/adminConfigStore");

  try {
    const created = await createAdminConfigItem("rule-packages", {
      name: "v3_3_portable",
      description: "默认预决算公开规则包",
      enabled: true,
      data: {
        version: "v3_3_portable",
        severities: ["critical", "high", "medium", "low", "manual_review"],
      },
    }, "admin");

    assert.ok(created.id, "created item should get an id");
    assert.equal(created.updated_by, "admin");
    assert.equal(created.created_by, "admin");
    assert.equal(created.enabled, true);
    assert.equal(created.data.version, "v3_3_portable");

    const listed = await listAdminConfigItems("rule-packages");
    assert.equal(listed.length, 1, "created item should be listed");
    assert.equal(listed[0].name, "v3_3_portable");

    const updated = await updateAdminConfigItem("rule-packages", created.id, {
      enabled: false,
      description: "第一版只沉淀配置，不改变运行链路",
      data: {
        version: "v3_3_portable",
        runtime_effect: false,
      },
    }, "ops");

    assert.equal(updated?.enabled, false);
    assert.equal(updated?.updated_by, "ops");
    assert.equal(updated?.data.runtime_effect, false);

    const fetched = await getAdminConfigItem("rule-packages", created.id);
    assert.equal(fetched?.description, "第一版只沉淀配置，不改变运行链路");

    const removed = await deleteAdminConfigItem("rule-packages", created.id, "ops");
    assert.equal(removed, true);
    assert.equal((await listAdminConfigItems("rule-packages")).length, 0);

    console.log("adminConfigStore CRUD and audit tests passed");
  } finally {
    moduleInternals._resolveFilename = originalResolveFilename;
    await rm(configPath, { force: true });
    await rm(tempDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
