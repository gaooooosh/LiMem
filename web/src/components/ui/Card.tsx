import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-panel shadow-sm",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("border-b border-border px-5 py-3", className)} {...rest} />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("text-base font-semibold", className)} {...rest} />;
}

export function CardDescription({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-1 text-sm text-subtle", className)} {...rest} />;
}

export function CardContent({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-center gap-2 border-t border-border px-5 py-3", className)}
      {...rest}
    />
  );
}
