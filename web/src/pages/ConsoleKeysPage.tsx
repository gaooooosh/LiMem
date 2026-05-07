import { useEffect, useMemo, useState } from "react";
import { Layout, PageHeader } from "@/components/Layout";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Badge } from "@/components/ui/Badge";
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
import { ScopeBadgeList } from "@/components/ScopeBadge";
import { ScopeChecklist } from "@/components/ScopeChecklist";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OneTimeTokenDialog } from "@/components/OneTimeTokenDialog";
import { Plus, Trash2, KeyRound } from "lucide-react";
import { meApi } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import type { ApiKeyView, Scope } from "@/api/types";
import { formatDate, shortId } from "@/lib/utils";
import { toast } from "@/components/Toaster";

export function ConsoleKeysPage() {
  const { me } = useAuth();
  const [list, setList] = useState<ApiKeyView[] | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [label, setLabel] = useState("");
  const [chosen, setChosen] = useState<Scope[]>(["r"]);
  const [busy, setBusy] = useState(false);
  const [issuedToken, setIssuedToken] = useState<{ token: string; label: string } | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiKeyView | null>(null);

  // ROOT 没有可管理的 SQL key；使用 /me/keys 接口，root 时返回 []
  const allowedScopes: Scope[] = useMemo(() => {
    if (!me) return [];
    return me.scopes;
  }, [me]);

  const load = async () => {
    setList(null);
    try {
      setList(await meApi.listKeys());
    } catch {
      setList([]);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openIssue = () => {
    setLabel("");
    setChosen(allowedScopes.includes("r") ? ["r"] : [allowedScopes[0]].filter(Boolean) as Scope[]);
    setIssuing(true);
  };

  const onIssue = async () => {
    if (chosen.length === 0) {
      toast.warning("至少选择一个 scope");
      return;
    }
    setBusy(true);
    try {
      const r = await meApi.issueKey(label.trim(), chosen.join(","));
      setIssuing(false);
      setIssuedToken({ token: r.token, label: r.key.label });
      load();
    } catch {
      // toast 已弹
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async () => {
    if (!revokeTarget) return;
    setBusy(true);
    try {
      await meApi.revokeKey(revokeTarget.id);
      toast.success("已撤销");
      setRevokeTarget(null);
      load();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  if (me?.is_root) {
    return (
      <Layout>
        <PageHeader eyebrow="控制台 · 凭据" title="我的 API Key" />
        <div className="rounded-xl border border-warning/30 bg-warning-soft/60 p-4 text-sm leading-relaxed text-warning shadow-soft">
          您正以 ROOT 身份登录，ROOT_API_KEY 通过环境变量管理，无法在此自助签发或撤销。
          请前往{" "}
          <a className="font-medium underline underline-offset-2" href="/ui/admin/users">
            用户管理
          </a>{" "}
          创建具名 admin user。
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <PageHeader
        eyebrow="控制台 · 凭据"
        title="我的 API Key"
        description="每个 Key 有独立的 scope；签发后明文 Token 仅显示一次。"
        actions={
          <Button onClick={openIssue} disabled={allowedScopes.length === 0}>
            <Plus className="h-4 w-4" /> 签发新 Key
          </Button>
        }
      />

      <div className="mb-5 flex flex-wrap items-center gap-2 rounded-xl border border-border/70 bg-panel-soft px-4 py-3 text-xs text-subtle shadow-soft">
        <KeyRound className="h-3.5 w-3.5 text-accent" />
        <span>当前 Key scope:</span>
        <ScopeBadgeList scopes={me?.scopes ?? []} />
        <span className="opacity-60">·</span>
        <span>签发新 Key 时无法选择超出此范围的 scope（防止自我提权）。</span>
      </div>

      <Table>
        <THead>
          <TR>
            <TH>Label</TH>
            <TH>key_id</TH>
            <TH>Scopes</TH>
            <TH>状态</TH>
            <TH>创建</TH>
            <TH>最后使用</TH>
            <TH className="text-right">操作</TH>
          </TR>
        </THead>
        <TBody>
          {list === null ? (
            <SkeletonRow colSpan={7} rows={4} />
          ) : list.length === 0 ? (
            <EmptyRow
              colSpan={7}
              text="尚无 Key"
              icon={<KeyRound className="h-5 w-5" />}
            />
          ) : (
            list.map((k) => {
              const isCurrent = k.id === me?.key_id;
              return (
                <TR key={k.id}>
                  <TD className="font-medium">
                    {k.label || <span className="text-subtle">（无）</span>}
                    {isCurrent && (
                      <Badge variant="accent" className="ml-2">本次登录</Badge>
                    )}
                  </TD>
                  <TD className="font-mono text-xs text-subtle">
                    {shortId(k.id, 12)}
                  </TD>
                  <TD><ScopeBadgeList scopes={k.scopes} /></TD>
                  <TD>
                    {k.revoked_at ? (
                      <Badge variant="outline">已撤销</Badge>
                    ) : (
                      <Badge variant="success" dot>活跃</Badge>
                    )}
                  </TD>
                  <TD className="text-xs text-subtle">{formatDate(k.created_at)}</TD>
                  <TD className="text-xs text-subtle">{formatDate(k.last_used_at)}</TD>
                  <TD className="text-right">
                    {!k.revoked_at && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRevokeTarget(k)}
                        title={isCurrent ? "撤销当前 Key 后会立即登出" : ""}
                      >
                        <Trash2 className="h-3.5 w-3.5" /> 撤销
                      </Button>
                    )}
                  </TD>
                </TR>
              );
            })
          )}
        </TBody>
      </Table>

      <Dialog
        open={issuing}
        onClose={() => !busy && setIssuing(false)}
        title="签发新 API Key"
        description="为新 Key 选择标签和 scope。明文 Token 仅签发后展示一次。"
      >
        <div className="space-y-4">
          <div>
            <Label htmlFor="lbl">标签（label）</Label>
            <Input
              id="lbl"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="如：laptop / car-agent / readonly-dashboard"
            />
          </div>
          <div>
            <Label>Scopes</Label>
            <ScopeChecklist
              value={chosen}
              onChange={setChosen}
              allowed={allowedScopes}
              showAdmin={allowedScopes.includes("admin")}
            />
          </div>
        </div>
        <DialogActions>
          <Button variant="ghost" onClick={() => setIssuing(false)} disabled={busy}>取消</Button>
          <Button onClick={onIssue} loading={busy} disabled={chosen.length === 0}>
            签发
          </Button>
        </DialogActions>
      </Dialog>

      <OneTimeTokenDialog
        open={!!issuedToken}
        token={issuedToken?.token ?? null}
        keyLabel={issuedToken?.label}
        onClose={() => setIssuedToken(null)}
      />

      <ConfirmDialog
        open={!!revokeTarget}
        onCancel={() => setRevokeTarget(null)}
        onConfirm={onRevoke}
        loading={busy}
        title="撤销 API Key"
        description={
          <>
            撤销后该 Key 将无法继续使用（不可恢复）。
            {revokeTarget?.id === me?.key_id && (
              <span className="mt-2 block font-medium text-danger">
                ⚠ 这是本次登录所用的 Key，撤销后您将立即被登出。
              </span>
            )}
          </>
        }
        confirmText="撤销"
        danger
      />
    </Layout>
  );
}
