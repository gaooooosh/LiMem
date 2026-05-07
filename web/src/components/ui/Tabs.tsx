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

export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted p-1",
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
        "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "bg-panel text-text shadow-sm"
          : "text-subtle hover:text-text",
        disabled && "cursor-not-allowed opacity-50",
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
  return <div className={cn("mt-4", className)}>{children}</div>;
}
