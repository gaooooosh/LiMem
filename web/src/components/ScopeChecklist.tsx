import type { Scope } from "@/api/types";
import { cn } from "@/lib/utils";

interface Props {
  value: Scope[];
  onChange: (next: Scope[]) => void;
  /** 调用方持有的 scope，超出此集合的复选框被禁用以防自我提权 */
  allowed: Scope[];
  /** 是否显示 admin 选项；用户自助签发场景 admin 通常被 allowed 排除掉 */
  showAdmin?: boolean;
}

const all: { scope: Scope; label: string; desc: string }[] = [
  { scope: "r", label: "读 (r)", desc: "查询、健康、统计、审计" },
  { scope: "w", label: "写 (w)", desc: "建/删库、ingest、演化、图操作" },
  { scope: "admin", label: "管理 (admin)", desc: "等同 ROOT，谨慎签发" },
];

export function ScopeChecklist({ value, onChange, allowed, showAdmin = true }: Props) {
  const items = showAdmin ? all : all.filter((it) => it.scope !== "admin");
  const toggle = (s: Scope) => {
    if (value.includes(s)) onChange(value.filter((x) => x !== s));
    else onChange([...value, s]);
  };
  return (
    <div className="space-y-1.5">
      {items.map((it) => {
        const can = allowed.includes(it.scope);
        const checked = value.includes(it.scope);
        return (
          <label
            key={it.scope}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 hover:bg-muted/50",
              !can && "cursor-not-allowed opacity-50 hover:bg-transparent",
              checked && "bg-accent/5 border-accent/40",
            )}
            title={can ? "" : "当前 Key 不具备此 scope，无法签发"}
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={!can}
              onChange={() => toggle(it.scope)}
              className="mt-1 h-4 w-4 accent-accent"
            />
            <div className="flex-1">
              <div className="text-sm font-medium">{it.label}</div>
              <div className="text-xs text-subtle">{it.desc}</div>
            </div>
          </label>
        );
      })}
    </div>
  );
}
