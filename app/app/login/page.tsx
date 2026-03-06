"use client";

import { FormEvent, useEffect, useState } from "react";

function normalizeNextPath(rawPath: string | null): string {
  if (!rawPath || !rawPath.startsWith("/")) {
    return "/";
  }
  if (rawPath.startsWith("/login")) {
    return "/";
  }
  return rawPath;
}

export default function LoginPage() {
  const [nextPath, setNextPath] = useState("/");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setNextPath(normalizeNextPath(params.get("next")));
  }, []);

  useEffect(() => {
    let cancelled = false;

    const probeLogin = async () => {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!cancelled && response.ok) {
          window.location.assign(nextPath);
          return;
        }
      } catch {
        // Ignore pre-check errors and let user log in manually.
      }

      if (!cancelled) {
        setChecking(false);
      }
    };

    probeLogin();
    return () => {
      cancelled = true;
    };
  }, [nextPath]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedUsername = username.trim();

    if (!trimmedUsername) {
      setError("请输入用户名");
      return;
    }
    if (!password) {
      setError("请输入密码");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: trimmedUsername, password }),
      });

      let payload: any = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }

      if (!response.ok) {
        setError(String(payload?.detail || "登录失败"));
        return;
      }

      window.location.assign(nextPath);
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100">
        <div className="text-slate-600">正在检查登录状态...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-xl">
        <h1 className="text-2xl font-semibold text-slate-900">GovBudgetChecker 登录</h1>
        <p className="mt-2 text-sm text-slate-600">请输入用户名和密码进入系统</p>

        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <label className="block">
            <span className="mb-1 block text-sm text-slate-700">用户名</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              placeholder="例如：admin"
              autoComplete="username"
              disabled={loading}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm text-slate-700">密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              placeholder="请输入密码"
              autoComplete="current-password"
              disabled={loading}
            />
          </label>

          {error ? (
            <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
