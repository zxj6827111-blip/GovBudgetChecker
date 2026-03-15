"use client";

import Link from "next/link";

import UserManagementPanel from "@/components/admin/UserManagementPanel";

export default function AdminUsersPage() {
  return (
    <div className="min-h-screen bg-slate-100 p-4 md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-slate-900">用户管理</h1>
              <p className="mt-1 text-sm text-slate-600">
                支持新增用户、调整权限、启停账号、重置密码和删除账号。
              </p>
            </div>
            <Link
              href="/admin"
              className="inline-flex rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50"
            >
              返回系统管理
            </Link>
          </div>
        </div>

        <UserManagementPanel embedded showSummaryHeader={false} />
      </div>
    </div>
  );
}
