import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * 骨架屏单元：用于替换"加载中…"文本。
 * 通过 className 控制宽高，例如 `h-4 w-32`。
 */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("lm-skeleton", className)} {...rest} />;
}

/** 多行文本骨架 */
export function SkeletonText({
  rows = 3,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn("h-3.5 w-full", i === rows - 1 && "w-2/3")}
        />
      ))}
    </div>
  );
}

/** 卡片化骨架（含上下两块） */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-panel p-5 shadow-soft",
        className,
      )}
    >
      <Skeleton className="mb-3 h-4 w-32" />
      <SkeletonText rows={3} />
    </div>
  );
}
