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
    // body scroll lock
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
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" aria-hidden />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        className={cn(
          "relative z-10 max-h-[85vh] w-full max-w-lg overflow-auto rounded-lg border border-border bg-panel shadow-xl",
          className,
        )}
      >
        {(title || description) && (
          <div className="border-b border-border px-5 py-3">
            {title && <div className="text-base font-semibold">{title}</div>}
            {description && (
              <div className="mt-1 text-sm text-subtle">{description}</div>
            )}
          </div>
        )}
        {dismissOnOverlay && (
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="absolute right-3 top-3 rounded-md p-1 text-subtle hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function DialogActions({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "mt-5 flex items-center justify-end gap-2 border-t border-border pt-4",
        className,
      )}
    >
      {children}
    </div>
  );
}
