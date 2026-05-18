// 通用数据库列表表格：被 ConsolePage（自己的库）与 AdminDatabasesPage（全部库）共用。
// 行为：
//   - active：显示「进入」「归档」「删除」
//   - archived：显示「进入」「删除」
//   - 「删除」走 dbApi.hardDelete + DangerConfirmDialog（确认词 = display_name）
//   - 「归档」走 dbApi.archive + ConfirmDialog
// admin 模式额外多一个 owner 列（链接到用户详情）。
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import {
  Table,
  THead,
  TBody,
  TR,
  TH,
  TD,
  EmptyRow,
  SkeletonRow,
} from "@/components/ui/Table";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DangerConfirmDialog } from "@/components/DangerConfirmDialog";
import { dbApi } from "@/api/client";
import { toast } from "@/components/Toaster";
import type { DatabaseView } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { Archive, ArrowRight, Database as DBIcon, Trash2 } from "lucide-react";

interface Props {
  list: DatabaseView[] | null;
  /** admin 模式下多展示 owner 列 + 操作按钮始终启用 */
  adminMode?: boolean;
  /** 是否允许写操作（归档/删除）；adminMode=true 时强制启用 */
  canWrite?: boolean;
  /** 操作完成后刷新回调；不提供时无副作用（如导航走） */
  onChange?: () => void;
  /** 空状态自定义（仅用户控制台用到） */
  emptyText?: React.ReactNode;
  emptyAction?: React.ReactNode;
}

export function DatabaseTable({
  list,
  adminMode = false,
  canWrite = false,
  onChange,
  emptyText,
  emptyAction,
}: Props) {
  const [archiveTarget, setArchiveTarget] = useState<DatabaseView | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DatabaseView | null>(null);
  const [busy, setBusy] = useState(false);

  const allowWrite = adminMode || canWrite;

  const cols = adminMode ? 7 : 6;

  const onArchive = async () => {
    if (!archiveTarget) return;
    setBusy(true);
    try {
      await dbApi.archive(archiveTarget.db_id);
      toast.success(`已归档 ${archiveTarget.display_name}`);
      setArchiveTarget(null);
      onChange?.();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await dbApi.hardDelete(deleteTarget.db_id);
      toast.success(`已彻底删除 ${deleteTarget.display_name}`);
      setDeleteTarget(null);
      onChange?.();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Table>
        <THead>
          <TR>
            <TH>名称</TH>
            <TH>db_id</TH>
            {adminMode && <TH>owner</TH>}
            <TH>状态</TH>
            <TH>创建时间</TH>
            <TH>最后访问</TH>
            <TH className="text-right">操作</TH>
          </TR>
        </THead>
        <TBody>
          {list === null ? (
            <SkeletonRow colSpan={cols} rows={4} />
          ) : list.length === 0 ? (
            <EmptyRow
              colSpan={cols}
              text={emptyText ?? "暂无库"}
              icon={<DBIcon className="h-5 w-5" />}
              action={emptyAction}
            />
          ) : (
            list.map((d) => (
              <TR key={d.db_id}>
                <TD className="font-medium">
                  <span className="inline-flex items-center gap-2.5">
                    <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent/10 text-accent">
                      <DBIcon className="h-3.5 w-3.5" />
                    </span>
                    {d.display_name}
                  </span>
                </TD>
                <TD className="font-mono text-xs text-subtle">{shortId(d.db_id, 16)}</TD>
                {adminMode && (
                  <TD>
                    <Link
                      to={`/ui/admin/users/${d.owner_user_id}`}
                      className="font-mono text-xs text-accent hover:underline"
                    >
                      {shortId(d.owner_user_id, 12)}
                    </Link>
                  </TD>
                )}
                <TD>
                  <Badge variant={d.status === "active" ? "success" : "outline"} dot>
                    {d.status === "active" ? "活跃" : "归档"}
                  </Badge>
                </TD>
                <TD className="text-xs text-subtle">{formatDate(d.created_at)}</TD>
                <TD className="text-xs text-subtle">{formatDate(d.last_accessed_at)}</TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1.5">
                    <Link to={`/ui/console/db/${d.db_id}`}>
                      <Button variant="outline" size="sm">
                        进入 <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </Link>
                    {allowWrite && d.status === "active" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setArchiveTarget(d)}
                      >
                        <Archive className="h-3.5 w-3.5" /> 归档
                      </Button>
                    )}
                    {allowWrite && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(d)}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-danger" /> 删除
                      </Button>
                    )}
                  </div>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>

      <ConfirmDialog
        open={!!archiveTarget}
        onCancel={() => !busy && setArchiveTarget(null)}
        onConfirm={onArchive}
        loading={busy}
        title="归档数据库"
        description={
          <>
            将 <code className="font-mono">{archiveTarget?.display_name}</code> 标记为已归档。
            归档后数据保留但不再可写入。
          </>
        }
        confirmText="确认归档"
        danger
      />

      <DangerConfirmDialog
        open={!!deleteTarget}
        onCancel={() => !busy && setDeleteTarget(null)}
        onConfirm={onDelete}
        loading={busy}
        title="彻底删除数据库"
        description={
          <>
            该操作会删除 <code className="font-mono">{deleteTarget?.display_name}</code> 的
            Kuzu 数据库文件、审计日志与登记记录，不可恢复。
          </>
        }
        confirmPhrase={deleteTarget?.display_name ?? ""}
        inputLabel="请输入数据库的显示名"
        confirmText="永久删除"
      />
    </>
  );
}
