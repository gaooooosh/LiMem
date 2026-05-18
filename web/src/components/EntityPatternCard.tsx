// 注册实体 Pattern 内联编辑器（v2 单文档 markdown）。
// 替换原 PatternsDrawer：去掉 list / 搜索 / 含归档 / pattern_type / metadata / status，
// 收敛为：load → textarea → 保存(PUT) / 删除(DELETE)。
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import { DangerConfirmDialog } from "@/components/DangerConfirmDialog";
import { toast } from "@/components/Toaster";
import { entityPatternApi } from "@/api/client";
import { formatDate } from "@/lib/utils";
import type { RegisteredEntity } from "@/api/types";
import { Save, Trash2 } from "lucide-react";

interface Props {
  dbId: string;
  entity: RegisteredEntity;
  canWrite: boolean;
}

const PLACEHOLDER = `## 偏好
- 喜欢咖啡，不喝牛奶

## 习惯
- 周二例会 10:00`;

export function EntityPatternCard({ dbId, entity, canWrite }: Props) {
  const [serverContent, setServerContent] = useState<string>("");
  const [draft, setDraft] = useState<string>("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [hasPattern, setHasPattern] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await entityPatternApi.get(dbId, entity.id);
      const text = r.pattern ? r.content : "";
      setServerContent(text);
      setDraft(text);
      setUpdatedAt(r.pattern?.updated_at ?? null);
      setHasPattern(!!r.pattern);
    } catch {
      // toast 已在 api<T> 中弹
    } finally {
      setLoading(false);
    }
  }, [dbId, entity.id]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = draft !== serverContent;

  const onSave = async () => {
    if (!canWrite || !dirty) return;
    if (!draft.trim()) {
      toast.error("content 不能为空，删除请用右上角按钮");
      return;
    }
    setSaving(true);
    try {
      const r = await entityPatternApi.put(dbId, entity.id, draft);
      setServerContent(r.pattern.content);
      setUpdatedAt(r.pattern.updated_at ?? null);
      setHasPattern(true);
      toast.success(r.action === "created" ? "Pattern 已创建" : "Pattern 已更新");
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    setDeleting(true);
    try {
      await entityPatternApi.remove(dbId, entity.id);
      setServerContent("");
      setDraft("");
      setUpdatedAt(null);
      setHasPattern(false);
      setConfirmDelete(false);
      toast.success("Pattern 已删除");
    } catch {
      // ignore
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs text-subtle">
          {hasPattern && updatedAt
            ? `更新于 ${formatDate(new Date(updatedAt * 1000).toISOString())}`
            : "尚未创建 Pattern"}
        </div>
        {hasPattern && canWrite && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmDelete(true)}
            disabled={saving || deleting}
          >
            <Trash2 className="h-3.5 w-3.5" /> 删除
          </Button>
        )}
      </div>

      <Textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={!canWrite || loading}
        rows={16}
        placeholder={PLACEHOLDER}
        className="min-h-[320px]"
      />

      <div className="flex items-center justify-between text-xs text-subtle">
        <div>{draft.length} 字符</div>
        <Button
          size="sm"
          onClick={onSave}
          loading={saving}
          disabled={!canWrite || !dirty || saving}
        >
          <Save className="h-3.5 w-3.5" /> 保存
        </Button>
      </div>

      <DangerConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={onDelete}
        loading={deleting}
        title="删除该实体的 Pattern？"
        description="此操作不可恢复，将清空该实体的 markdown 文档。"
        confirmPhrase={entity.id}
        inputLabel="请输入 entity_id"
        confirmText="永久删除"
      />
    </div>
  );
}
