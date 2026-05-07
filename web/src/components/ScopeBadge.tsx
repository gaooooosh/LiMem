import { Badge } from "./ui/Badge";
import type { Scope } from "@/api/types";

const labels: Record<Scope, string> = {
  r: "读",
  w: "写",
  admin: "管理",
};

const variantOf: Record<Scope, "accent" | "warning" | "danger"> = {
  r: "accent",
  w: "warning",
  admin: "danger",
};

export function ScopeBadge({ scope }: { scope: Scope }) {
  return (
    <Badge variant={variantOf[scope]} className="uppercase">
      {scope} <span className="text-[10px] opacity-70">·</span> {labels[scope]}
    </Badge>
  );
}

export function ScopeBadgeList({ scopes }: { scopes: Scope[] | string }) {
  const list = Array.isArray(scopes)
    ? scopes
    : (scopes
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter((s): s is Scope => s === "r" || s === "w" || s === "admin"));
  return (
    <span className="inline-flex flex-wrap gap-1">
      {list.length === 0 ? (
        <Badge variant="outline">无</Badge>
      ) : (
        list.map((s) => <ScopeBadge key={s} scope={s} />)
      )}
    </span>
  );
}
