import { useCallback, useEffect, useState } from "react";
import { Layout, PageHeader } from "@/components/Layout";
import { DatabaseTable } from "@/components/DatabaseTable";
import { adminApi } from "@/api/client";
import type { DatabaseView } from "@/api/types";

export function AdminDatabasesPage() {
  const [list, setList] = useState<DatabaseView[] | null>(null);
  const [showArchived, setShowArchived] = useState(true);

  const reload = useCallback(() => {
    setList(null);
    adminApi
      .listAllDatabases(showArchived)
      .then(setList)
      .catch(() => setList([]));
  }, [showArchived]);

  useEffect(reload, [reload]);

  return (
    <Layout>
      <PageHeader
        eyebrow="管理后台 · 数据库"
        title="全部数据库"
        description="跨用户的所有库。归档保留数据；删除将不可逆销毁 Kuzu 文件与审计日志。"
        actions={
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border bg-panel px-3 py-1.5 text-sm text-text-soft shadow-soft transition hover:border-border-strong">
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
      <DatabaseTable list={list} adminMode onChange={reload} />
    </Layout>
  );
}
