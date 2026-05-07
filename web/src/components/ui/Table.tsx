import type { HTMLAttributes, TableHTMLAttributes, ThHTMLAttributes, TdHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Table({ className, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-auto rounded-lg border border-border bg-panel">
      <table className={cn("w-full text-sm", className)} {...rest} />
    </div>
  );
}

export function THead({ className, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn(
        "bg-muted/60 text-left text-xs font-semibold uppercase tracking-wide text-subtle",
        className,
      )}
      {...rest}
    />
  );
}

export function TBody({ className, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-border", className)} {...rest} />;
}

export function TR({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("hover:bg-muted/40", className)} {...rest} />;
}

export function TH({ className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("px-4 py-2.5 font-medium", className)} {...rest} />;
}

export function TD({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-4 py-2.5 align-middle", className)} {...rest} />;
}

export function EmptyRow({ colSpan, text = "暂无数据" }: { colSpan: number; text?: string }) {
  return (
    <tr>
      <td className="px-4 py-8 text-center text-subtle" colSpan={colSpan}>
        {text}
      </td>
    </tr>
  );
}
