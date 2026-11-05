"use client";

import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { Badge, Button, Card, Td, Th } from "@/components/ui";

const FIELD_CLASS = "w-full rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-3 py-2";

type UserRecord = {
  username: string;
  is_admin: boolean;
  is_read_only?: boolean;
  is_active: boolean;
  organization_ids?: string[];
  created_at: number;
  updated_at: number;
};

type OrganizationRecord = {
  id: string;
  name: string;
  level: string;
  level_name?: string;
  parent_id?: string | null;
};

type AuthMeResponse = {
  user?: UserRecord;
  detail?: string;
};

type UserManagementPanelProps = {
  embedded?: boolean;
  showSummaryHeader?: boolean;
};

const MIN_PASSWORD_LENGTH = 6;

function formatTimestamp(ts: number) {
  if (!Number.isFinite(ts) || ts <= 0) {
    return "--";
  }
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function parseError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = String((payload as Record<string, unknown>).detail ?? "").trim();
    if (detail) {
      return detail;
    }
  }
  return fallback;
}

function normalizeOrganizationIds(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const result: string[] = [];
  const seen = new Set<string>();
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

function toggleOrganizationId(current: string[], orgId: string): string[] {
  if (current.includes(orgId)) {
    return current.filter((item) => item !== orgId);
  }
  return [...current, orgId];
}

export default function UserManagementPanel({
  embedded = false,
  showSummaryHeader = true,
}: UserManagementPanelProps) {
  const router = useRouter();
  const [me, setMe] = useState<UserRecord | null>(null);
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyUsername, setBusyUsername] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newUserAdmin, setNewUserAdmin] = useState(false);
  const [newUserReadOnly, setNewUserReadOnly] = useState(false);
  const [newUserOrganizationIds, setNewUserOrganizationIds] = useState<string[]>([]);
  const [scopeEditorUsername, setScopeEditorUsername] = useState<string | null>(null);
  const [scopeDraft, setScopeDraft] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const isAdmin = useMemo(() => Boolean(me?.is_admin), [me]);
  const organizationNameById = useMemo(
    () => new Map(organizations.map((org) => [org.id, org.name])),
    [organizations],
  );

  const describeOrganizationScope = useCallback(
    (ids: string[] | undefined) => {
      const names = normalizeOrganizationIds(ids).map(
        (orgId) => organizationNameById.get(orgId) ?? orgId,
      );
      if (!names.length) {
        return "仅本人任务";
      }
      if (names.length <= 2) {
        return names.join("、");
      }
      return `${names.slice(0, 2).join("、")} 等 ${names.length} 项`;
    },
    [organizationNameById],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const meResponse = await fetch("/api/auth/me", { cache: "no-store" });
      if (meResponse.status === 401) {
        router.replace("/login");
        return;
      }

      const mePayload = (await meResponse.json()) as AuthMeResponse;
      if (!meResponse.ok) {
        setError(parseError(mePayload, "无法获取当前登录用户"));
        return;
      }

      const currentUser = (mePayload.user ?? null) as UserRecord | null;
      setMe(currentUser);
      if (!currentUser?.is_admin) {
        setUsers([]);
        setOrganizations([]);
        return;
      }

      const [usersResponse, organizationsResponse] = await Promise.all([
        fetch("/api/users", { cache: "no-store" }),
        fetch("/api/organizations/list", { cache: "no-store" }),
      ]);

      const usersPayload = await usersResponse.json();
      if (!usersResponse.ok) {
        setError(parseError(usersPayload, "无法获取用户列表"));
        return;
      }

      const organizationsPayload = await organizationsResponse.json();
      if (!organizationsResponse.ok) {
        setError(parseError(organizationsPayload, "无法获取组织列表"));
        return;
      }

      const rows = Array.isArray(usersPayload?.users) ? (usersPayload.users as UserRecord[]) : [];
      setUsers(
        rows.map((row) => ({
          ...row,
          organization_ids: normalizeOrganizationIds(row.organization_ids),
        })),
      );
      setOrganizations(
        Array.isArray(organizationsPayload?.organizations)
          ? (organizationsPayload.organizations as OrganizationRecord[])
          : [],
      );
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "用户数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const updateUser = async (
    username: string,
    payload: {
      is_admin?: boolean;
      is_read_only?: boolean;
      is_active?: boolean;
      password?: string;
      organization_ids?: string[];
    },
    successText: string,
  ): Promise<boolean> => {
    setBusyUsername(username);
    setError("");
    setMessage("");

    try {
      const response = await fetch(`/api/users/${encodeURIComponent(username)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) {
        setError(parseError(body, "更新用户失败"));
        return false;
      }

      setMessage(successText);
      await loadData();
      return true;
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "更新用户失败");
      return false;
    } finally {
      setBusyUsername(null);
    }
  };

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const username = newUsername.trim();
    if (!username) {
      setError("请输入用户名");
      return;
    }
    if (!newPassword) {
      setError("请输入密码");
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`密码长度不能少于 ${MIN_PASSWORD_LENGTH} 位`);
      return;
    }

    setSubmitting(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username,
          password: newPassword,
          is_admin: newUserAdmin,
          is_read_only: newUserReadOnly,
          organization_ids: newUserAdmin ? [] : newUserOrganizationIds,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(parseError(payload, "新增用户失败"));
        return;
      }

      setNewUsername("");
      setNewPassword("");
      setNewUserAdmin(false);
      setNewUserReadOnly(false);
      setNewUserOrganizationIds([]);
      setMessage(`用户 ${payload.username} 已创建`);
      await loadData();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "新增用户失败");
    } finally {
      setSubmitting(false);
    }
  };

  const resetUserPassword = async (username: string) => {
    const nextPassword = window.prompt(`请为 ${username} 设置新密码：`, "");
    if (nextPassword === null) {
      return;
    }
    if (!nextPassword) {
      setError("密码不能为空");
      return;
    }
    if (nextPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`密码长度不能少于 ${MIN_PASSWORD_LENGTH} 位`);
      return;
    }

    await updateUser(username, { password: nextPassword }, `${username} 的密码已重置`);
  };

  const removeUser = async (username: string) => {
    if (!window.confirm(`确定要删除用户 ${username} 吗？`)) {
      return;
    }

    setBusyUsername(username);
    setError("");
    setMessage("");

    try {
      const response = await fetch(`/api/users/${encodeURIComponent(username)}`, {
        method: "DELETE",
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(parseError(payload, "删除用户失败"));
        return;
      }

      setMessage(`用户 ${username} 已删除`);
      await loadData();
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : "删除用户失败");
    } finally {
      setBusyUsername(null);
    }
  };

  const openScopeEditor = (user: UserRecord) => {
    setScopeEditorUsername(user.username);
    setScopeDraft(normalizeOrganizationIds(user.organization_ids));
  };

  const saveScopeEditor = async (username: string) => {
    const saved = await updateUser(
      username,
      { organization_ids: scopeDraft },
      `${username} 的组织范围已更新`,
    );
    if (saved) {
      setScopeEditorUsername(null);
      setScopeDraft([]);
    }
  };

  const renderOrganizationPicker = (
    selectedIds: string[],
    onChange: (nextIds: string[]) => void,
    disabled = false,
    testIdPrefix = "admin-users-org",
  ) => (
    <div className="max-h-44 overflow-y-auto rounded-md border border-border bg-white">
      {organizations.length ? (
        organizations.map((org) => {
          const checked = selectedIds.includes(org.id);
          return (
            <label
              key={org.id}
              className="flex items-start gap-2 border-b border-slate-100 px-3 py-2 last:border-b-0"
            >
              <input
                type="checkbox"
                data-testid={`${testIdPrefix}-${org.id}`}
                checked={checked}
                disabled={disabled}
                onChange={() => onChange(toggleOrganizationId(selectedIds, org.id))}
                className="mt-1"
              />
              <span className="min-w-0">
                <span className="block truncate text-sm text-slate-800">{org.name}</span>
                <span className="text-xs text-slate-500">{org.level_name || org.level}</span>
              </span>
            </label>
          );
        })
      ) : (
        <div className="px-3 py-2 text-sm text-slate-500">暂无可选组织</div>
      )}
    </div>
  );

  if (loading) {
    return embedded ? (
      <div className="rounded-card border border-border bg-white p-6 text-sm text-slate-600 shadow-soft">
        正在加载用户管理...
      </div>
    ) : (
      <div className="flex min-h-[320px] items-center justify-center rounded-card border border-border bg-white p-8 shadow-soft">
        <div className="text-slate-600">正在加载用户管理...</div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="rounded-card border border-border bg-white p-8 shadow-soft">
        <h2 className="text-xl font-semibold text-slate-900">无访问权限</h2>
        <p className="mt-2 text-sm text-slate-600">当前账号不是管理员，无法访问用户管理功能。</p>
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="admin-users-panel">
      {showSummaryHeader ? (
        <Card>
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">用户与权限</h2>
              <p className="mt-1 text-xs text-slate-500">
                当前管理员：{me?.username || "admin"}，共 {users.length} 个账号
              </p>
            </div>
            <Badge>支持组织范围、启停、重置密码和删除</Badge>
          </div>
        </Card>
      ) : null}

      {/* 分栏按视口断点判断：两层侧栏占去约 530px，xl 下表格只剩不到 300px，所以到 2xl 才并排。 */}
      <div className="grid gap-5 2xl:grid-cols-[360px_minmax(0,1fr)] 2xl:items-start">
        <Card title="添加用户" desc="创建系统账号，并按需授予管理员或组织访问范围。">
          <form className="grid gap-3 md:grid-cols-2 2xl:grid-cols-1" onSubmit={createUser}>
            <input
              data-testid="admin-users-new-username"
              value={newUsername}
              onChange={(event) => setNewUsername(event.target.value)}
              placeholder="用户名"
              className={FIELD_CLASS}
              disabled={submitting}
              autoComplete="username"
            />
            <input
              data-testid="admin-users-new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              type="password"
              placeholder={`密码（至少 ${MIN_PASSWORD_LENGTH} 位）`}
              className={FIELD_CLASS}
              disabled={submitting}
              autoComplete="new-password"
            />
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                data-testid="admin-users-new-admin"
                checked={newUserAdmin}
                onChange={(event) => { setNewUserAdmin(event.target.checked); if (event.target.checked) setNewUserReadOnly(false); }}
                disabled={submitting}
              />
              设为管理员
            </label>
            {!newUserAdmin ? <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" data-testid="admin-users-new-readonly" checked={newUserReadOnly} onChange={event => setNewUserReadOnly(event.target.checked)} disabled={submitting} />主管只读账号（仅查看授权范围）</label> : null}
            {!newUserAdmin ? (
              <div className="space-y-2 md:col-span-2 2xl:col-span-1">
                <div className="text-sm font-medium text-slate-700">可访问组织范围</div>
                {renderOrganizationPicker(
                  newUserOrganizationIds,
                  setNewUserOrganizationIds,
                  submitting,
                  "admin-users-new-org",
                )}
                <div className="text-xs text-slate-500">
                  {newUserReadOnly ? "主管账号必须选择组织范围，不能上传、修改问题或完成复核。" : "未选择时仅能访问本人上传的任务。"}
                </div>
              </div>
            ) : (
              <div className="rounded-md bg-surface-100 px-3 py-2 text-xs text-slate-600 md:col-span-2 2xl:col-span-1">
                管理员默认可访问全部组织。
              </div>
            )}
            <div className="md:col-span-2 2xl:col-span-1">
              <Button
                type="submit"
                data-testid="admin-users-create"
                disabled={submitting}
                className="w-full md:w-auto 2xl:w-full"
              >
                {submitting ? "创建中..." : "创建用户"}
              </Button>
            </div>
          </form>
        </Card>

        <div className="min-w-0 space-y-4">
          {error ? (
            <div data-testid="admin-users-error" className="rounded-card border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
              {error}
            </div>
          ) : null}
          {message ? (
            <div data-testid="admin-users-message" className="rounded-card border border-success-200 bg-success-50 px-4 py-3 text-sm text-success-700">
              {message}
            </div>
          ) : null}

          <Card title="用户列表" bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr>
                    <Th className="px-3">用户名</Th>
                    <Th className="px-3">角色</Th>
                    <Th className="px-3">状态</Th>
                    <Th className="px-3">组织范围</Th>
                    <Th className="px-3">创建时间</Th>
                    <Th className="px-3">操作</Th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((item) => {
                    const isBusy = busyUsername === item.username;
                    const isCurrent = item.username === me?.username;
                    const editingScope = scopeEditorUsername === item.username;

                    return (
                      <tr key={item.username} data-testid={`admin-users-row-${item.username}`}>
                        <Td className="px-3 text-slate-800">{item.username}</Td>
                        <Td className="px-3">
                          <Badge tone={item.is_admin ? "processing" : "neutral"} className="whitespace-nowrap">
                            {item.is_read_only ? "主管只读" : item.is_admin ? "管理员" : "审核工作人员"}
                          </Badge>
                        </Td>
                        <Td className="px-3">
                          <Badge tone={item.is_active ? "done" : "review"} className="whitespace-nowrap">
                            {item.is_active ? "已启用" : "已禁用"}
                          </Badge>
                        </Td>
                        <Td className="min-w-36 px-3 text-slate-600">
                          {item.is_admin ? (
                            <span className="text-sm text-slate-700">全部组织</span>
                          ) : (
                            <div className="space-y-2">
                              <div className="text-sm">{describeOrganizationScope(item.organization_ids)}</div>
                              {editingScope ? (
                                <div className="w-72 rounded-md border border-border bg-surface-100 p-3">
                                  {renderOrganizationPicker(scopeDraft, setScopeDraft, isBusy, `admin-users-scope-org-${item.username}`)}
                                  <div className="mt-3 flex gap-2">
                                    <Button
                                      size="sm"
                                      data-testid={`admin-users-scope-save-${item.username}`}
                                      disabled={isBusy}
                                      onClick={() => saveScopeEditor(item.username)}
                                    >
                                      保存范围
                                    </Button>
                                    <Button
                                      variant="secondary"
                                      size="sm"
                                      data-testid={`admin-users-scope-cancel-${item.username}`}
                                      disabled={isBusy}
                                      onClick={() => {
                                        setScopeEditorUsername(null);
                                        setScopeDraft([]);
                                      }}
                                    >
                                      取消
                                    </Button>
                                  </div>
                                </div>
                              ) : null}
                            </div>
                          )}
                        </Td>
                        <Td className="px-3 text-slate-600">{formatTimestamp(item.created_at)}</Td>
                        <Td className="min-w-[220px] px-3">
                          <div className="flex flex-wrap gap-2">
                            <Button
                              variant="secondary"
                              size="sm"
                              className="whitespace-nowrap"
                              data-testid={`admin-users-role-${item.username}`}
                              disabled={isBusy}
                              onClick={() => updateUser(item.username, { is_admin: !item.is_admin, ...(item.is_read_only ? { is_read_only: false } : {}) }, `${item.username} 的角色已更新`)}
                            >
                              {item.is_admin ? "取消管理员" : "设为管理员"}
                            </Button>
                            {!item.is_admin ? <Button variant="secondary" size="sm" className="whitespace-nowrap" disabled={isBusy} data-testid={`admin-users-readonly-${item.username}`} onClick={() => updateUser(item.username, { is_read_only: !item.is_read_only }, `${item.username} 的只读权限已更新，请重新登录`)}>{item.is_read_only ? "改为工作人员" : "设为主管只读"}</Button> : null}
                            <Button
                              variant="secondary"
                              size="sm"
                              className="whitespace-nowrap"
                              data-testid={`admin-users-active-${item.username}`}
                              disabled={isBusy}
                              onClick={() => updateUser(item.username, { is_active: !item.is_active }, `${item.username} 的状态已更新`)}
                            >
                              {item.is_active ? "禁用" : "启用"}
                            </Button>
                            {!item.is_admin ? (
                              <Button
                                variant="secondary"
                                size="sm"
                                className="whitespace-nowrap"
                                data-testid={`admin-users-scope-open-${item.username}`}
                                disabled={isBusy}
                                onClick={() => openScopeEditor(item)}
                              >
                                设置范围
                              </Button>
                            ) : null}
                            <Button
                              variant="secondary"
                              size="sm"
                              className="whitespace-nowrap"
                              data-testid={`admin-users-password-${item.username}`}
                              disabled={isBusy}
                              onClick={() => resetUserPassword(item.username)}
                            >
                              重置密码
                            </Button>
                            <Button
                              variant="secondary"
                              size="sm"
                              data-testid={`admin-users-delete-${item.username}`}
                              disabled={isBusy || isCurrent}
                              onClick={() => removeUser(item.username)}
                              className="whitespace-nowrap border-danger-200 text-danger-700 hover:bg-danger-50"
                              title={isCurrent ? "不能删除当前登录用户" : ""}
                            >
                              删除
                            </Button>
                          </div>
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
