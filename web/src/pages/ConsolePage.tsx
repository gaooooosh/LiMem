import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ScopeBadgeList } from "@/components/ScopeBadge";
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
import { Database, Plus, Archive, ArrowRight, IdCard, KeyRound, ShieldCheck } from "lucide-react";
import { dbApi } from "@/api/client";
import { useAuth, hasScope } from "@/auth/AuthContext";
import type { DatabaseView } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { toast } from "@/components/Toaster";

export function ConsolePage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [dbs, setDbs] = useState<DatabaseView[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<DatabaseView | null>(null);

  const canWrite = hasScope(me, "w");

  const load = async () => {
    setDbs(null);
    try {
      const list = await dbApi.list();
      setDbs(list);
    } catch {
      setDbs([]);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const onCreate = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const db = await dbApi.create(name.trim());
      toast.success(`已创建 ${db.display_name}`);
      setCreating(false);
      setName("");
      navigate(`/ui/console/db/${db.db_id}`);
    } catch {
      // toast 已在 client 中弹
    } finally {
      setBusy(false);
    }
  };

  const onArchive = async () => {
    if (!archiveTarget) return;
    setBusy(true);
    try {
      await dbApi.archive(archiveTarget.db_id);
      toast.success(`已归档 ${archiveTarget.display_name}`);
      setArchiveTarget(null);
      load();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <Layout>
      <PageHeader
        eyebrow="控制台"
        title="我的数据库"
        description="每个数据库是一个独立的 Kuzu 图记忆库，物理隔离。可在此创建、归档与进入详情。"
        actions={
          <Button
            onClick={() => setCreating(true)}
            disabled={!canWrite || me?.is_root}
            title={
              me?.is_root
                ? "ROOT 不能直接建库；请用 admin 创建用户后用其 Key 建库"
                : !canWrite
                  ? "当前 Key 缺少 w scope"
                  : ""
            }
          >
            <Plus className="h-4 w-4" /> 新建数据库
          </Button>
        }
      />

      {me && (
        <Card className="mb-6 overflow-hidden">
          <CardContent className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <InfoCell
              icon={<IdCard className="h-4 w-4" />}
              label="当前身份"
              tone="accent"
            >
              <div className="flex items-center gap-1.5 text-sm font-medium">
                {me.user_name}
                {me.is_root && <Badge variant="danger" dot>ROOT</Badge>}
              </div>
              <div className="mt-1 truncate font-mono text-[11px] text-subtle">
                {me.user_id}
              </div>
            </InfoCell>
            <InfoCell
              icon={<ShieldCheck className="h-4 w-4" />}
              label="权限"
              tone="success"
            >
              <ScopeBadgeList scopes={me.scopes} />
            </InfoCell>
            <InfoCell
              icon={<KeyRound className="h-4 w-4" />}
              label="当前 Key"
              tone="warning"
            >
              <div className="font-mono text-xs">
                {shortId(me.key_id, 12)}
              </div>
              {me.key_label && (
                <div className="text-[11px] text-subtle">{me.key_label}</div>
              )}
            </InfoCell>
            <div className="flex items-end justify-end sm:col-span-2 lg:col-span-1">
              <Link to="/ui/console/keys" className="w-full">
                <Button variant="outline" className="w-full">
                  管理我的 Key <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      <Table>
        <THead>
          <TR>
            <TH>名称</TH>
            <TH>db_id</TH>
            <TH>状态</TH>
            <TH>创建时间</TH>
            <TH>最后访问</TH>
            <TH className="text-right">操作</TH>
          </TR>
        </THead>
        <TBody>
          {dbs === null ? (
            <SkeletonRow colSpan={6} rows={4} />
          ) : dbs.length === 0 ? (
            <EmptyRow
              colSpan={6}
              text="还没有数据库，点击右上角创建一个"
              icon={<Database className="h-5 w-5" />}
            />
          ) : (
            dbs.map((d) => (
              <TR key={d.db_id}>
                <TD className="font-medium">
                  <span className="inline-flex items-center gap-2.5">
                    <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent/10 text-accent">
                      <Database className="h-3.5 w-3.5" />
                    </span>
                    {d.display_name}
                  </span>
                </TD>
                <TD className="font-mono text-xs text-subtle">
                  {shortId(d.db_id, 16)}
                </TD>
                <TD>
                  {d.status === "active" ? (
                    <Badge variant="success" dot>活跃</Badge>
                  ) : (
                    <Badge variant="outline">已归档</Badge>
                  )}
                </TD>
                <TD className="text-xs text-subtle">{formatDate(d.created_at)}</TD>
                <TD className="text-xs text-subtle">
                  {formatDate(d.last_accessed_at)}
                </TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1.5">
                    <Link to={`/ui/console/db/${d.db_id}`}>
                      <Button variant="outline" size="sm">
                        进入 <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </Link>
                    {d.status === "active" && canWrite && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setArchiveTarget(d)}
                      >
                        <Archive className="h-3.5 w-3.5" /> 归档
                      </Button>
                    )}
                  </div>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>

      <Dialog
        open={creating}
        onClose={() => !busy && setCreating(false)}
        title="新建数据库"
        description="display_name 仅作展示，db_id 由后端自动生成。"
      >
        <div>
          <Label htmlFor="dn" className="normal-case tracking-normal">
            显示名
          </Label>
          <Input
            id="dn"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如：我的车载助手记忆库"
            autoFocus
          />
        </div>
        <DialogActions>
          <Button variant="ghost" onClick={() => setCreating(false)} disabled={busy}>
            取消
          </Button>
          <Button onClick={onCreate} loading={busy} disabled={!name.trim()}>
            创建
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!archiveTarget}
        onCancel={() => setArchiveTarget(null)}
        onConfirm={onArchive}
        loading={busy}
        title="归档数据库"
        description={
          <>
            将 <code className="font-mono">{archiveTarget?.display_name}</code>{" "}
            标记为已归档。 归档后数据保留但不再可写入；如需恢复请联系管理员。
          </>
        }
        confirmText="确认归档"
        danger
      />
    </Layout>
  );
}

function InfoCell({
  icon,
  label,
  tone,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  tone: "accent" | "success" | "warning";
  children: React.ReactNode;
}) {
  const toneCls: Record<string, string> = {
    accent: "bg-accent/10 text-accent",
    success: "bg-success/10 text-success",
    warning: "bg-warning/12 text-warning",
  };
  return (
    <div className="flex items-start gap-3">
      <div
        className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${toneCls[tone]}`}
      >
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-subtle">
          {label}
        </div>
        <div className="mt-1">{children}</div>
      </div>
    </div>
  );
}
