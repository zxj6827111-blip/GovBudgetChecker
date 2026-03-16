export type SeverityCode = "critical" | "high" | "medium" | "low" | "info";

type SeverityMeta = {
  label: string;
  riskLabel: string;
  badgeClass: string;
  accentClass: string;
  panelClass: string;
};

const SEVERITY_META: Record<SeverityCode, SeverityMeta> = {
  critical: {
    label: "严重",
    riskLabel: "严重风险",
    badgeClass: "border-rose-200 bg-rose-100 text-rose-800",
    accentClass: "bg-rose-600",
    panelClass: "border-rose-100 bg-rose-50 text-rose-700",
  },
  high: {
    label: "高",
    riskLabel: "高风险",
    badgeClass: "border-red-200 bg-red-100 text-red-700",
    accentClass: "bg-red-500",
    panelClass: "border-red-100 bg-red-50 text-red-700",
  },
  medium: {
    label: "中",
    riskLabel: "中风险",
    badgeClass: "border-amber-200 bg-amber-100 text-amber-700",
    accentClass: "bg-amber-500",
    panelClass: "border-amber-100 bg-amber-50 text-amber-700",
  },
  low: {
    label: "低",
    riskLabel: "低风险",
    badgeClass: "border-sky-200 bg-sky-100 text-sky-700",
    accentClass: "bg-sky-500",
    panelClass: "border-sky-100 bg-sky-50 text-sky-700",
  },
  info: {
    label: "提示",
    riskLabel: "提示",
    badgeClass: "border-slate-200 bg-slate-100 text-slate-700",
    accentClass: "bg-slate-300",
    panelClass: "border-slate-200 bg-slate-100 text-slate-700",
  },
};

export const SEVERITY_ORDER: SeverityCode[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
];

export function normalizeSeverityCode(value: unknown): SeverityCode {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (["critical", "fatal"].includes(normalized)) {
    return "critical";
  }
  if (["error", "high"].includes(normalized)) {
    return "high";
  }
  if (["warn", "warning", "medium"].includes(normalized)) {
    return "medium";
  }
  if (normalized === "low") {
    return "low";
  }
  return "info";
}

export function getSeverityMeta(value: unknown, preferredLabel?: string) {
  const code = normalizeSeverityCode(value);
  const meta = SEVERITY_META[code];
  const label = String(preferredLabel ?? "").trim() || meta.label;
  return {
    code,
    label,
    riskLabel: meta.riskLabel,
    badgeClass: meta.badgeClass,
    accentClass: meta.accentClass,
    panelClass: meta.panelClass,
  };
}

export function isHighRiskSeverity(value: unknown): boolean {
  const code = normalizeSeverityCode(value);
  return code === "critical" || code === "high";
}
