import type { HTMLAttributes, TableHTMLAttributes, ThHTMLAttributes, TdHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Table({ className, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-hidden rounded-xl border border-border bg-panel shadow-soft">
      <div className="w-full overflow-auto">
        <table className={cn("w-full text-sm", className)} {...rest} />
      </div>
    </div>
  );
}

export function THead({ className, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn(
        "sticky top-0 z-[1] bg-panel-soft/95 backdrop-blur-sm",
        "border-b border-border text-left text-[11px] font-semibold uppercase tracking-[0.06em] text-subtle",
        className,
      )}
      {...rest}
    />
  );
}

export function TBody({ className, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody
      className={cn("divide-y divide-border/70 [&_tr:last-child]:border-b-0", className)}
      {...rest}
    />
  );
}

export function TR({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "transition-colors duration-150 ease-out-soft",
        "hover:bg-muted/60",
        className,
      )}
      {...rest}
    />
  );
}

export function TH({ className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("px-4 py-3 font-semibold first:pl-5 last:pr-5", className)}
      {...rest}
    />
  );
}

export function TD({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn("px-4 py-3 align-middle first:pl-5 last:pr-5", className)}
      {...rest}
    />
  );
}

export function EmptyRow({
  colSpan,
  text = "暂无数据",
  icon,
}: {
  colSpan: number;
  text?: string;
  icon?: React.ReactNode;
}) {
  return (
    <tr>
      <td className="px-4 py-12 text-center" colSpan={colSpan}>
        <div className="flex flex-col items-center justify-center gap-2 text-subtle">
          {icon && (
            <div className="grid h-10 w-10 place-items-center rounded-full bg-muted text-subtle/80">
              {icon}
            </div>
          )}
          <span className="text-sm">{text}</span>
        </div>
      </td>
    </tr>
  );
}

/** 给 Table tbody 用的骨架行：colSpan 个单元格，rows 行 */
export function SkeletonRow({ colSpan, rows = 3 }: { colSpan: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: colSpan }).map((__, j) => (
            <td key={j} className="px-4 py-3 first:pl-5 last:pr-5">
              <div className="lm-skeleton h-3.5 w-full max-w-[180px]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
