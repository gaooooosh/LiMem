import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent } from "@/components/ui/Card";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { Table, THead, TBody, TR, TH, TD, EmptyRow } from "@/components/ui/Table";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OneTimeTokenDialog } from "@/components/OneTimeTokenDialog";
import { ScopeBadgeList } from "@/components/ScopeBadge";
import { ScopeChecklist } from "@/components/ScopeChecklist";
import { adminApi } from "@/api/client";
import type { ApiKeyView, Scope, UserDetail } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { ArrowLeft, ArrowRight, KeyRound, Trash2 } from "lucide-react";
import { toast } from "@/components/Toaster";

export function AdminUserDetailPage() {
  const { userId = "" } = useParams();
  const [data, setData] = useState<UserDetail | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [label, setLabel] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["r", "w"]);
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<{ token: string; label: string } | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiKeyView | null>(null);

  const load = async () => {
    setData(null);
    try {
      setData(await adminApi.getUser(userId));
    } catch {
      setData(null);
    }
  };
  useEffect(() => {
    load();
  }, [userId]);

  const onIssue = async () => {
    if (scopes.length === 0) {
      toast.warning("至少选择一个 scope");
      return;
    }
    setBusy(true);
    try {
      const r = await adminApi.issueKey(userId, label.trim(), scopes.join(","));
      setIssuing(false);
      setLabel("");
      setScopes(["r", "w"]);
      setToken({ token: r.token, label: r.key.label });
      load();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async () => {
    if (!revokeTarget) return;
    setBusy(true);
    try {
      await adminApi.revokeKey(revokeTarget.id);
      toast.success("已撤销");
      setRevokeTarget(null);
      load();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <Layout>
      <div className="mb-3">
        <Link to="/ui/admin/users" className="inline-flex items-center gap-1 text-sm text-subtle hover:text-text">
          <ArrowLeft className="h-3.5 w-3.5" /> 返回用户列表
        </Link>
      </div>
      <PageHeader
        title={data ? data.user.name : userId}
        description={
          data ? (
            <>
              user_id: <code className="font-mono">{data.user.id}</code> · 创建于 {formatDate(data.user.created_at)}
            </>
          ) : (
            "加载中…"
          )
        }
        actions={
          <Button onClick={() => setIssuing(true)}><KeyRound className="h-4 w-4" /> 签发新 Key</Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardContent>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold">API Keys ({data?.keys.length ?? "…"})</div>
            </div>
            <Table>
              <THead>
                <TR>
                  <TH>Label</TH>
                  <TH>Scopes</TH>
                  <TH>状态</TH>
                  <TH>最后使用</TH>
                  <TH className="text-right">操作</TH>
                </TR>
              </THead>
              <TBody>
                {data === null ? (
                  <EmptyRow colSpan={5} text="加载中…" />
                ) : data.keys.length === 0 ? (
                  <EmptyRow colSpan={5} text="该用户尚无 Key" />
                ) : (
                  data.keys.map((k) => (
                    <TR key={k.id}>
                      <TD className="font-medium">
                        {k.label || <span className="text-subtle">（无）</span>}
                        <div className="font-mono text-[10px] text-subtle">{shortId(k.id, 12)}</div>
                      </TD>
                      <TD><ScopeBadgeList scopes={k.scopes} /></TD>
                      <TD>
                        {k.revoked_at ? (
                          <Badge variant="outline">已撤销</Badge>
                        ) : (
                          <Badge variant="success">活跃</Badge>
                        )}
                      </TD>
                      <TD className="text-xs text-subtle">{formatDate(k.last_used_at)}</TD>
                      <TD className="text-right">
                        {!k.revoked_at && (
                          <Button variant="ghost" size="sm" onClick={() => setRevokeTarget(k)}>
                            <Trash2 className="h-3.5 w-3.5" /> 撤销
                          </Button>
                        )}
                      </TD>
                    </TR>
                  ))
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardContent>
            <div className="mb-3 text-sm font-semibold">数据库 ({data?.databases.length ?? "…"})</div>
            <Table>
              <THead>
                <TR>
                  <TH>名称</TH>
                  <TH>db_id</TH>
                  <TH>状态</TH>
                  <TH className="text-right">进入</TH>
                </TR>
              </THead>
              <TBody>
                {data === null ? (
                  <EmptyRow colSpan={4} text="加载中…" />
                ) : data.databases.length === 0 ? (
                  <EmptyRow colSpan={4} text="该用户尚无库" />
                ) : (
                  data.databases.map((d) => (
                    <TR key={d.db_id}>
                      <TD className="font-medium">{d.display_name}</TD>
                      <TD className="font-mono text-xs text-subtle">{shortId(d.db_id, 16)}</TD>
                      <TD>
                        <Badge variant={d.status === "active" ? "success" : "outline"}>
                          {d.status === "active" ? "活跃" : "归档"}
                        </Badge>
                      </TD>
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
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={issuing}
        onClose={() => !busy && setIssuing(false)}
        title={`为 ${data?.user.name ?? "用户"} 签发新 Key`}
        description="管理员签发不受 caller scope 限制，可签发任意 scope 包括 admin。"
      >
        <div className="space-y-4">
          <div>
            <Label htmlFor="lbl2">标签</Label>
            <Input id="lbl2" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="laptop / mobile / ci" />
          </div>
          <div>
            <Label>Scopes</Label>
            <ScopeChecklist
              value={scopes}
              onChange={setScopes}
              allowed={["r", "w", "admin"]}
              showAdmin
            />
          </div>
        </div>
        <DialogActions>
          <Button variant="ghost" onClick={() => setIssuing(false)} disabled={busy}>取消</Button>
          <Button onClick={onIssue} loading={busy} disabled={scopes.length === 0}>签发</Button>
        </DialogActions>
      </Dialog>

      <OneTimeTokenDialog
        open={!!token}
        token={token?.token ?? null}
        keyLabel={token?.label}
        onClose={() => setToken(null)}
      />

      <ConfirmDialog
        open={!!revokeTarget}
        onCancel={() => setRevokeTarget(null)}
        onConfirm={onRevoke}
        loading={busy}
        title="撤销 API Key"
        description={`此操作不可恢复，撤销后 ${revokeTarget?.label || revokeTarget?.id?.slice(0, 8)} 将立即失效。`}
        confirmText="撤销"
        danger
      />
    </Layout>
  );
}
