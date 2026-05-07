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
  primary: [
    "bg-gradient-brand text-white shadow-soft",
    "hover:shadow-glow hover:brightness-[1.04]",
    "active:brightness-[0.96]",
    "disabled:bg-none disabled:bg-muted disabled:text-subtle disabled:shadow-none",
  ].join(" "),
  secondary: [
    "bg-muted text-text",
    "hover:bg-border-strong/50",
    "disabled:text-subtle",
  ].join(" "),
  ghost: [
    "bg-transparent text-text-soft",
    "hover:bg-muted hover:text-text",
    "disabled:text-subtle",
  ].join(" "),
  danger: [
    "bg-danger text-white shadow-soft",
    "hover:opacity-95 hover:shadow-md",
    "active:opacity-90",
    "disabled:bg-muted disabled:text-subtle disabled:shadow-none",
  ].join(" "),
  outline: [
    "border border-border bg-panel/60 text-text",
    "hover:bg-muted hover:border-border-strong",
    "disabled:text-subtle",
  ].join(" "),
};

const sizeCls: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5 rounded-md",
  md: "h-9 px-4 text-sm gap-2 rounded-lg",
  lg: "h-11 px-6 text-base gap-2 rounded-lg",
  icon: "h-9 w-9 p-0 rounded-lg",
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
        "group/btn relative inline-flex select-none items-center justify-center font-medium",
        "transition-[transform,box-shadow,background,color,opacity] duration-150 ease-out-soft",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "active:scale-[0.98] disabled:active:scale-100",
        "disabled:cursor-not-allowed",
        variantCls[variant],
        sizeCls[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <span
          className={cn(
            "inline-block animate-spin rounded-full border-2 border-current border-r-transparent",
            size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5",
          )}
          aria-hidden
        />
      )}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
