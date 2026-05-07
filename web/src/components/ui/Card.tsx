import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** 是否启用 hover 抬升态（适合可点击 / 可链接卡片） */
  interactive?: boolean;
  /** 是否使用更柔和的玻璃质感 */
  glass?: boolean;
}

export function Card({ className, interactive, glass, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border shadow-soft",
        glass ? "lm-glass" : "bg-panel",
        "transition-[transform,box-shadow,border-color] duration-200 ease-out-soft",
        interactive &&
          "hover:-translate-y-0.5 hover:shadow-md hover:border-border-strong cursor-pointer",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 border-b border-border/70 px-5 py-4",
        className,
      )}
      {...rest}
    />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("text-base font-semibold tracking-tight", className)}
      {...rest}
    />
  );
}

export function CardDescription({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("text-sm text-subtle", className)} {...rest} />;
}

export function CardContent({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 border-t border-border/70 px-5 py-3",
        className,
      )}
      {...rest}
    />
  );
}
