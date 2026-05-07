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
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { dbApi, getStoredKey, memoryApi, adminApi } from "@/api/client";
import type { DatabaseView, IngestResponse, QueryResponse } from "@/api/types";
import { useAuth, hasScope } from "@/auth/AuthContext";
import { formatDate } from "@/lib/utils";
import { toast } from "@/components/Toaster";
import { ArrowLeft, ChartBar, Database as DBIcon, FileSearch, GitGraph, Send, Sparkles } from "lucide-react";

type TabKey = "overview" | "ingest" | "query" | "graph" | "logs" | "evolve";

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
        <Link to="/ui/console" className="inline-flex items-center gap-1 text-sm text-subtle hover:text-text">
          <ArrowLeft className="h-3.5 w-3.5" /> 返回库列表
        </Link>
      </div>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <DBIcon className="h-6 w-6 text-accent" />
            {meta?.display_name ?? dbId}
            {meta && (
              <Badge variant={meta.status === "active" ? "success" : "outline"}>
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
          <TabsTrigger value="logs">审计日志</TabsTrigger>
          <TabsTrigger value="evolve" disabled={!canWrite}><Sparkles className="h-3.5 w-3.5" /> 演化</TabsTrigger>
        </TabsList>

        <TabsContent value="overview"><OverviewTab dbId={dbId} /></TabsContent>
        <TabsContent value="ingest"><IngestTab dbId={dbId} /></TabsContent>
        <TabsContent value="query"><QueryTab dbId={dbId} /></TabsContent>
        <TabsContent value="graph"><GraphTab dbId={dbId} /></TabsContent>
        <TabsContent value="logs"><LogsTab dbId={dbId} /></TabsContent>
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
          <div className="mb-2 text-sm font-semibold">健康检查</div>
          {error ? (
            <div className="text-sm text-danger">{error}</div>
          ) : health === null ? (
            <div className="text-sm text-subtle">加载中…</div>
          ) : (
            <pre className="overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
              {JSON.stringify(health, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <div className="mb-2 text-sm font-semibold">统计</div>
          {stats === null ? (
            <div className="text-sm text-subtle">加载中…</div>
          ) : (
            <pre className="overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
              {JSON.stringify(stats, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
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
      <div className="text-xs uppercase tracking-wide text-subtle">{label}</div>
      <div className={"mt-0.5 text-sm " + (mono ? "font-mono" : "")}>{children}</div>
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
              <ul className="space-y-3">
                {resp.results.map((r) => (
                  <li key={r.event_id} className="rounded-md border border-border p-3">
                    <div className="mb-1.5 flex items-center justify-between">
                      <code className="font-mono text-xs text-subtle">{r.event_id}</code>
                      <Badge variant="accent">score {r.score.toFixed(3)}</Badge>
                    </div>
                    <div className="mb-1 text-sm font-medium">{r.summary}</div>
                    <div className="grid grid-cols-1 gap-1 text-xs text-subtle md:grid-cols-3">
                      <div><strong>action:</strong> {r.action || "—"}</div>
                      <div><strong>causality:</strong> {r.causality || "—"}</div>
                      <div><strong>ts:</strong> {r.timestamp ? new Date(r.timestamp).toLocaleString("zh-CN") : "—"}</div>
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
          className="block h-[78vh] w-full rounded-lg"
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
          className="block h-[78vh] w-full rounded-lg"
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
            <pre ref={ref} className="overflow-auto rounded-md bg-muted p-3 text-xs">{last}</pre>
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
