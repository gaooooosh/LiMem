// 注册实体 Pattern 的查看 / 编辑抽屉：q 搜索 + include_inactive + 全字段编辑
import { useCallback, useEffect, useMemo, useState } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Badge } from "@/components/ui/Badge";
import { Table, THead, TBody, TR, TH, TD, EmptyRow, SkeletonRow } from "@/components/ui/Table";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DangerConfirmDialog } from "@/components/DangerConfirmDialog";
import { toast } from "@/components/Toaster";
import { entityPatternApi } from "@/api/client";
import { formatDate } from "@/lib/utils";
import type {
  EntityPattern,
  EntityPatternStatus,
  RegisteredEntity,
  UpdateEntityPatternRequest,
} from "@/api/types";
import { Plus, RefreshCcw, Search } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  dbId: string;
  entity: RegisteredEntity | null;
  canWrite: boolean;
}

type FormMode = "idle" | "create" | "edit";

interface PatternForm {
  pattern_id: string; // 仅 create 时使用；为空则后端自动生成
  content: string;
  pattern_type: string;
  status: EntityPatternStatus;
  metadataJson: string;
}

const EMPTY_FORM: PatternForm = {
  pattern_id: "",
  content: "",
  pattern_type: "preference",
  status: "active",
  metadataJson: "",
};

