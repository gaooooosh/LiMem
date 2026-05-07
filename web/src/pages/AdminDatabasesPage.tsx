import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Table, THead, TBody, TR, TH, TD, EmptyRow } from "@/components/ui/Table";
import { adminApi } from "@/api/client";
import type { DatabaseView } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { ArrowRight } from "lucide-react";

export function AdminDatabasesPage() {
  const [list, setList] = useState<DatabaseView[] | null>(null);
  const [showArchived, setShowArchived] = useState(true);

  useEffect(() => {
    setList(null);
    adminApi
      .listAllDatabases(showArchived)
      .then(setList)
      .catch(() => setList([]));
  }, [showArchived]);

  return (
    <Layout>
      <PageHeader
        title="全部数据库"
        description="跨用户的所有库。点击进入可使用 admin 身份越权访问业务接口。"
        actions={
          <label className="flex items-center gap-2 text-sm text-subtle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            显示已归档
          </label>
        }
      />
      <Table>
        <THead>
          <TR>
            <TH>名称</TH>
            <TH>db_id</TH>
            <TH>owner</TH>
            <TH>状态</TH>
            <TH>创建时间</TH>
            <TH>最后访问</TH>
            <TH className="text-right">操作</TH>
          </TR>
        </THead>
        <TBody>
          {list === null ? (
            <EmptyRow colSpan={7} text="加载中…" />
          ) : list.length === 0 ? (
            <EmptyRow colSpan={7} text="暂无库" />
          ) : (
            list.map((d) => (
              <TR key={d.db_id}>
                <TD className="font-medium">{d.display_name}</TD>
                <TD className="font-mono text-xs text-subtle">{shortId(d.db_id, 16)}</TD>
                <TD>
                  <Link to={`/ui/admin/users/${d.owner_user_id}`} className="font-mono text-xs text-accent hover:underline">
                    {shortId(d.owner_user_id, 12)}
                  </Link>
                </TD>
                <TD>
                  <Badge variant={d.status === "active" ? "success" : "outline"}>
                    {d.status === "active" ? "活跃" : "归档"}
                  </Badge>
                </TD>
                <TD className="text-xs text-subtle">{formatDate(d.created_at)}</TD>
                <TD className="text-xs text-subtle">{formatDate(d.last_accessed_at)}</TD>
                <TD className="text-right">
                  <Link to={`/ui/console/db/${d.db_id}`}>
                    <Button variant="outline" size="sm">详情 <ArrowRight className="h-3.5 w-3.5" /></Button>
                  </Link>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>
    </Layout>
  );
}
