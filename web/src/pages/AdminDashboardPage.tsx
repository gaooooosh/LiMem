import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { adminApi } from "@/api/client";
import type { AdminHealth, DatabaseView, UserView } from "@/api/types";
import { Activity, Database, Users } from "lucide-react";

export function AdminDashboardPage() {
  const [users, setUsers] = useState<UserView[] | null>(null);
  const [dbs, setDbs] = useState<DatabaseView[] | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);

  useEffect(() => {
    Promise.allSettled([adminApi.listUsers(), adminApi.listAllDatabases(true), adminApi.health()]).then(
      ([u, d, h]) => {
        setUsers(u.status === "fulfilled" ? u.value : []);
        setDbs(d.status === "fulfilled" ? d.value : []);
        setHealth(h.status === "fulfilled" ? h.value : null);
      },
    );
  }, []);

  const activeDbs = dbs?.filter((d) => d.status === "active").length ?? 0;
  const archivedDbs = dbs?.filter((d) => d.status === "archived").length ?? 0;

  return (
    <Layout>
      <PageHeader
        title="管理员仪表盘"
        description="系统级总览：用户数、库数、连接池状态。仅 admin scope 可见。"
      />
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          icon={<Users className="h-5 w-5" />}
          label="用户总数"
          value={users === null ? "…" : users.length}
          href="/ui/admin/users"
        />
        <StatCard
          icon={<Database className="h-5 w-5" />}
          label="数据库（活跃 / 归档）"
          value={dbs === null ? "…" : `${activeDbs} / ${archivedDbs}`}
          href="/ui/admin/databases"
        />
        <StatCard
          icon={<Activity className="h-5 w-5" />}
          label="服务状态"
          value={health?.status ?? "…"}
          badge={health?.status === "ok" ? "success" : "warning"}
        />
      </div>

      <Card className="mt-6">
        <CardContent>
          <div className="mb-2 text-sm font-semibold">连接池详情</div>
          <pre className="overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
            {health ? JSON.stringify(health.pool, null, 2) : "加载中…"}
          </pre>
        </CardContent>
      </Card>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <Card>
          <CardContent>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-semibold">最近创建的用户</div>
              <Link to="/ui/admin/users"><Button variant="ghost" size="sm">查看全部</Button></Link>
            </div>
            <ul className="divide-y divide-border">
              {(users ?? []).slice(-5).reverse().map((u) => (
                <li key={u.id} className="flex items-center justify-between py-2 text-sm">
                  <Link to={`/ui/admin/users/${u.id}`} className="font-medium text-accent hover:underline">
                    {u.name}
                  </Link>
                  <span className="text-xs text-subtle">{u.created_at}</span>
                </li>
              ))}
              {users?.length === 0 && <li className="py-3 text-center text-sm text-subtle">暂无用户</li>}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-semibold">最近创建的库</div>
              <Link to="/ui/admin/databases"><Button variant="ghost" size="sm">查看全部</Button></Link>
            </div>
            <ul className="divide-y divide-border">
              {(dbs ?? []).slice(-5).reverse().map((d) => (
                <li key={d.db_id} className="flex items-center justify-between py-2 text-sm">
                  <span className="font-medium">{d.display_name}</span>
                  <Badge variant={d.status === "active" ? "success" : "outline"}>
                    {d.status === "active" ? "活跃" : "归档"}
                  </Badge>
                </li>
              ))}
              {dbs?.length === 0 && <li className="py-3 text-center text-sm text-subtle">暂无库</li>}
            </ul>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}

function StatCard({
  icon,
  label,
  value,
  href,
  badge,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  href?: string;
  badge?: "success" | "warning";
}) {
  const inner = (
    <Card className="transition-colors hover:border-accent/40">
      <CardContent className="flex items-center gap-4">
        <div className="grid h-10 w-10 place-items-center rounded-md bg-accent/10 text-accent">{icon}</div>
        <div className="flex-1">
          <div className="text-xs uppercase tracking-wide text-subtle">{label}</div>
          <div className="mt-0.5 text-2xl font-semibold">
            {value}
            {badge && (
              <Badge variant={badge} className="ml-2 align-middle">{badge === "success" ? "OK" : "WARN"}</Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
  return href ? <Link to={href}>{inner}</Link> : inner;
}
