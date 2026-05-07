// 极简 toast 实现：单例 store + 顶层组件订阅
import { useEffect, useState } from "react";
import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastKind = "info" | "success" | "warning" | "error";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  ttl: number;
}

type Listener = (items: ToastItem[]) => void;

class ToastStore {
  private items: ToastItem[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.items);
    return () => {
      this.listeners.delete(fn);
    };
  }

  push(kind: ToastKind, message: string, ttl = 4000) {
    const id = this.nextId++;
    this.items = [...this.items, { id, kind, message, ttl }];
    this.emit();
    if (ttl > 0) setTimeout(() => this.dismiss(id), ttl);
  }

  dismiss(id: number) {
    this.items = this.items.filter((t) => t.id !== id);
    this.emit();
  }

  private emit() {
    for (const fn of this.listeners) fn(this.items);
  }
}

const store = new ToastStore();

export const toast = {
  info: (msg: string) => store.push("info", msg),
  success: (msg: string) => store.push("success", msg),
  warning: (msg: string) => store.push("warning", msg),
  error: (msg: string) => store.push("error", msg, 6000),
};

const iconOf: Record<ToastKind, JSX.Element> = {
  info: <Info className="h-4 w-4" />,
  success: <CheckCircle2 className="h-4 w-4" />,
  warning: <TriangleAlert className="h-4 w-4" />,
  error: <TriangleAlert className="h-4 w-4" />,
};

const styleOf: Record<ToastKind, string> = {
  info: "border-border/70 bg-panel/90 text-text",
  success: "border-success/30 bg-success-soft text-success",
  warning: "border-warning/30 bg-warning-soft text-warning",
  error: "border-danger/30 bg-danger-soft text-danger",
};

const accentOf: Record<ToastKind, string> = {
  info: "bg-subtle/60",
  success: "bg-success",
  warning: "bg-warning",
  error: "bg-danger",
};

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => store.subscribe(setItems), []);
  return (
    <div
      className={cn(
        "pointer-events-none fixed right-4 top-4 z-[100] flex w-[22rem] flex-col gap-2",
        "max-w-[calc(100vw-2rem)]",
      )}
    >
      {items.map((t) => (
        <div
          key={t.id}
          className={cn(
            "lm-anim-slide-in-right pointer-events-auto group relative overflow-hidden",
            "flex items-start gap-2.5 rounded-xl border px-3.5 py-3 pr-9 text-sm shadow-md backdrop-blur",
            styleOf[t.kind],
          )}
          role="status"
        >
          <span
            className={cn(
              "absolute left-0 top-0 h-full w-1",
              accentOf[t.kind],
            )}
            aria-hidden
          />
          <span className="mt-0.5 shrink-0">{iconOf[t.kind]}</span>
          <div className="flex-1 break-words leading-relaxed">{t.message}</div>
          <button
            type="button"
            onClick={() => store.dismiss(t.id)}
            className={cn(
              "absolute right-2 top-2 grid h-6 w-6 place-items-center rounded-md",
              "text-current/70 opacity-60 transition hover:bg-black/5 hover:opacity-100",
              "dark:hover:bg-white/10",
            )}
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
          {t.ttl > 0 && (
            <span
              className={cn(
                "absolute bottom-0 left-0 h-[2px] w-full origin-left opacity-50",
                accentOf[t.kind],
              )}
              style={{ animation: `lm-toast-${t.id} ${t.ttl}ms linear forwards` }}
              aria-hidden
            />
          )}
          <style>{`@keyframes lm-toast-${t.id} { from { transform: scaleX(1); } to { transform: scaleX(0); } }`}</style>
        </div>
      ))}
    </div>
  );
}
