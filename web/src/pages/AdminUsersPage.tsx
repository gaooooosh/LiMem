import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
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
import { adminApi } from "@/api/client";
import type { UserView } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { Plus, ArrowRight, Users } from "lucide-react";
import { toast } from "@/components/Toaster";

export function AdminUsersPage() {
  const [users, setUsers] = useState<UserView[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setUsers(null);
    try {
      setUsers(await adminApi.listUsers());
    } catch {
      setUsers([]);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const onCreate = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const u = await adminApi.createUser(name.trim());
      toast.success(`已创建 ${u.name}`);
      setCreating(false);
      setName("");
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
        eyebrow="管理后台 · 用户"
        title="用户管理"
        description="新建用户、查看每个用户的 Key 与库。仅 admin scope 可访问。"
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> 新建用户
          </Button>
        }
      />
      <Table>
        <THead>
          <TR>
            <TH>用户名</TH>
            <TH>user_id</TH>
            <TH>创建时间</TH>
            <TH className="text-right">操作</TH>
          </TR>
        </THead>
        <TBody>
          {users === null ? (
            <SkeletonRow colSpan={4} rows={4} />
          ) : users.length === 0 ? (
            <EmptyRow
              colSpan={4}
              text="尚无用户"
              icon={<Users className="h-5 w-5" />}
            />
          ) : (
            users.map((u) => (
              <TR key={u.id}>
                <TD className="font-medium">
                  <span className="inline-flex items-center gap-2.5">
                    <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-brand-soft text-accent text-xs font-semibold">
                      {u.name.slice(0, 1).toUpperCase()}
                    </span>
                    {u.name}
                  </span>
                </TD>
                <TD className="font-mono text-xs text-subtle">{shortId(u.id, 16)}</TD>
                <TD className="text-xs text-subtle">{formatDate(u.created_at)}</TD>
                <TD className="text-right">
                  <Link to={`/ui/admin/users/${u.id}`}>
                    <Button variant="outline" size="sm">
                      详情 <ArrowRight className="h-3.5 w-3.5" />
                    </Button>
                  </Link>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>

      <Dialog
        open={creating}
        onClose={() => !busy && setCreating(false)}
        title="新建用户"
        description="user.name 必须唯一。创建后请到详情页签发其首个 API Key。"
      >
        <div>
          <Label htmlFor="uname" className="normal-case tracking-normal">
            用户名
          </Label>
          <Input
            id="uname"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="alice"
            autoFocus
          />
        </div>
        <DialogActions>
          <Button variant="ghost" onClick={() => setCreating(false)} disabled={busy}>取消</Button>
          <Button onClick={onCreate} loading={busy} disabled={!name.trim()}>创建</Button>
        </DialogActions>
      </Dialog>
    </Layout>
  );
}
