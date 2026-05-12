import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Layout, PageHeader } from "@/components/Layout";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Badge } from "@/components/ui/Badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Table, THead, TBody, TR, TH, TD, EmptyRow, SkeletonRow } from "@/components/ui/Table";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { dbApi, getStoredKey, memoryApi, adminApi, entityApi } from "@/api/client";
import type {
  DatabaseView,
  IngestResponse,
  QueryResponse,
  RegisteredEntity,
  UpdateEntityRequest,
} from "@/api/types";
import { useAuth, hasScope } from "@/auth/AuthContext";
import { formatDate } from "@/lib/utils";
import { toast } from "@/components/Toaster";
import { Skeleton } from "@/components/ui/Skeleton";
import { ArrowLeft, ChartBar, Database as DBIcon, FileSearch, GitGraph, Send, Sparkles, ScrollText, Tags } from "lucide-react";

type TabKey = "overview" | "ingest" | "query" | "graph" | "logs" | "evolve" | "entities";

export function DbDetailPage() {
  const { dbId = "" } = useParams();
  const { me } = useAuth();
  const [tab, setTab] = useState<TabKey>("overview");
  const [meta, setMeta] = useState<DatabaseView | null>(null);
  const canWrite = hasScope(me, "w");

  const loadMeta = async () => {
    // /databases 只列自己；root 走 /admin/databases
    if (me?.is_root || me?.scopes.includes("admin")) {
      try {
        const all = await adminApi.listAllDatabases(true);
        setMeta(all.find((d) => d.db_id === dbId) ?? null);
        return;
      } catch {
        /* fallthrough */
      }
    }
    try {
      const list = await dbApi.list();
      setMeta(list.find((d) => d.db_id === dbId) ?? null);
    } catch {
      setMeta(null);
    }
  };

  useEffect(() => {
    loadMeta();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dbId]);

  return (
    <Layout>
      <div className="mb-3">
        <Link
          to="/ui/console"
          className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-sm text-subtle transition-colors hover:text-text"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> 返回库列表
        </Link>
      </div>
      <PageHeader
        eyebrow="数据库"
        title={
          <span className="flex flex-wrap items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-brand text-white shadow-glow">
              <DBIcon className="h-4 w-4" />
            </span>
            <span className="truncate">{meta?.display_name ?? dbId}</span>
            {meta && (
              <Badge variant={meta.status === "active" ? "success" : "outline"} dot>
                {meta.status === "active" ? "活跃" : "已归档"}
              </Badge>
            )}
          </span>
        }
        description={
          <>
            db_id: <code className="font-mono">{dbId}</code>
            {meta && (
              <>
                {" · "}owner: <code className="font-mono">{meta.owner_user_id}</code>
                {" · "}created: {formatDate(meta.created_at)}
              </>
            )}
          </>
        }
      />

      <Tabs value={tab} onChange={(v) => setTab(v as TabKey)}>
        <TabsList>
          <TabsTrigger value="overview"><ChartBar className="h-3.5 w-3.5" /> 概览</TabsTrigger>
          <TabsTrigger value="ingest" disabled={!canWrite}><Send className="h-3.5 w-3.5" /> 写入</TabsTrigger>
          <TabsTrigger value="query"><FileSearch className="h-3.5 w-3.5" /> 查询</TabsTrigger>
          <TabsTrigger value="graph"><GitGraph className="h-3.5 w-3.5" /> 图谱</TabsTrigger>
          <TabsTrigger value="logs"><ScrollText className="h-3.5 w-3.5" /> 审计日志</TabsTrigger>
          <TabsTrigger value="entities"><Tags className="h-3.5 w-3.5" /> 实体注册</TabsTrigger>
          <TabsTrigger value="evolve" disabled={!canWrite}><Sparkles className="h-3.5 w-3.5" /> 演化</TabsTrigger>
        </TabsList>

        <TabsContent value="overview"><OverviewTab dbId={dbId} /></TabsContent>
        <TabsContent value="ingest"><IngestTab dbId={dbId} /></TabsContent>
        <TabsContent value="query"><QueryTab dbId={dbId} /></TabsContent>
        <TabsContent value="graph"><GraphTab dbId={dbId} /></TabsContent>
        <TabsContent value="logs"><LogsTab dbId={dbId} /></TabsContent>
        <TabsContent value="entities"><EntitiesTab dbId={dbId} canWrite={canWrite} /></TabsContent>
        <TabsContent value="evolve"><EvolveTab dbId={dbId} /></TabsContent>
      </Tabs>
    </Layout>
  );
}

