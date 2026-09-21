"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  HelpCircle,
  Loader2,
  MinusCircle,
  UserCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui";
import {
  presentMaterialStatus,
  type MaterialStatusIcon,
} from "@/lib/materialStatusPresentation";

/**
 * MaterialStatusBadge：材料状态的唯一渲染入口。
 *
 * 颜色/文案全部来自 `lib/materialStatusPresentation`：三个页面共用同一份定义，
 * 页面里不允许再出现 `status === "missing" ? "text-red-500" : ...` 这类判断。
 * 未登记的状态码会原样显示在徽章里（不吞成空白），便于发现后端新增状态。
 */
const ICONS: Record<MaterialStatusIcon, LucideIcon> = {
  clock: Clock,
  alert: AlertTriangle,
  check: CheckCircle2,
  loader: Loader2,
  "user-check": UserCheck,
  help: HelpCircle,
  slash: MinusCircle,
  x: XCircle,
};

export interface MaterialStatusBadgeProps {
  status: unknown;
  /** 可选：在徽章后附带原因说明（详情页/详情卡用），默认不显示。 */
  className?: string;
  testId?: string;
}

export function MaterialStatusBadge({ status, className, testId }: MaterialStatusBadgeProps) {
  const presentation = presentMaterialStatus(status);
  const Icon = ICONS[presentation.icon];

  return (
    <Badge
      tone={presentation.tone}
      className={className}
      data-testid={testId}
    >
      <span className="inline-flex items-center gap-1" title={presentation.description}>
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        {presentation.label}
      </span>
    </Badge>
  );
}

export default MaterialStatusBadge;
