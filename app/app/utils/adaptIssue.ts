/**
 * 统一的Issue数据适配函数
 * 确保前端数据映射一致性，支持snake_case和camelCase转换
 */

import { normalizeSeverityCode } from "../../lib/issueSeverity";

export interface AdaptedIssue {
  id: string;
  source: "ai" | "rule";
  ruleId: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  title: string;
  message: string;
  page?: number;
  evidence: any[];
  tags: string[];
  createdAt: number;
  metrics: Record<string, any>;
  
  // 兼容性字段（camelCase）
  rule_id?: string;
  created_at?: number;
}

/**
 * 统一的Issue适配函数
 * 处理snake_case到camelCase的转换，确保数据一致性
 */
export function adaptIssue(issue: any): AdaptedIssue {
  const adapted: AdaptedIssue = {
    id: issue.id || `issue_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    source: issue.source === "ai" || issue.source === "AI_VALIDATOR" ? "ai" : "rule",
    ruleId: issue.rule_id || issue.ruleId || issue.rule || "",
    severity: normalizeSeverity(issue.severity),
    title: issue.title || (issue.message ? issue.message.split('。')[0] + (issue.message.includes('。') ? '。' : '') : ""),
    message: issue.message || "",
    page: issue.page || issue.location?.page,
    evidence: issue.evidence || [],
    tags: issue.tags || [],
    createdAt: issue.created_at || issue.createdAt || Date.now() / 1000,
    metrics: issue.metrics || {},
    
    // 兼容性字段（过渡期）
    rule_id: issue.rule_id || issue.ruleId || issue.rule || "",
    created_at: issue.created_at || issue.createdAt || Date.now() / 1000,
  };

  return adapted;
}

/**
 * 标准化严重程度
 */
function normalizeSeverity(severity: string): "critical" | "high" | "medium" | "low" | "info" {
  return normalizeSeverityCode(severity);
}

/**
 * 批量适配Issue数组
 */
export function adaptIssues(issues: any[]): AdaptedIssue[] {
  if (!Array.isArray(issues)) return [];
  return issues.map(adaptIssue);
}

/**
 * 检查是否为空态（AI结果为空且服务降级）
 */
export function isEmptyStateWithFallback(aiFindings: any[], meta: any): boolean {
  return aiFindings.length === 0 && meta?.provider_stats?.some((stat: any) => stat.fell_back === true);
}

/**
 * 获取空态提示信息
 */
export function getEmptyStateMessage(aiFindings: any[], meta: any): string {
  if (isEmptyStateWithFallback(aiFindings, meta)) {
    return "AI 服务降级/结果为空（详见日志）";
  }
  
  if (aiFindings.length === 0) {
    return "AI 检测未发现问题";
  }
  
  return "没有发现问题";
}
