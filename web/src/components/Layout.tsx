import type { ReactNode } from "react";
import { TopBar } from "./TopBar";
import { cn } from "@/lib/utils";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="relative min-h-screen bg-bg">
      <TopBar />
      <main className="mx-auto max-w-7xl px-4 py-8 lm-anim-fade-up md:px-6">
        {children}
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** 标题上方的小型标签，例如 "管理 / 用户" */
  eyebrow?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "mb-7 flex flex-col gap-4 border-b border-border/60 pb-5",
        "md:flex-row md:items-end md:justify-between md:gap-6",
      )}
    >
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-subtle">
            {eyebrow}
          </div>
        )}
        <h1 className="text-2xl font-semibold tracking-tight md:text-[26px]">
          {title}
        </h1>
        {description && (
          <p className="mt-1.5 text-sm leading-relaxed text-subtle">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
