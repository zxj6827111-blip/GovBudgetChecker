"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

export function WorkbenchCockpitDrawer({ title, onClose, returnFocusTo, children }: {
  title: string; onClose: () => void; returnFocusTo: HTMLElement | null; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
      if (event.key !== "Tab") return;
      const controls = Array.from(ref.current?.querySelectorAll<HTMLElement>("button:not([disabled]),a[href],input,select,summary") ?? [])
        .filter((element) => element.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keydown); if (returnFocusTo?.isConnected) returnFocusTo.focus(); };
  }, [onClose, returnFocusTo]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/30" onClick={onClose} data-testid="gbc-cockpit-backdrop">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="gbc-cockpit-drawer"
        onClick={(event) => event.stopPropagation()} className="h-full w-[calc(100vw-1rem)] max-w-5xl overflow-y-auto border-l border-border bg-white shadow-xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white px-6 py-4">
          <h2 id={titleId} className="text-lg font-semibold text-slate-900">{title}</h2>
          <button type="button" onClick={onClose} aria-label="关闭明细" className="rounded-md p-2 text-slate-500 hover:bg-surface-100 focus-visible:ring-2 focus-visible:ring-primary-400"><X className="h-5 w-5" /></button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}
