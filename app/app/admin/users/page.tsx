"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

type UserRecord = {
  username: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: number;
  updated_at: number;
};

type AuthMeResponse = {
  user?: UserRecord;
  detail?: string;
};

const MIN_PASSWORD_LENGTH = 6;

function formatTimestamp(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) {
    return "-";
  }
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function parseError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = String((payload as Record<string, unknown>).detail ?? "").trim();
    if (detail) {
      return detail;
    }
  }
  return fallback;
}

export default function AdminUsersPage() {
  const router = useRouter();
  const [me, setMe] = useState<UserRecord | null>(null);
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyUsername, setBusyUsername] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newUserAdmin, setNewUserAdmin] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const isAdmin = useMemo(() => Boolean(me?.is_admin), [me]);

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
        return;
      }

      const usersResponse = await fetch("/api/users", { cache: "no-store" });
      const usersPayload = await usersResponse.json();
      if (!usersResponse.ok) {
        setError(parseError(usersPayload, "无法获取用户列表"));
        return;
      }

      const rows = Array.isArray(usersPayload?.users)
        ? (usersPayload.users as UserRecord[])
        : [];
      setUsers(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "用户数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    loadData();
  }, [loadData]);

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
      setMessage(`用户 ${payload.username} 已创建`);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增用户失败");
    } finally {
      setSubmitting(false);
    }
  };

  const updateUser = async (
    username: string,
    payload: { is_admin?: boolean; is_active?: boolean; password?: string },
    successText: string
  ) => {
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
        return;
      }
      setMessage(successText);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "更新用户失败");
    } finally {
      setBusyUsername(null);
    }
  };

  const resetUserPassword = async (username: string) => {
    const newPasswordInput = window.prompt(`为 ${username} 设置新密码：`, "");
    if (newPasswordInput === null) {
      return;
    }
    if (!newPasswordInput) {
      setError("密码不能为空");
      return;
    }
    if (newPasswordInput.length < MIN_PASSWORD_LENGTH) {
      setError(`密码长度不能少于 ${MIN_PASSWORD_LENGTH} 位`);
      return;
    }
    await updateUser(
      username,
      { password: newPasswordInput },
      `${username} 密码已重置`
    );
  };

  const removeUser = async (username: string) => {
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
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除用户失败");
    } finally {
      setBusyUsername(null);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100">
        <div className="text-slate-600">正在加载用户管理...</div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100 p-4">
        <div className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white p-8 shadow-lg">
          <h1 className="text-xl font-semibold text-slate-900">无访问权限</h1>
          <p className="mt-2 text-sm text-slate-600">
            当前账号不是管理员，不能访问用户管理页。
          </p>
          <Link
            href="/"
            className="mt-6 inline-flex rounded-lg bg-slate-900 px-4 py-2 text-sm text-white"
          >
            返回系统主页
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-100 p-4 md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-slate-900">用户管理</h1>
              <p className="mt-1 text-sm text-slate-600">
                当前管理员：{me?.username || "admin"}
              </p>
            </div>
            <Link
              href="/"
              className="inline-flex rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
            >
              返回主页
            </Link>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-medium text-slate-900">添加用户</h2>
          <form className="mt-4 grid gap-3 md:grid-cols-4" onSubmit={createUser}>
            <input
              value={newUsername}
              onChange={(event) => setNewUsername(event.target.value)}
              placeholder="用户名"
              className="rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              disabled={submitting}
            />
            <input
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              type="password"
              placeholder={`密码（至少 ${MIN_PASSWORD_LENGTH} 位）`}
              className="rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              disabled={submitting}
              autoComplete="new-password"
            />
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={newUserAdmin}
                onChange={(event) => setNewUserAdmin(event.target.checked)}
                disabled={submitting}
              />
              设为管理员
            </label>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-500"
            >
              {submitting ? "创建中..." : "创建用户"}
            </button>
          </form>
        </div>

        {error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        {message ? (
          <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
            {message}
          </div>
        ) : null}

        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">用户名</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">角色</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">状态</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">创建时间</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.map((item) => {
                  const isBusy = busyUsername === item.username;
                  const isCurrent = item.username === me?.username;
                  return (
                    <tr key={item.username}>
                      <td className="px-4 py-3 text-slate-800">{item.username}</td>
                      <td className="px-4 py-3">
                        {item.is_admin ? (
                          <span className="rounded-full bg-indigo-100 px-2 py-1 text-xs text-indigo-700">
                            admin
                          </span>
                        ) : (
                          <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-700">
                            user
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {item.is_active ? (
                          <span className="rounded-full bg-green-100 px-2 py-1 text-xs text-green-700">
                            已启用
                          </span>
                        ) : (
                          <span className="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-700">
                            已禁用
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {formatTimestamp(item.created_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() =>
                              updateUser(
                                item.username,
                                { is_admin: !item.is_admin },
                                `${item.username} 角色已更新`
                              )
                            }
                            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 disabled:opacity-60"
                          >
                            {item.is_admin ? "取消管理员" : "设为管理员"}
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() =>
                              updateUser(
                                item.username,
                                { is_active: !item.is_active },
                                `${item.username} 状态已更新`
                              )
                            }
                            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 disabled:opacity-60"
                          >
                            {item.is_active ? "禁用" : "启用"}
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => resetUserPassword(item.username)}
                            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 disabled:opacity-60"
                          >
                            重置密码
                          </button>
                          <button
                            type="button"
                            disabled={isBusy || isCurrent}
                            onClick={() => removeUser(item.username)}
                            className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 disabled:opacity-60"
                            title={isCurrent ? "不能删除当前登录用户" : ""}
                          >
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
