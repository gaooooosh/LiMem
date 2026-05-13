// 右侧滑出抽屉：与 Dialog 共享焦点陷阱 / esc / 锁滚动语义，但布局贴右边
import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** 是否点击外部蒙层/ESC 关闭，默认 true */
  dismissOnOverlay?: boolean;
  /** 控制宽度的 tailwind class，默认 max-w-2xl */
  widthClassName?: string;
  className?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  dismissOnOverlay = true,
  widthClassName = "max-w-2xl",
  className,
}: DrawerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previousActive = document.activeElement as HTMLElement | null;

    const getFocusableElements = () => {
      const root = ref.current;
      if (!root) return [];
      return Array.from(
        root.querySelectorAll<HTMLElement>(
          [
            "a[href]",
            "button:not([disabled])",
            "textarea:not([disabled])",
            "input:not([disabled])",
            "select:not([disabled])",
            "[tabindex]:not([tabindex='-1'])",
          ].join(","),
        ),
      ).filter((el) => !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true");
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dismissOnOverlay) onCloseRef.current();
      if (e.key !== "Tab") return;

      const focusable = getFocusableElements();
      if (focusable.length === 0) {
        e.preventDefault();
        ref.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => {
      const [first] = getFocusableElements();
      (first ?? ref.current)?.focus();
    });

    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      if (previousActive && document.contains(previousActive)) {
        previousActive.focus();
      }
    };
  }, [open, dismissOnOverlay]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      onMouseDown={(e) => {
        if (!dismissOnOverlay) return;
        if (ref.current && !ref.current.contains(e.target as Node)) onCloseRef.current();
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
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "lm-anim-slide-in-right relative z-10 flex h-full w-full flex-col overflow-hidden",
          "border-l border-border bg-panel shadow-lg",
          widthClassName,
          className,
        )}
      >
        {(title || description) && (
          <div className="shrink-0 border-b border-border/70 px-6 py-4 pr-12">
            {title && (
              <div id={titleId} className="text-base font-semibold tracking-tight">
                {title}
              </div>
            )}
            {description && (
              <div id={descriptionId} className="mt-1 text-sm leading-relaxed text-subtle">
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
        <div className="flex-1 overflow-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}
