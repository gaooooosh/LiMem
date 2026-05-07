import { createContext, useContext, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TabsCtx {
  value: string;
  onChange: (v: string) => void;
}
const Ctx = createContext<TabsCtx | null>(null);

export function Tabs({
  value,
  onChange,
  children,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Ctx.Provider value={{ value, onChange }}>
      <div className={cn("w-full", className)}>{children}</div>
    </Ctx.Provider>
  );
}

export function TabsList({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex flex-wrap items-center gap-1 rounded-xl border border-border",
        "bg-panel-soft/80 p-1 shadow-soft backdrop-blur",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({
  value,
  children,
  disabled,
}: {
  value: string;
  children: ReactNode;
  disabled?: boolean;
}) {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("TabsTrigger must be inside Tabs");
  const active = ctx.value === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={() => ctx.onChange(value)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium",
        "transition-[background,color,box-shadow,border-color,opacity] duration-200 ease-out-soft",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        active
          ? "bg-panel text-text shadow-soft ring-1 ring-border-strong/40"
          : "text-subtle hover:text-text hover:bg-muted/60",
        disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("TabsContent must be inside Tabs");
  if (ctx.value !== value) return null;
  return (
    <div className={cn("mt-4 lm-anim-fade-up", className)}>{children}</div>
  );
}
