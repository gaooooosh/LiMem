import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "accent" | "success" | "warning" | "danger" | "outline";

const variants: Record<BadgeVariant, string> = {
  default: "bg-muted text-text-soft border border-border/70",
  accent: "bg-accent/10 text-accent border border-accent/25",
  success: "bg-success/10 text-success border border-success/25",
  warning: "bg-warning/12 text-warning border border-warning/30",
  danger: "bg-danger/10 text-danger border border-danger/25",
  outline: "border border-border text-subtle bg-transparent",
};

const dotColor: Record<BadgeVariant, string> = {
  default: "bg-subtle/70",
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  outline: "bg-subtle/60",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  /** 是否在前面显示状态点 */
  dot?: boolean;
}

export function Badge({ className, variant = "default", dot, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium leading-5",
        variants[variant],
        className,
      )}
      {...rest}
    >
      {dot && (
        <span
          className={cn("h-1.5 w-1.5 rounded-full", dotColor[variant])}
          aria-hidden
        />
      )}
      {children}
    </span>
  );
}
