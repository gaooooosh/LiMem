import { useEffect, useState, type ReactNode } from "react";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { Label } from "./ui/Label";

interface Props {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
  title: ReactNode;
  description?: ReactNode;
  /** 用户必须完整输入此字符串才能解锁确认按钮 */
  confirmPhrase: string;
  /** 输入框前缀提示 */
  inputLabel?: string;
  confirmText?: string;
  loading?: boolean;
}

export function DangerConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  description,
  confirmPhrase,
  inputLabel,
  confirmText = "永久删除",
  loading,
}: Props) {
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const ok = typed.trim() === confirmPhrase;
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={
        <span className="flex items-center gap-2 text-danger">⚠ {title}</span>
      }
      description={description}
      dismissOnOverlay={false}
    >
      <div className="space-y-3">
        <div className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          此操作不可撤销，请仔细核对。
        </div>
        <div>
          <Label>
            {inputLabel ?? "请输入"} <code className="rounded bg-muted px-1.5 py-0.5">{confirmPhrase}</code> 以解锁
          </Label>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmPhrase}
            autoFocus
          />
        </div>
      </div>
      <DialogActions>
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          取消
        </Button>
        <Button
          variant="danger"
          onClick={onConfirm}
          disabled={!ok}
          loading={loading}
        >
          {confirmText}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
