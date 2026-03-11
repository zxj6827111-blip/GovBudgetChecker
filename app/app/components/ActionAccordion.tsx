import React from 'react';

interface ActionAccordionProps {
  title: string;
  description: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  icon?: React.ReactNode;
}

export default function ActionAccordion({
  title,
  description,
  isOpen,
  onToggle,
  children,
  icon
}: ActionAccordionProps) {
  return (
    <div className="overflow-hidden rounded-2xl border border-gray-200/70 bg-white/80 backdrop-blur-sm shadow-sm transition-all hover:border-gray-300/80 hover:shadow-md">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-gray-50/80"
        title={`展开或收起${title}`}
      >
        <div className="flex min-w-0 items-center gap-3">
          {icon && (
            <div className="hidden h-10 w-10 items-center justify-center rounded-lg border border-indigo-100/50 bg-indigo-50 p-1.5 text-indigo-500 sm:flex">
              {icon}
            </div>
          )}
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <div className="shrink-0 text-sm font-semibold tracking-tight text-gray-800">{title}</div>
              <div className="min-w-0 truncate text-[11px] leading-5 text-gray-500 opacity-90">
                {description}
              </div>
            </div>
          </div>
        </div>
        <svg
          className={`h-4 w-4 shrink-0 text-gray-400 transition-transform duration-300 ${isOpen ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      <div
        className={`grid transition-[grid-template-rows] duration-300 ease-in-out ${
          isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
        }`}
      >
        <div className="overflow-hidden">
          <div className="border-t border-gray-200/70 bg-gray-50/60 px-4 pb-4 pt-3">
            <div className="flex flex-col gap-3">
              {children}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
