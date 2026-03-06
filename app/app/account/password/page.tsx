"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

const MIN_PASSWORD_LENGTH = 6;

function parseError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = String((payload as Record<string, unknown>).detail ?? "").trim();
    if (detail) {
      return detail;
    }
  }
  if (payload && typeof payload === "object" && "message" in payload) {
    const message = String((payload as Record<string, unknown>).message ?? "").trim();
    if (message) {
      return message;
    }
  }
  return fallback;
}

export default function ChangePasswordPage() {
  const router = useRouter();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    const probeLogin = async () => {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (response.status === 401) {
          if (!cancelled) {
            router.replace("/login");
          }
          return;
        }
      } catch {
        if (!cancelled) {
          router.replace("/login");
          return;
        }
      }

      if (!cancelled) {
        setChecking(false);
      }
    };

    probeLogin();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setMessage("");

    if (!oldPassword || !newPassword || !confirmPassword) {
      setError("请完整填写旧密码、新密码、确认密码");
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`新密码长度不能少于 ${MIN_PASSWORD_LENGTH} 位`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(parseError(payload, "修改密码失败"));
        return;
      }

      setMessage("密码已更新，请重新登录");
      setTimeout(() => {
        window.location.assign("/login");
      }, 1200);
    } catch (e) {
      setError(e instanceof Error ? e.message : "修改密码失败");
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
    <div className="min-h-screen bg-slate-100 p-4 md:p-8">
      <div className="mx-auto w-full max-w-xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">修改密码</h1>
          <Link
            href="/"
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            返回主页
          </Link>
        </div>

        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block">
            <span className="mb-1 block text-sm text-slate-700">旧密码</span>
            <input
              type="password"
              value={oldPassword}
              onChange={(event) => setOldPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              autoComplete="current-password"
              disabled={loading}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm text-slate-700">新密码</span>
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              autoComplete="new-password"
              disabled={loading}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm text-slate-700">确认新密码</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              autoComplete="new-password"
              disabled={loading}
            />
          </label>

          {error ? (
            <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}
          {message ? (
            <div className="rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700">
              {message}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-500"
          >
            {loading ? "提交中..." : "确认修改"}
          </button>
        </form>
      </div>
    </div>
  );
}
