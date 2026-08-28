"use client";

/**
 * 登录页（修复 C：纳入设计系统）。
 *
 * 此前登录页的蓝色不来自任何色号类名，而是硬编码在 className 任意值里的
 * RGB 渐变（rgba(59,130,246) = Tailwind blue-500），既躲过 blue-* 类名扫描，
 * 也不受 primary 令牌控制。本文件现在只用语义令牌：
 * - 背景：surface-50→surface-100 渐变 + primary-100 顶部柔光；
 * - Logo 色块：brand-900（UI_COLOR_TOKEN_MAPPING.md 的 Logo 取色）；
 * - 主按钮：primary-600/700（与新 UI 主按钮同源）；
 * - 错误提示：danger-*；卡片阴影：shadow-float（令牌阴影，替代硬编码 rgba）。
 * 防回归由 app/tests/hardcodedColorGuard.test.ts 把关。
 */

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
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch("/api/auth/me", {
          cache: "no-store",
          headers: { "X-Login-Probe": "1" },
          signal: controller.signal,
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
      } finally {
        window.clearTimeout(timeoutId);
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
      <div className="grid min-h-[100dvh] place-items-center bg-surface-100 px-4">
        <div className="text-slate-600">正在检查登录状态...</div>
      </div>
    );
  }

  return (
    <div className="relative min-h-[100dvh] overflow-hidden bg-gradient-to-b from-surface-50 to-surface-100 px-4 py-8">
      {/* 顶部柔光：以 primary-100 令牌叠出（替代原先硬编码 rgba(59,130,246) 的
          radial-gradient——那是不受令牌控制的 Tailwind blue-500，登录页因此
          游离在设计系统之外）。 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[44vh] overflow-hidden"
      >
        <div className="absolute left-1/2 top-[-55%] h-[75vh] w-[130vw] -translate-x-1/2 rounded-[100%] bg-primary-100/60 blur-2xl" />
      </div>

      <div className="relative grid min-h-[calc(100dvh-4rem)] place-items-center">
        <div className="w-full max-w-[460px]">
          <div className="mb-6 flex items-center justify-center gap-3 text-slate-900">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-900 text-sm font-bold text-white shadow-lg">
              GC
            </div>
            <div>
              <div className="text-lg font-semibold tracking-tight">GovBudgetChecker</div>
              <div className="text-sm text-slate-500">政府预算审校平台</div>
            </div>
          </div>

          <div className="rounded-[28px] border border-white/70 bg-white/90 p-8 shadow-float backdrop-blur">
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
                  className="w-full rounded-xl border border-slate-300 px-3 py-3 outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
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
                  className="w-full rounded-xl border border-slate-300 px-3 py-3 outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
                  placeholder="请输入密码"
                  autoComplete="current-password"
                  disabled={loading}
                />
              </label>

              {error ? (
                <div className="rounded-xl border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                  {error}
                </div>
              ) : null}

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-primary-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-primary-300"
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
