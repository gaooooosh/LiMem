import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { DatabaseTable } from "@/components/DatabaseTable";
import { ArrowRight, Plus } from "lucide-react";
import { dbApi } from "@/api/client";
import { useAuth, hasScope } from "@/auth/AuthContext";
import type { DatabaseView } from "@/api/types";
import { toast } from "@/components/Toaster";

export function ConsolePage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [dbs, setDbs] = useState<DatabaseView[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const canWrite = hasScope(me, "w");
  const canCreate = canWrite && !me?.is_root;

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

  return (
    <Layout>
      <PageHeader
        eyebrow="控制台"
        title="我的数据库"
        description="每个数据库是一个独立的 Kuzu 图记忆库，物理隔离。归档保留数据，删除则不可逆销毁。"
        actions={
          <Button
            onClick={() => setCreating(true)}
            disabled={!canCreate}
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

      <DatabaseTable
        list={dbs}
        canWrite={canWrite}
        onChange={load}
        emptyText={<DatabaseEmptyText isRoot={!!me?.is_root} canCreate={canCreate} />}
        emptyAction={
          <DatabaseEmptyAction
            isRoot={!!me?.is_root}
            canCreate={canCreate}
            onCreate={() => setCreating(true)}
          />
        }
      />

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
    </Layout>
  );
}

function DatabaseEmptyText({
  isRoot,
  canCreate,
}: {
  isRoot: boolean;
  canCreate: boolean;
}) {
  if (isRoot) {
    return "ROOT 不能直接建库。请先创建具名 admin user，再使用该用户的 Key 建库。";
  }
  if (!canCreate) {
    return "当前 Key 缺少 w scope，暂时不能创建数据库。";
  }
  return "还没有数据库，可以先创建一个独立记忆库。";
}

function DatabaseEmptyAction({
  isRoot,
  canCreate,
  onCreate,
}: {
  isRoot: boolean;
  canCreate: boolean;
  onCreate: () => void;
}) {
  if (isRoot) {
    return (
      <Link to="/ui/admin/users">
        <Button variant="outline" size="sm">
          前往用户管理 <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      </Link>
    );
  }
  if (!canCreate) {
    return (
      <Link to="/ui/console/keys">
        <Button variant="outline" size="sm">
          管理我的 Key <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      </Link>
    );
  }
  return (
    <Button size="sm" onClick={onCreate}>
      <Plus className="h-3.5 w-3.5" /> 新建数据库
    </Button>
  );
}