export function PatternsDrawer({ open, onClose, dbId, entity, canWrite }: Props) {
  const [items, setItems] = useState<EntityPattern[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);

  const [mode, setMode] = useState<FormMode>("idle");
  const [form, setForm] = useState<PatternForm>(EMPTY_FORM);
  const [editTarget, setEditTarget] = useState<EntityPattern | null>(null);

  const [archiveTarget, setArchiveTarget] = useState<EntityPattern | null>(null);
  const [hardDeleteTarget, setHardDeleteTarget] = useState<EntityPattern | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  const entityId = entity?.id ?? "";

  const refresh = useCallback(
    async (override?: { q?: string; include_inactive?: boolean }) => {
      if (!dbId || !entityId) return;
      setLoading(true);
      try {
        const r = await entityPatternApi.list(dbId, entityId, {
          q: override?.q ?? q,
          include_inactive: override?.include_inactive ?? includeInactive,
        });
        setItems(r.items);
      } catch {
        // api<T> 已统一 toast
      } finally {
        setLoading(false);
      }
    },
    [dbId, entityId, q, includeInactive],
  );

  // 抽屉打开 / 实体切换：重置状态并拉取数据
  useEffect(() => {
    if (!open || !entityId) return;
    setMode("idle");
    setEditTarget(null);
    setForm(EMPTY_FORM);
    setQ("");
    setIncludeInactive(false);
    refresh({ q: "", include_inactive: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, entityId]);

  const startCreate = () => {
    if (!canWrite) return;
    setMode("create");
    setEditTarget(null);
    setForm(EMPTY_FORM);
  };

  const startEdit = (p: EntityPattern) => {
    if (!canWrite) return;
    setMode("edit");
    setEditTarget(p);
    setForm({
      pattern_id: p.id,
      content: p.content,
      pattern_type: p.pattern_type || "preference",
      status: p.status,
      metadataJson: Object.keys(p.metadata || {}).length
        ? JSON.stringify(p.metadata, null, 2)
        : "",
    });
  };

  const cancelEdit = () => {
    setMode("idle");
    setEditTarget(null);
    setForm(EMPTY_FORM);
  };

  const parseMetadata = (): Record<string, unknown> | null | undefined => {
    const raw = form.metadataJson.trim();
    if (!raw) return undefined;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        toast.error("metadata 必须是 JSON 对象");
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      toast.error("metadata 不是合法 JSON");
      return null;
    }
  };

  const submit = async () => {
    if (!canWrite || !entityId) return;
    if (!form.content.trim()) {
      toast.error("content 必填");
      return;
    }
    const metadata = parseMetadata();
    if (metadata === null) return; // 解析失败已 toast

    setBusy(true);
    try {
      if (mode === "create") {
        const r = await entityPatternApi.create(dbId, entityId, {
          content: form.content,
          pattern_type: form.pattern_type || "preference",
          metadata: metadata ?? undefined,
          pattern_id: form.pattern_id.trim() || undefined,
        });
        toast.success(`已创建：${r.pattern.id}`);
      } else if (mode === "edit" && editTarget) {
        const body: UpdateEntityPatternRequest = {};
        if (form.content !== editTarget.content) body.content = form.content;
        if (form.pattern_type !== editTarget.pattern_type) body.pattern_type = form.pattern_type;
        if (form.status !== editTarget.status) body.status = form.status;
        // metadata 整体覆盖（与后端语义一致）：用户清空 → 不传；非空 → 传解析结果
        if (metadata !== undefined) body.metadata = metadata;
        if (Object.keys(body).length === 0) {
          toast.info("没有变化");
          setBusy(false);
          return;
        }
        await entityPatternApi.update(dbId, entityId, editTarget.id, body);
        toast.success(`已更新：${editTarget.id}`);
      }
      await refresh();
      cancelEdit();
    } catch {
      // api<T> 已统一 toast
    } finally {
      setBusy(false);
    }
  };

  const confirmArchive = async () => {
    if (!archiveTarget || !entityId) return;
    setConfirmBusy(true);
    try {
      await entityPatternApi.remove(dbId, entityId, archiveTarget.id, false);
      toast.success(`已归档：${archiveTarget.id}`);
      setArchiveTarget(null);
      await refresh();
      // 若正在编辑被归档的条目，回收到 idle
      if (editTarget && editTarget.id === archiveTarget.id) cancelEdit();
    } catch {
      // toast handled
    } finally {
      setConfirmBusy(false);
    }
  };

  const confirmHardDelete = async () => {
    if (!hardDeleteTarget || !entityId) return;
    setConfirmBusy(true);
    try {
      await entityPatternApi.remove(dbId, entityId, hardDeleteTarget.id, true);
      toast.success(`已永久删除：${hardDeleteTarget.id}`);
      setHardDeleteTarget(null);
      await refresh();
      if (editTarget && editTarget.id === hardDeleteTarget.id) cancelEdit();
    } catch {
      // toast handled
    } finally {
      setConfirmBusy(false);
    }
  };

  const title = useMemo(() => {
    if (!entity) return "Patterns";
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span>Patterns of</span>
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-sm">{entity.id}</code>
        <Badge variant="outline">{entity.type || "UNKNOWN"}</Badge>
      </span>
    );
  }, [entity]);

  return (
    <>
      <Drawer
        open={open && !!entity}
        onClose={onClose}
        title={title}
        description={entity?.description}
        widthClassName="max-w-3xl"
      >
        <div className="space-y-4">
          {/* 工具栏：q + include_inactive + 刷新 + 新建 */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-1 items-center gap-2">
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") refresh();
                }}
                placeholder="按 content / pattern_type / metadata 子串搜索（不区分大小写）"
                className="flex-1"
              />
              <Button variant="ghost" size="sm" onClick={() => refresh()} loading={loading}>
                <Search className="h-3.5 w-3.5" /> 搜索
              </Button>
            </div>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-subtle">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => {
                  const v = e.target.checked;
                  setIncludeInactive(v);
                  refresh({ include_inactive: v });
                }}
              />
              含归档
            </label>
            <Button variant="ghost" size="sm" onClick={() => refresh()} loading={loading}>
              <RefreshCcw className="h-3.5 w-3.5" /> 刷新
            </Button>
            <Button size="sm" onClick={startCreate} disabled={!canWrite || busy}>
              <Plus className="h-3.5 w-3.5" /> 新建
            </Button>
          </div>

          {/* 列表 */}
          <Table>
            <THead>
              <TR>
                <TH>id</TH>
                <TH>type</TH>
                <TH>status</TH>
                <TH>content</TH>
                <TH>updated</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading && <SkeletonRow colSpan={6} />}
              {!loading && items.length === 0 && (
                <EmptyRow colSpan={6} text="暂无匹配 pattern，可点击右上角「新建」" />
              )}
              {!loading &&
                items.map((p) => (
                  <TR key={p.id}>
                    <TD className="font-mono text-xs">{p.id}</TD>
                    <TD>{p.pattern_type}</TD>
                    <TD>
                      <Badge variant={p.status === "active" ? "success" : "outline"} dot>
                        {p.status}
                      </Badge>
                    </TD>
                    <TD className="max-w-[260px] truncate" title={p.content}>
                      {p.content}
                    </TD>
                    <TD>
                      {p.updated_at
                        ? formatDate(new Date(p.updated_at * 1000).toISOString())
                        : "-"}
                    </TD>
                    <TD>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!canWrite || busy}
                          onClick={() => startEdit(p)}
                        >
                          编辑
                        </Button>
                        {p.status === "active" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!canWrite || busy}
                            onClick={() => setArchiveTarget(p)}
                          >
                            归档
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!canWrite || busy}
                          onClick={() => setHardDeleteTarget(p)}
                        >
                          硬删
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
            </TBody>
          </Table>

          {/* 编辑/新建表单 */}
          {mode !== "idle" && (
            <div className="rounded-xl border border-border bg-panel-soft p-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-sm font-semibold">
                  {mode === "create" ? "新建 pattern" : `编辑：${editTarget?.id ?? ""}`}
                </div>
                <Button variant="ghost" size="sm" onClick={cancelEdit} disabled={busy}>
                  取消
                </Button>
              </div>

              <div className="space-y-3">
                {mode === "create" && (
                  <div>
                    <Label htmlFor="pid">pattern_id（可选，留空自动生成）</Label>
                    <Input
                      id="pid"
                      value={form.pattern_id}
                      disabled={!canWrite}
                      onChange={(e) => setForm((f) => ({ ...f, pattern_id: e.target.value }))}
                      placeholder="如 pref_music"
                    />
                  </div>
                )}
                <div>
                  <Label htmlFor="pcontent">content *</Label>
                  <Textarea
                    id="pcontent"
                    rows={3}
                    value={form.content}
                    disabled={!canWrite}
                    onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
                    placeholder="该实体下的偏好/规则文本"
                  />
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <Label htmlFor="ptype">pattern_type</Label>
                    <Input
                      id="ptype"
                      value={form.pattern_type}
                      disabled={!canWrite}
                      onChange={(e) => setForm((f) => ({ ...f, pattern_type: e.target.value }))}
                      placeholder="preference / rule / taboo / habit"
                    />
                  </div>
                  <div>
                    <Label htmlFor="pstatus">status</Label>
                    <select
                      id="pstatus"
                      value={form.status}
                      disabled={!canWrite || mode === "create"}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, status: e.target.value as EntityPatternStatus }))
                      }
                      className="block h-9 w-full rounded-md border border-border bg-panel px-2 text-sm"
                    >
                      <option value="active">active</option>
                      <option value="archived">archived</option>
                    </select>
                  </div>
                </div>
                <div>
                  <Label htmlFor="pmeta">metadata（JSON 对象，可选；整体覆盖）</Label>
                  <Textarea
                    id="pmeta"
                    rows={3}
                    value={form.metadataJson}
                    disabled={!canWrite}
                    onChange={(e) => setForm((f) => ({ ...f, metadataJson: e.target.value }))}
                    placeholder='{"source":"manual","confidence":1.0}'
                  />
                </div>
                <div className="flex justify-end">
                  <Button onClick={submit} loading={busy} disabled={!canWrite || busy}>
                    {mode === "create" ? "创建" : "保存"}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </Drawer>

      {/* 归档确认 */}
      <ConfirmDialog
        open={!!archiveTarget}
        onCancel={() => setArchiveTarget(null)}
        onConfirm={confirmArchive}
        title="归档该 pattern？"
        description={archiveTarget ? `id=${archiveTarget.id}；归档后默认列表不再展示，可通过「含归档」复选查看。` : ""}
        confirmText="归档"
        loading={confirmBusy}
      />

      {/* 硬删确认 */}
      <DangerConfirmDialog
        open={!!hardDeleteTarget}
        onCancel={() => setHardDeleteTarget(null)}
        onConfirm={confirmHardDelete}
        title="永久删除该 pattern？"
        description={hardDeleteTarget ? `不可恢复。请输入 pattern_id 解锁：` : ""}
        confirmPhrase={hardDeleteTarget?.id ?? ""}
        inputLabel="请输入 pattern_id"
        loading={confirmBusy}
      />
    </>
  );
}
