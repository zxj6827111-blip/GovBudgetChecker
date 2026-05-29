import "server-only";

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

export type AdminConfigCollection =
  | "rule-packages"
  | "material-mappings"
  | "system-settings"
  | "export-templates";

export type AdminConfigItem = {
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

export type AdminConfigAuditEntry = {
  id: string;
  action: "create" | "update" | "delete";
  collection: AdminConfigCollection;
  item_id: string;
  actor: string;
  at: string;
};

export type AdminConfigState = {
  rule_packages: AdminConfigItem[];
  material_mappings: AdminConfigItem[];
  system_settings: AdminConfigItem[];
  export_templates: AdminConfigItem[];
  audit_log: AdminConfigAuditEntry[];
};

const COLLECTION_KEYS: Record<AdminConfigCollection, keyof AdminConfigState> = {
  "rule-packages": "rule_packages",
  "material-mappings": "material_mappings",
  "system-settings": "system_settings",
  "export-templates": "export_templates",
};

const DEFAULT_CONFIG: AdminConfigState = {
  rule_packages: [],
  material_mappings: [],
  system_settings: [],
  export_templates: [],
  audit_log: [],
};

const CONFIG_FILE = resolve(
  process.env.GBC_ADMIN_CONFIG_PATH?.trim() || resolve(process.cwd(), "..", "data", "admin_config.json"),
);
const MAX_AUDIT_ENTRIES = 300;

function cloneDefaultState(): AdminConfigState {
  return {
    rule_packages: [],
    material_mappings: [],
    system_settings: [],
    export_templates: [],
    audit_log: [],
  };
}

function normalizeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function normalizeItem(value: unknown): AdminConfigItem | null {
  const record = normalizeRecord(value);
  const id = String(record.id ?? "").trim();
  const name = String(record.name ?? "").trim();
  if (!id || !name) {
    return null;
  }
  const now = new Date().toISOString();
  return {
    id,
    name,
    enabled: typeof record.enabled === "boolean" ? record.enabled : true,
    description: String(record.description ?? ""),
    updated_at: String(record.updated_at ?? now),
    updated_by: String(record.updated_by ?? "system"),
    created_at: String(record.created_at ?? record.updated_at ?? now),
    created_by: String(record.created_by ?? record.updated_by ?? "system"),
    data: normalizeRecord(record.data),
  };
}

function normalizeState(value: unknown): AdminConfigState {
  const record = normalizeRecord(value);
  const state = cloneDefaultState();
  for (const [, key] of Object.entries(COLLECTION_KEYS) as Array<[AdminConfigCollection, keyof AdminConfigState]>) {
    const rawItems = record[key];
    const items: unknown[] = Array.isArray(rawItems) ? rawItems : [];
    state[key] = items
      .map(normalizeItem)
      .filter((item): item is AdminConfigItem => item !== null) as never;
  }
  state.audit_log = Array.isArray(record.audit_log)
    ? record.audit_log
        .map((entry) => {
          const item = normalizeRecord(entry);
          const id = String(item.id ?? "").trim();
          const collection = String(item.collection ?? "") as AdminConfigCollection;
          if (!id || !(collection in COLLECTION_KEYS)) {
            return null;
          }
          return {
            id,
            action: String(item.action ?? "update") as AdminConfigAuditEntry["action"],
            collection,
            item_id: String(item.item_id ?? ""),
            actor: String(item.actor ?? "system"),
            at: String(item.at ?? new Date().toISOString()),
          };
        })
        .filter((entry): entry is AdminConfigAuditEntry => entry !== null)
        .slice(-MAX_AUDIT_ENTRIES)
    : [];
  return state;
}

async function readState(): Promise<AdminConfigState> {
  try {
    const text = await readFile(CONFIG_FILE, "utf8");
    return normalizeState(JSON.parse(text));
  } catch {
    return { ...DEFAULT_CONFIG, ...cloneDefaultState() };
  }
}

async function writeState(state: AdminConfigState): Promise<void> {
  await mkdir(dirname(CONFIG_FILE), { recursive: true });
  const tmpPath = `${CONFIG_FILE}.${Date.now()}.tmp`;
  await writeFile(tmpPath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await rename(tmpPath, CONFIG_FILE);
}

function collectionKey(collection: AdminConfigCollection): keyof AdminConfigState {
  return COLLECTION_KEYS[collection];
}

function makeId(collection: AdminConfigCollection, name: string): string {
  const slug =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "item";
  return `${collection}-${slug}-${Date.now().toString(36)}`;
}

function makeAuditEntry(
  action: AdminConfigAuditEntry["action"],
  collection: AdminConfigCollection,
  itemId: string,
  actor: string,
): AdminConfigAuditEntry {
  return {
    id: `audit-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    action,
    collection,
    item_id: itemId,
    actor,
    at: new Date().toISOString(),
  };
}

function parseItemInput(input: unknown): Pick<AdminConfigItem, "name" | "enabled" | "description" | "data"> {
  const record = normalizeRecord(input);
  const name = String(record.name ?? "").trim();
  if (!name) {
    throw new Error("name is required");
  }
  return {
    name,
    enabled: typeof record.enabled === "boolean" ? record.enabled : true,
    description: String(record.description ?? ""),
    data: normalizeRecord(record.data),
  };
}

export async function listAdminConfigItems(collection: AdminConfigCollection): Promise<AdminConfigItem[]> {
  const state = await readState();
  return [...(state[collectionKey(collection)] as AdminConfigItem[])];
}

export async function getAdminConfigItem(
  collection: AdminConfigCollection,
  itemId: string,
): Promise<AdminConfigItem | null> {
  const state = await readState();
  return (state[collectionKey(collection)] as AdminConfigItem[]).find((item) => item.id === itemId) ?? null;
}

export async function createAdminConfigItem(
  collection: AdminConfigCollection,
  input: unknown,
  actor: string,
): Promise<AdminConfigItem> {
  const state = await readState();
  const key = collectionKey(collection);
  const payload = parseItemInput(input);
  const now = new Date().toISOString();
  const item: AdminConfigItem = {
    id: makeId(collection, payload.name),
    ...payload,
    created_at: now,
    created_by: actor,
    updated_at: now,
    updated_by: actor,
  };
  (state[key] as AdminConfigItem[]).unshift(item);
  state.audit_log = [...state.audit_log, makeAuditEntry("create", collection, item.id, actor)].slice(-MAX_AUDIT_ENTRIES);
  await writeState(state);
  return item;
}

export async function updateAdminConfigItem(
  collection: AdminConfigCollection,
  itemId: string,
  input: unknown,
  actor: string,
): Promise<AdminConfigItem | null> {
  const state = await readState();
  const key = collectionKey(collection);
  const items = state[key] as AdminConfigItem[];
  const index = items.findIndex((item) => item.id === itemId);
  if (index < 0) {
    return null;
  }
  const record = normalizeRecord(input);
  const next: AdminConfigItem = {
    ...items[index],
    name: typeof record.name === "string" && record.name.trim() ? record.name.trim() : items[index].name,
    enabled: typeof record.enabled === "boolean" ? record.enabled : items[index].enabled,
    description: typeof record.description === "string" ? record.description : items[index].description,
    data: "data" in record ? normalizeRecord(record.data) : items[index].data,
    updated_at: new Date().toISOString(),
    updated_by: actor,
  };
  items[index] = next;
  state.audit_log = [...state.audit_log, makeAuditEntry("update", collection, next.id, actor)].slice(-MAX_AUDIT_ENTRIES);
  await writeState(state);
  return next;
}

export async function deleteAdminConfigItem(
  collection: AdminConfigCollection,
  itemId: string,
  actor: string,
): Promise<boolean> {
  const state = await readState();
  const key = collectionKey(collection);
  const items = state[key] as AdminConfigItem[];
  const nextItems = items.filter((item) => item.id !== itemId);
  if (nextItems.length === items.length) {
    return false;
  }
  state[key] = nextItems as never;
  state.audit_log = [...state.audit_log, makeAuditEntry("delete", collection, itemId, actor)].slice(-MAX_AUDIT_ENTRIES);
  await writeState(state);
  return true;
}

export function isAdminConfigCollection(value: string): value is AdminConfigCollection {
  return value in COLLECTION_KEYS;
}
