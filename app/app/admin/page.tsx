"use client";

import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileText,
  RefreshCw,
  Settings,
  Trash2,
  UploadCloud,
  Users,
} from "lucide-react";

import BatchUploadModal from "@/components/BatchUploadModal";
import AnalysisResultsPanel from "@/components/admin/AnalysisResultsPanel";
import UserManagementPanel from "@/components/admin/UserManagementPanel";
import { cn } from "@/lib/utils";

type ActionState = {
  id: string;
  status: "loading" | "success" | null;
};

const tabs = [
  { id: "analysis", label: "分析结果", icon: FileText },
  { id: "operations", label: "数据与运行", icon: Database },
  { id: "users", label: "用户管理", icon: Users },
  { id: "organization", label: "组织架构", icon: Users },
  { id: "system", label: "系统设置", icon: Settings },
] as const;

export default function AdminPage() {
  const [activeTab, setActiveTab] =
    useState<(typeof tabs)[number]["id"]>("users");
  const [actionStatus, setActionStatus] = useState<ActionState>({
    id: "",
    status: null,
  });
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const handleAction = (id: string) => {
    setActionStatus({ id, status: "loading" });
    setTimeout(() => {
      setActionStatus({ id, status: "success" });
      setTimeout(() => setActionStatus({ id: "", status: null }), 3000);
    }, 1500);
  };

  return (
    <div className="flex h-full bg-surface-50">
      <div className="flex w-64 shrink-0 flex-col border-r border-border bg-white">
        <div className="border-b border-border p-6">
          <h1 className="text-xl font-bold tracking-tight text-slate-900">系统管理</h1>
          <p className="mt-1 text-sm text-slate-500">管理员专属工具箱</p>
        </div>
        <nav className="space-y-1 p-4">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary-50 text-primary-700"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )}
              >
                <Icon
                  className={cn("h-5 w-5", isActive ? "text-primary-600" : "text-slate-400")}
                />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto p-8">
        <div className="mx-auto max-w-5xl">
          {activeTab === "operations" ? (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-300">
              <section>
                <h2 className="mb-4 text-lg font-bold text-slate-900">全局数据接入</h2>
                <div className="flex items-start justify-between rounded-xl border border-border bg-white p-6 shadow-sm">
                  <div className="flex gap-4">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary-50">
                      <UploadCloud className="h-6 w-6 text-primary-600" />
                    </div>
                    <div>
                      <h3 className="text-base font-semibold text-slate-900">
                        全区报告批量上传与匹配
                      </h3>
                      <p className="mb-4 mt-1 text-sm text-slate-500">
                        支持批量拖拽多个 PDF 文件，或上传 ZIP 压缩包。系统将自动解析文件名并智能匹配到对应的组织架构。
                      </p>
                      <button
                        onClick={() => setIsUploadModalOpen(true)}
                        className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-700"
                      >
                        <UploadCloud className="h-4 w-4" />
                        批量上传报告
                      </button>
                    </div>
                  </div>
                </div>
              </section>

              <section>
                <h2 className="mb-4 text-lg font-bold text-slate-900">任务与规则运行</h2>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="rounded-xl border border-border bg-white p-6 shadow-sm">
                    <div className="mb-3 flex items-center gap-3">
                      <div className="rounded-lg bg-slate-100 p-2 text-slate-600">
                        <Activity className="h-5 w-5" />
                      </div>
                      <h3 className="font-semibold text-slate-900">按组织批量重分析</h3>
                    </div>
                    <p className="mb-6 h-10 text-sm text-slate-500">
                      选择特定部门或全区，对历史报告重新执行完整的 AI 审查与规则校验流水线。
                    </p>
                    <button
                      onClick={() => handleAction("reanalyze")}
                      disabled={actionStatus.id === "reanalyze"}
                      className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
                    >
                      {actionStatus.id === "reanalyze" && actionStatus.status === "loading" ? (
                        <>
                          <RefreshCw className="h-4 w-4 animate-spin" />
                          执行中...
                        </>
                      ) : actionStatus.id === "reanalyze" &&
                        actionStatus.status === "success" ? (
                        <>
                          <CheckCircle2 className="h-4 w-4 text-success-600" />
                          已触发重分析
                        </>
                      ) : (
                        "配置并执行"
                      )}
                    </button>
                  </div>

                  <div className="rounded-xl border border-border bg-white p-6 shadow-sm">
                    <div className="mb-3 flex items-center gap-3">
                      <div className="rounded-lg bg-slate-100 p-2 text-slate-600">
                        <RefreshCw className="h-5 w-5" />
                      </div>
                      <h3 className="font-semibold text-slate-900">批量重匹配规则</h3>
                    </div>
                    <p className="mb-6 h-10 text-sm text-slate-500">
                      当底层审查规则库更新后，无需重新解析文档，仅对已提取 Facts 重新运行规则引擎。
                    </p>
                    <button
                      onClick={() => handleAction("rematch")}
                      disabled={actionStatus.id === "rematch"}
                      className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
                    >
                      {actionStatus.id === "rematch" && actionStatus.status === "loading" ? (
                        <>
                          <RefreshCw className="h-4 w-4 animate-spin" />
                          执行中...
                        </>
                      ) : actionStatus.id === "rematch" &&
                        actionStatus.status === "success" ? (
                        <>
                          <CheckCircle2 className="h-4 w-4 text-success-600" />
                          已触发重匹配
                        </>
                      ) : (
                        "执行重匹配"
                      )}
                    </button>
                  </div>
                </div>
              </section>

              <section>
                <h2 className="mb-4 flex items-center gap-2 text-lg font-bold text-danger-600">
                  <AlertTriangle className="h-5 w-5" />
                  危险操作
                </h2>
                <div className="flex items-start justify-between rounded-xl border border-danger-100 bg-danger-50 p-6 shadow-sm">
                  <div>
                    <h3 className="text-base font-semibold text-danger-900">
                      清理旧版结构化入库数据
                    </h3>
                    <p className="mt-1 max-w-xl text-sm text-danger-700/80">
                      删除系统中版本号低于 v2.0 的历史结构化入库数据。该操作不可逆，通常用于大版本升级后的存储清理。
                    </p>
                  </div>
                  <button
                    onClick={() => handleAction("cleanup")}
                    disabled={actionStatus.id === "cleanup"}
                    className="flex shrink-0 items-center gap-2 rounded-lg bg-danger-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-danger-700"
                  >
                    {actionStatus.id === "cleanup" && actionStatus.status === "loading" ? (
                      <>
                        <RefreshCw className="h-4 w-4 animate-spin" />
                        清理中...
                      </>
                    ) : actionStatus.id === "cleanup" &&
                      actionStatus.status === "success" ? (
                      <>
                        <CheckCircle2 className="h-4 w-4" />
                        清理完成
                      </>
                    ) : (
                      <>
                        <Trash2 className="h-4 w-4" />
                        执行清理
                      </>
                    )}
                  </button>
                </div>
              </section>
            </div>
          ) : null}

          {activeTab === "analysis" ? (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-300">
              <AnalysisResultsPanel />
            </div>
          ) : null}

          {activeTab === "users" ? (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-300">
              <section>
                <h2 className="mb-4 text-lg font-bold text-slate-900">用户管理</h2>
                <UserManagementPanel embedded />
              </section>
            </div>
          ) : null}

          {activeTab === "organization" ? (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-300">
              <section>
                <h2 className="mb-4 text-lg font-bold text-slate-900">组织架构管理</h2>
                <div className="rounded-xl border border-border bg-white p-6 shadow-sm">
                  <div className="mb-6 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="rounded-lg bg-primary-50 p-2 text-primary-600">
                        <Users className="h-5 w-5" />
                      </div>
                      <div>
                        <h3 className="font-semibold text-slate-900">部门与单位字典</h3>
                        <p className="mt-0.5 text-sm text-slate-500">
                          管理全区组织架构树，支持全量导入与导出。
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <button className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-700">
                      <UploadCloud className="h-4 w-4" />
                      导入组织架构（Excel）
                    </button>
                    <button className="flex items-center gap-2 rounded-lg border border-border bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50">
                      <FileText className="h-4 w-4 text-slate-400" />
                      导出当前架构
                    </button>
                  </div>
                </div>
              </section>
            </div>
          ) : null}

          {activeTab === "system" ? (
            <div className="flex flex-col items-center justify-center py-20 text-slate-400 animate-in fade-in slide-in-from-bottom-4 duration-300">
              <Settings className="mb-4 h-16 w-16 text-slate-300" />
              <p className="text-lg font-medium text-slate-600">系统设置开发中</p>
              <p className="mt-2 text-sm">后续会补充模型参数、阈值和规则配置能力。</p>
            </div>
          ) : null}
        </div>
      </div>

      {isUploadModalOpen ? (
        <BatchUploadModal
          defaultDocType="dept_budget"
          onClose={() => setIsUploadModalOpen(false)}
          onComplete={() => setIsUploadModalOpen(false)}
        />
      ) : null}
    </div>
  );
}
