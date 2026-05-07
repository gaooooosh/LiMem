import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** 是否点击外部蒙层关闭，默认 true；危险操作可设 false */
  dismissOnOverlay?: boolean;
  className?: string;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  dismissOnOverlay = true,
  className,
}: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dismissOnOverlay) onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose, dismissOnOverlay]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onMouseDown={(e) => {
        if (!dismissOnOverlay) return;
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="absolute inset-0 bg-slate-950/55 backdrop-blur-[6px] lm-anim-fade"
        aria-hidden
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        className={cn(
          "lm-anim-scale relative z-10 max-h-[85vh] w-full max-w-lg overflow-auto",
          "rounded-2xl border border-border bg-panel shadow-lg",
          className,
        )}
      >
        {(title || description) && (
          <div className="border-b border-border/70 px-6 py-4">
            {title && (
              <div className="text-base font-semibold tracking-tight">{title}</div>
            )}
            {description && (
              <div className="mt-1 text-sm leading-relaxed text-subtle">
                {description}
              </div>
            )}
          </div>
        )}
        {dismissOnOverlay && (
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className={cn(
              "absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-lg",
              "text-subtle transition-colors hover:bg-muted hover:text-text",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
            )}
          >
            <X className="h-4 w-4" />
          </button>
        )}
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

export function DialogActions({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mt-6 flex items-center justify-end gap-2 border-t border-border/70 pt-4",
        className,
      )}
    >
      {children}
    </div>
  );
}
