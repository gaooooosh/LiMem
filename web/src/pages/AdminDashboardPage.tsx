import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { adminApi } from "@/api/client";
import type { AdminHealth, DatabaseView, UserView } from "@/api/types";
import { Activity, ArrowRight, Database, Users } from "lucide-react";
import { formatDate } from "@/lib/utils";

export function AdminDashboardPage() {
  const [users, setUsers] = useState<UserView[] | null>(null);
  const [dbs, setDbs] = useState<DatabaseView[] | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);

  useEffect(() => {
    Promise.allSettled([
      adminApi.listUsers(),
      adminApi.listAllDatabases(true),
      adminApi.health(),
    ]).then(([u, d, h]) => {
      setUsers(u.status === "fulfilled" ? u.value : []);
      setDbs(d.status === "fulfilled" ? d.value : []);
      setHealth(h.status === "fulfilled" ? h.value : null);
    });
  }, []);

  const activeDbs = dbs?.filter((d) => d.status === "active").length ?? 0;
  const archivedDbs = dbs?.filter((d) => d.status === "archived").length ?? 0;

  return (
    <Layout>
      <PageHeader
        eyebrow="管理后台"
        title="系统总览"
        description="用户数、库数、连接池状态。仅 admin scope 可见。"
      />
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          icon={<Users className="h-5 w-5" />}
          label="用户总数"
          value={users === null ? null : users.length}
          href="/ui/admin/users"
          tone="accent"
        />
        <StatCard
          icon={<Database className="h-5 w-5" />}
          label="数据库（活跃 / 归档）"
          value={dbs === null ? null : `${activeDbs} / ${archivedDbs}`}
          href="/ui/admin/databases"
          tone="brand"
        />
        <StatCard
          icon={<Activity className="h-5 w-5" />}
          label="服务状态"
          value={health?.status ?? null}
          badge={health?.status === "ok" ? "success" : "warning"}
          tone={health?.status === "ok" ? "success" : "warning"}
        />
      </div>

      <Card className="mt-6">
        <CardContent>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold">连接池详情</div>
            {health && (
              <Badge variant={health.status === "ok" ? "success" : "warning"} dot>
                {health.status}
              </Badge>
            )}
          </div>
          {!health ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : (
            <PoolGrid pool={health.pool} />
          )}
        </CardContent>
      </Card>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold">最近创建的用户</div>
              <Link to="/ui/admin/users">
                <Button variant="ghost" size="sm">
                  查看全部 <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
            <ul className="divide-y divide-border/70">
              {users === null
                ? Array.from({ length: 5 }).map((_, i) => (
                    <li key={i} className="py-2.5">
                      <Skeleton className="h-3.5 w-2/3" />
                    </li>
                  ))
                : (users ?? [])
                    .slice(-5)
                    .reverse()
                    .map((u) => (
                      <li
                        key={u.id}
                        className="flex items-center justify-between py-2.5 text-sm"
                      >
                        <Link
                          to={`/ui/admin/users/${u.id}`}
                          className="font-medium text-text hover:text-accent transition-colors"
                        >
                          {u.name}
                        </Link>
                        <span className="text-xs text-subtle">
                          {formatDate(u.created_at)}
                        </span>
                      </li>
                    ))}
              {users?.length === 0 && (
                <li className="py-6 text-center text-sm text-subtle">暂无用户</li>
              )}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold">最近创建的库</div>
              <Link to="/ui/admin/databases">
                <Button variant="ghost" size="sm">
                  查看全部 <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
            <ul className="divide-y divide-border/70">
              {dbs === null
                ? Array.from({ length: 5 }).map((_, i) => (
                    <li key={i} className="py-2.5">
                      <Skeleton className="h-3.5 w-3/4" />
                    </li>
                  ))
                : (dbs ?? [])
                    .slice(-5)
                    .reverse()
                    .map((d) => (
                      <li
                        key={d.db_id}
                        className="flex items-center justify-between py-2.5 text-sm"
                      >
                        <span className="font-medium">{d.display_name}</span>
                        <Badge
                          variant={d.status === "active" ? "success" : "outline"}
                          dot
                        >
                          {d.status === "active" ? "活跃" : "归档"}
                        </Badge>
                      </li>
                    ))}
              {dbs?.length === 0 && (
                <li className="py-6 text-center text-sm text-subtle">暂无库</li>
              )}
            </ul>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}

type Tone = "accent" | "brand" | "success" | "warning";

function StatCard({
  icon,
  label,
  value,
  href,
  badge,
  tone = "accent",
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode | null;
  href?: string;
  badge?: "success" | "warning";
  tone?: Tone;
}) {
  const toneCls: Record<Tone, string> = {
    accent: "bg-accent/10 text-accent",
    brand: "bg-gradient-brand-soft text-accent-2",
    success: "bg-success/10 text-success",
    warning: "bg-warning/12 text-warning",
  };
  const inner = (
    <Card interactive={!!href} className="overflow-hidden">
      <CardContent className="flex items-center gap-4">
        <div
          className={`grid h-11 w-11 place-items-center rounded-xl shadow-soft ${toneCls[tone]}`}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-subtle">
            {label}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-2xl font-semibold tracking-tight">
              {value === null ? <Skeleton className="h-7 w-16" /> : value}
            </span>
            {badge && value !== null && (
              <Badge variant={badge} dot>
                {badge === "success" ? "OK" : "WARN"}
              </Badge>
            )}
          </div>
        </div>
        {href && (
          <ArrowRight className="h-4 w-4 text-subtle transition-transform group-hover:translate-x-0.5" />
        )}
      </CardContent>
    </Card>
  );
  return href ? (
    <Link to={href} className="group block">
      {inner}
    </Link>
  ) : (
    inner
  );
}

/** 把 health.pool 渲染成结构化网格，避免裸 JSON */
function PoolGrid({ pool }: { pool: unknown }) {
  if (!pool || typeof pool !== "object") {
    return (
      <pre className="overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
        {JSON.stringify(pool, null, 2)}
      </pre>
    );
  }
  const entries = Object.entries(pool as Record<string, unknown>);
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
      {entries.map(([k, v]) => (
        <div
          key={k}
          className="rounded-lg border border-border/60 bg-bg-soft px-3 py-2.5"
        >
          <div className="text-[11px] font-medium uppercase tracking-wide text-subtle">
            {k}
          </div>
          <div className="mt-0.5 truncate text-sm font-semibold tabular-nums">
            {formatPoolValue(v)}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatPoolValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}
