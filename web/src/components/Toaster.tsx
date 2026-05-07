// 极简 toast 实现：单例 store + 顶层组件订阅
import { useEffect, useState } from "react";
import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastKind = "info" | "success" | "warning" | "error";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
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
    this.items = [...this.items, { id, kind, message }];
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
  info: "border-border bg-panel text-text",
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/30 bg-warning/10 text-warning",
  error: "border-danger/30 bg-danger/10 text-danger",
};

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => store.subscribe(setItems), []);
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-80 flex-col gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          className={cn(
            "pointer-events-auto flex items-start gap-2 rounded-md border bg-panel px-3 py-2 text-sm shadow-lg",
            styleOf[t.kind],
          )}
        >
          <span className="mt-0.5 shrink-0">{iconOf[t.kind]}</span>
          <div className="flex-1 break-words">{t.message}</div>
          <button
            type="button"
            onClick={() => store.dismiss(t.id)}
            className="-mr-1 -mt-0.5 rounded p-0.5 text-subtle hover:bg-muted"
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
