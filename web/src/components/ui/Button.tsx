import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "lg" | "icon";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const variantCls: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-accent-hover focus-visible:ring-accent disabled:bg-muted disabled:text-subtle",
  secondary:
    "bg-muted text-text hover:bg-border disabled:text-subtle",
  ghost:
    "bg-transparent text-text hover:bg-muted disabled:text-subtle",
  danger:
    "bg-danger text-white hover:opacity-90 disabled:bg-muted disabled:text-subtle",
  outline:
    "border border-border bg-panel text-text hover:bg-muted disabled:text-subtle",
};

const sizeCls: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-6 text-base",
  icon: "h-9 w-9 p-0",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant = "primary", size = "md", loading, disabled, children, ...rest },
    ref,
  ) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center justify-center gap-2 rounded-md font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "disabled:cursor-not-allowed",
        variantCls[variant],
        sizeCls[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent" />
      )}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
