import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "accent" | "success" | "warning" | "danger" | "outline";

const variants: Record<BadgeVariant, string> = {
  default: "bg-muted text-text",
  accent: "bg-accent/10 text-accent border border-accent/20",
  success: "bg-success/10 text-success border border-success/20",
  warning: "bg-warning/10 text-warning border border-warning/20",
  danger: "bg-danger/10 text-danger border border-danger/20",
  outline: "border border-border text-subtle",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = "default", ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        variants[variant],
        className,
      )}
      {...rest}
    />
  );
}
