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
  const [nextPathReady, setNextPathReady] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setNextPath(normalizeNextPath(params.get("next")));
    setNextPathReady(true);
  }, []);

  useEffect(() => {
    if (!nextPathReady) {
      return;
    }

    let cancelled = false;

    const probeLogin = async () => {
      try {
        const response = await fetch("/api/auth/me", {
          cache: "no-store",
          headers: { "X-Login-Probe": "1" },
        });
        const payload = (await response.json().catch(() => ({}))) as {
          user?: Record<string, unknown> | null;
        };

        if (!cancelled && response.ok && payload.user) {
          window.location.assign(nextPath);
          return;
        }
      } catch {
        // Ignore pre-check errors and let the user log in manually.
      }

      if (!cancelled) {
        setChecking(false);
      }
    };

    probeLogin();
    return () => {
      cancelled = true;
    };
  }, [nextPath, nextPathReady]);

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

      let payload: Record<string, unknown> = {};
      try {
        payload = (await response.json()) as Record<string, unknown>;
      } catch {
        payload = {};
      }

      if (!response.ok) {
        setError(String(payload.detail ?? "登录失败"));
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
      <div className="grid min-h-[100dvh] place-items-center bg-slate-100 px-4">
        <div className="text-slate-600">正在检查登录状态...</div>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.16),_transparent_38%),linear-gradient(180deg,_#f8fafc_0%,_#e2e8f0_100%)] px-4 py-8">
      <div className="grid min-h-[calc(100dvh-4rem)] place-items-center">
        <div className="w-full max-w-[460px]">
          <div className="mb-6 flex items-center justify-center gap-3 text-slate-900">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-900 text-sm font-bold text-white shadow-lg">
              GC
            </div>
            <div>
              <div className="text-lg font-semibold tracking-tight">GovBudgetChecker</div>
              <div className="text-sm text-slate-500">政府预算审校平台</div>
            </div>
          </div>

          <div className="rounded-[28px] border border-white/70 bg-white/92 p-8 shadow-[0_20px_60px_rgba(15,23,42,0.18)] backdrop-blur">
            <h1 className="text-2xl font-semibold text-slate-900">登录系统</h1>
            <p className="mt-2 text-sm text-slate-600">
              请输入用户名和密码进入当前审校工作台。
            </p>

            <form className="mt-6 space-y-4" onSubmit={onSubmit}>
              <label className="block">
                <span className="mb-1.5 block text-sm text-slate-700">用户名</span>
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  className="w-full rounded-xl border border-slate-300 px-3 py-3 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                  placeholder="例如：admin"
                  autoComplete="username"
                  disabled={loading}
                />
              </label>

              <label className="block">
                <span className="mb-1.5 block text-sm text-slate-700">密码</span>
                <input
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  className="w-full rounded-xl border border-slate-300 px-3 py-3 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                  placeholder="请输入密码"
                  autoComplete="current-password"
                  disabled={loading}
                />
              </label>

              {error ? (
                <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </div>
              ) : null}

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-slate-900 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
              >
                {loading ? "登录中..." : "登录"}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