// ----- 概览 -----
function OverviewTab({ dbId }: { dbId: string }) {
  const [health, setHealth] = useState<unknown>(null);
  const [stats, setStats] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [h, s] = await Promise.all([memoryApi.health(dbId), memoryApi.stats(dbId)]);
        if (!alive) return;
        setHealth(h);
        setStats(s);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "加载失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, [dbId]);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardContent>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold">健康检查</div>
            {health !== null && !error && (
              <Badge variant="success" dot>OK</Badge>
            )}
          </div>
          {error ? (
            <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </div>
          ) : health === null ? (
            <SkeletonBlock />
          ) : (
            <JsonViewer data={health} />
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <div className="mb-3 text-sm font-semibold">统计</div>
          {stats === null ? (
            <SkeletonBlock />
          ) : (
            <JsonViewer data={stats} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SkeletonBlock() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-3.5 w-full" />
      <Skeleton className="h-3.5 w-5/6" />
      <Skeleton className="h-3.5 w-2/3" />
    </div>
  );
}

/** 把对象渲染成 key/value 列表；非对象退回 pre */
function JsonViewer({ data }: { data: unknown }) {
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const entries = Object.entries(data as Record<string, unknown>);
    return (
      <dl className="divide-y divide-border/60 rounded-lg border border-border/60 bg-bg-soft">
        {entries.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[120px_1fr] gap-2 px-3 py-2 text-xs">
            <dt className="truncate font-medium text-subtle">{k}</dt>
            <dd className="break-all font-mono text-text">
              {typeof v === "object"
                ? JSON.stringify(v)
                : String(v ?? "—")}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <pre className="overflow-auto rounded-lg border border-border/60 bg-bg-soft p-3 text-xs leading-relaxed">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ----- 写入 -----
function IngestTab({ dbId }: { dbId: string }) {
  const [text, setText] = useState("");
  const [tsStr, setTsStr] = useState("");
  const [busy, setBusy] = useState(false);
  const [resp, setResp] = useState<IngestResponse | null>(null);

  const onSubmit = async () => {
    if (!text.trim()) return;
    let payload: unknown = text;
    try {
      payload = JSON.parse(text);
    } catch {
      // 非 JSON 时按字符串提交
    }
    const ts = tsStr.trim() ? Number(tsStr.trim()) : undefined;
    if (tsStr.trim() && !Number.isFinite(ts)) {
      toast.error("时间戳必须是整数（毫秒或秒）");
      return;
    }
    setBusy(true);
    try {
      const r = await memoryApi.ingest(dbId, payload, ts);
      setResp(r);
      toast.success(`已写入：${r.summary.slice(0, 40)}`);
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="data">事件数据（JSON 或纯文本）</Label>
            <Textarea
              id="data"
              rows={12}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={'{\n  "speaker": "user",\n  "text": "今天上午我去了健身房"\n}\n或直接写一段对话文本'}
            />
          </div>
          <div>
            <Label htmlFor="ts">时间戳（可选，整数）</Label>
            <Input
              id="ts"
              value={tsStr}
              onChange={(e) => setTsStr(e.target.value)}
              placeholder="留空使用服务器当前时间"
              inputMode="numeric"
            />
          </div>
          <Button onClick={onSubmit} loading={busy} disabled={!text.trim()}>
            <Send className="h-4 w-4" /> 写入事件
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <div className="mb-2 text-sm font-semibold">最近一次响应</div>
          {!resp ? (
            <div className="text-sm text-subtle">写入后这里展示返回的 event_id / summary / 实体数等</div>
          ) : (
            <div className="space-y-3 text-sm">
              <Field label="event_id" mono>{resp.event_id}</Field>
              <Field label="summary">{resp.summary}</Field>
              <div className="flex flex-wrap gap-2">
                <Badge variant={resp.is_new ? "success" : "outline"}>
                  {resp.is_new ? "新事件" : "合并到已有"}
                </Badge>
                <Badge variant="accent">实体 +{resp.entities_created}</Badge>
                <Badge variant="default">事件总数 {resp.event_count}</Badge>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-subtle">
        {label}
      </div>
      <div className={"mt-0.5 text-sm " + (mono ? "font-mono break-all" : "")}>
        {children}
      </div>
    </div>
  );
}

function KV({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="font-semibold text-text-soft">{label}:</span>
      <span className="truncate">{value || "—"}</span>
    </div>
  );
}

// ----- 查询 -----
function QueryTab({ dbId }: { dbId: string }) {
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(5);
  const [busy, setBusy] = useState(false);
  const [resp, setResp] = useState<QueryResponse | null>(null);

  const onSubmit = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try {
      const r = await memoryApi.query(dbId, q.trim(), topK);
      setResp(r);
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Label htmlFor="q">查询语句</Label>
              <Input
                id="q"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onSubmit()}
                placeholder="如：用户最近一次去健身房做了什么？"
              />
            </div>
            <div className="w-32">
              <Label htmlFor="topk">top_k</Label>
              <Input
                id="topk"
                type="number"
                min={1}
                max={50}
                value={topK}
                onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              />
            </div>
            <Button onClick={onSubmit} loading={busy} disabled={!q.trim()}>
              <FileSearch className="h-4 w-4" /> 查询
            </Button>
          </div>
        </CardContent>
      </Card>

      {resp && (
        <Card>
          <CardContent>
            <div className="mb-3 flex items-center justify-between text-sm text-subtle">
              <span>共返回 {resp.results.length} 条；total={resp.total}</span>
            </div>
            {resp.results.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-6 text-center text-subtle">
                未命中任何记忆
              </div>
            ) : (
              <ul className="space-y-2.5">
                {resp.results.map((r) => (
                  <li
                    key={r.event_id}
                    className="group/result rounded-xl border border-border/70 bg-panel-soft px-4 py-3 shadow-soft transition-colors hover:border-accent/30 hover:bg-panel"
                  >
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <code className="truncate font-mono text-[11px] text-subtle">
                        {r.event_id}
                      </code>
                      <Badge variant="accent" dot>
                        score {r.score.toFixed(3)}
                      </Badge>
                    </div>
                    <div className="mb-2 text-sm font-medium leading-relaxed">
                      {r.summary}
                    </div>
                    <div className="grid grid-cols-1 gap-1.5 text-xs text-subtle md:grid-cols-3">
                      <KV label="action" value={r.action} />
                      <KV label="causality" value={r.causality} />
                      <KV
                        label="ts"
                        value={
                          r.timestamp
                            ? new Date(r.timestamp).toLocaleString("zh-CN")
                            : null
                        }
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ----- 图谱（iframe 嵌入 /graph） -----
function GraphTab({ dbId }: { dbId: string }) {
  const key = getStoredKey() ?? "";
  const src = useMemo(
    () => `/graph?db=${encodeURIComponent(dbId)}&key=${encodeURIComponent(key)}`,
    [dbId, key],
  );
  return (
    <Card>
      <CardContent className="p-0">
        <iframe
          title="知识图谱"
          src={src}
          className="block h-[78vh] w-full rounded-xl bg-bg-soft"
          sandbox="allow-scripts allow-same-origin allow-popups"
        />
      </CardContent>
    </Card>
  );
}

// ----- 日志（iframe 嵌入 /logs） -----
function LogsTab({ dbId }: { dbId: string }) {
  const key = getStoredKey() ?? "";
  const src = useMemo(
    () => `/logs?db=${encodeURIComponent(dbId)}&key=${encodeURIComponent(key)}`,
    [dbId, key],
  );
  return (
    <Card>
      <CardContent className="p-0">
        <iframe
          title="审计日志"
          src={src}
          className="block h-[78vh] w-full rounded-xl bg-bg-soft"
          sandbox="allow-scripts allow-same-origin"
        />
      </CardContent>
    </Card>
  );
}

// ----- 演化与索引重建 -----
function EvolveTab({ dbId }: { dbId: string }) {
  const [confirmEvolve, setConfirmEvolve] = useState(false);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<string>("");
  const ref = useRef<HTMLPreElement>(null);

  const runEvolve = async () => {
    setBusy(true);
    try {
      const r = await memoryApi.evolve(dbId);
      setLast(JSON.stringify(r, null, 2));
      toast.success(r.message || "演化完成");
    } finally {
      setBusy(false);
      setConfirmEvolve(false);
    }
  };

  const runRebuild = async () => {
    setBusy(true);
    try {
      const r = await memoryApi.rebuildIndex(dbId);
      setLast(JSON.stringify(r, null, 2));
      toast.success(r.message || "索引已重建");
    } finally {
      setBusy(false);
      setConfirmRebuild(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3">
          <div className="text-sm font-semibold">触发演化（evolve）</div>
          <p className="text-sm text-subtle">
            扫描最近写入的事件，执行 Context/Pattern/NEXT 关系增量维护。耗时取决于增量大小。
          </p>
          <Button onClick={() => setConfirmEvolve(true)} loading={busy && confirmEvolve}>
            <Sparkles className="h-4 w-4" /> 立即演化
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="space-y-3">
          <div className="text-sm font-semibold">重建 BM25 全文索引</div>
          <p className="text-sm text-subtle">
            如果查询召回明显异常或近期改写过事件文本，可重建索引。期间该库的查询性能可能下降。
          </p>
          <Button variant="outline" onClick={() => setConfirmRebuild(true)} loading={busy && confirmRebuild}>
            重建索引
          </Button>
        </CardContent>
      </Card>
      {last && (
        <Card>
          <CardContent>
            <div className="mb-2 text-sm font-semibold">上次操作返回</div>
            <pre
              ref={ref}
              className="overflow-auto rounded-lg border border-border/60 bg-bg-soft p-3 text-xs leading-relaxed"
            >
              {last}
            </pre>
          </CardContent>
        </Card>
      )}

      <ConfirmDialog
        open={confirmEvolve}
        onCancel={() => !busy && setConfirmEvolve(false)}
        onConfirm={runEvolve}
        loading={busy}
        title="触发演化"
        description="将对该库执行 Context/Pattern/NEXT 增量维护，可能持续一段时间。"
        confirmText="开始演化"
      />
      <ConfirmDialog
        open={confirmRebuild}
        onCancel={() => !busy && setConfirmRebuild(false)}
        onConfirm={runRebuild}
        loading={busy}
        title="重建 BM25 索引"
        description="期间查询性能可能下降。建议在低峰期执行。"
        confirmText="开始重建"
        danger
      />
    </div>
  );
}

// ----- 实体注册 -----
function EntitiesTab({ dbId, canWrite }: { dbId: string; canWrite: boolean }) {
  const [items, setItems] = useState<RegisteredEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"create" | "edit">("create");
  const [editTarget, setEditTarget] = useState<RegisteredEntity | null>(null);
  const [form, setForm] = useState({
    entity_id: "",
    description: "",
    entity_type: "UNKNOWN",
    aliasesCsv: "",
    metadataJson: "",
  });
  const [addAliasesCsv, setAddAliasesCsv] = useState("");
  const [removeAliasesCsv, setRemoveAliasesCsv] = useState("");

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await entityApi.list(dbId);
      setItems(r.items);
    } catch {
      // api<T> 已统一 toast
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dbId]);

  const resetForm = () => {
    setMode("create");
    setEditTarget(null);
    setForm({ entity_id: "", description: "", entity_type: "UNKNOWN", aliasesCsv: "", metadataJson: "" });
    setAddAliasesCsv("");
    setRemoveAliasesCsv("");
  };

  const onSelect = (e: RegisteredEntity) => {
    setMode("edit");
    setEditTarget(e);
    setForm({
      entity_id: e.id,
      description: e.description,
      entity_type: e.type,
      aliasesCsv: e.aliases.join(","),
      metadataJson: "",
    });
    setAddAliasesCsv("");
    setRemoveAliasesCsv("");
  };

  const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

  const onSubmit = async () => {
    if (!canWrite) return;
    if (mode === "create") {
      if (!form.entity_id.trim() || !form.description.trim()) {
        toast.error("entity_id 与 description 必填");
        return;
      }
    }
    setBusy(true);
    try {
      if (mode === "create") {
        let metadata: Record<string, unknown> | undefined;
        if (form.metadataJson.trim()) {
          try {
            metadata = JSON.parse(form.metadataJson);
          } catch {
            toast.error("metadata 必须是合法 JSON");
            return;
          }
        }
        const r = await entityApi.register(dbId, {
          entity_id: form.entity_id.trim(),
          description: form.description,
          entity_type: form.entity_type || "UNKNOWN",
          aliases: csv(form.aliasesCsv),
          metadata,
        });
        const verb = r.action === "created" ? "注册" : r.action === "promoted" ? "晋升" : "更新";
        toast.success(`已${verb}：${r.entity.id}`);
      } else if (editTarget) {
        const adds = csv(addAliasesCsv);
        const rms = csv(removeAliasesCsv);
        const body: UpdateEntityRequest = {
          description: form.description !== editTarget.description ? form.description : undefined,
          entity_type: form.entity_type !== editTarget.type ? form.entity_type : undefined,
          add_aliases: adds.length ? adds : undefined,
          remove_aliases: rms.length ? rms : undefined,
        };
        await entityApi.update(dbId, editTarget.id, body);
        toast.success(`已更新：${editTarget.id}`);
      }
      await refresh();
      resetForm();
    } catch {
      // api<T> 已统一 toast
    } finally {
      setBusy(false);
    }
  };

  const submitDisabled =
    !canWrite ||
    busy ||
    (mode === "create" && (!form.entity_id.trim() || !form.description.trim()));

  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      {/* 左：注册实体列表 */}
      <Card>
        <CardContent>
          <div className="mb-2 flex items-center justify-between">
            <div className="text-sm font-semibold">注册实体（{items.length}）</div>
            <Button variant="ghost" size="sm" onClick={refresh} loading={loading}>
              刷新
            </Button>
          </div>
          <Table>
            <THead>
              <TR>
                <TH>id</TH>
                <TH>type</TH>
                <TH>aliases</TH>
                <TH>updated</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading && <SkeletonRow colSpan={5} />}
              {!loading && items.length === 0 && (
                <EmptyRow colSpan={5} text="暂无注册实体，可在右侧表单注册第一个实体" />
              )}
              {!loading &&
                items.map((e) => (
                  <TR key={e.id}>
                    <TD className="font-mono">{e.id}</TD>
                    <TD>{e.type}</TD>
                    <TD className="max-w-[220px] truncate">{e.aliases.join(", ") || "-"}</TD>
                    <TD>
                      {e.updated_at
                        ? formatDate(new Date(e.updated_at * 1000).toISOString())
                        : "-"}
                    </TD>
                    <TD>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!canWrite}
                        onClick={() => onSelect(e)}
                      >
                        编辑
                      </Button>
                    </TD>
                  </TR>
                ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {/* 右：注册/编辑表单 */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold">
              {mode === "create" ? "注册新实体" : `编辑：${editTarget?.id ?? ""}`}
            </div>
            {mode === "edit" && (
              <Button variant="ghost" size="sm" onClick={resetForm} disabled={busy}>
                取消
              </Button>
            )}
          </div>

          <div>
            <Label htmlFor="eid">entity_id *</Label>
            <Input
              id="eid"
              value={form.entity_id}
              disabled={mode === "edit" || !canWrite}
              onChange={(e) => setForm((f) => ({ ...f, entity_id: e.target.value }))}
              placeholder="如 u_alice / proj_apollo"
            />
          </div>
          <div>
            <Label htmlFor="etype">entity_type</Label>
            <Input
              id="etype"
              value={form.entity_type}
              disabled={!canWrite}
              onChange={(e) => setForm((f) => ({ ...f, entity_type: e.target.value }))}
              placeholder="UNKNOWN / person / project / ..."
            />
          </div>
          <div>
            <Label htmlFor="edesc">description *</Label>
            <Textarea
              id="edesc"
              rows={4}
              value={form.description}
              disabled={!canWrite}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="对实体的自然语言描述，用于语义链接"
            />
          </div>

          {mode === "create" ? (
            <>
              <div>
                <Label htmlFor="ealiases">aliases（逗号分隔）</Label>
                <Input
                  id="ealiases"
                  value={form.aliasesCsv}
                  disabled={!canWrite}
                  onChange={(e) => setForm((f) => ({ ...f, aliasesCsv: e.target.value }))}
                  placeholder="alice, 小李"
                />
              </div>
              <div>
                <Label htmlFor="emeta">metadata（JSON，可选）</Label>
                <Textarea
                  id="emeta"
                  rows={3}
                  value={form.metadataJson}
                  disabled={!canWrite}
                  onChange={(e) => setForm((f) => ({ ...f, metadataJson: e.target.value }))}
                  placeholder='{"team": "infra"}'
                />
              </div>
            </>
          ) : (
            <>
              <div>
                <Label htmlFor="eadd">新增 aliases（逗号分隔）</Label>
                <Input
                  id="eadd"
                  value={addAliasesCsv}
                  disabled={!canWrite}
                  onChange={(e) => setAddAliasesCsv(e.target.value)}
                  placeholder="A.L., Alice Liu"
                />
              </div>
              <div>
                <Label htmlFor="erm">移除 aliases（逗号分隔）</Label>
                <Input
                  id="erm"
                  value={removeAliasesCsv}
                  disabled={!canWrite}
                  onChange={(e) => setRemoveAliasesCsv(e.target.value)}
                  placeholder="留空则不移除"
                />
              </div>
              <div className="text-xs text-subtle">
                当前 aliases：{editTarget?.aliases.join(", ") || "（空）"}
              </div>
            </>
          )}

          <Button onClick={onSubmit} loading={busy} disabled={submitDisabled}>
            <Tags className="h-4 w-4" /> {mode === "create" ? "注册实体" : "保存修改"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
