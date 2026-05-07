import type { ReactNode } from "react";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Button } from "./ui/Button";

interface Props {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
  title: ReactNode;
  description?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  loading?: boolean;
}

export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  description,
  confirmText = "确认",
  cancelText = "取消",
  danger,
  loading,
}: Props) {
  return (
    <Dialog open={open} onClose={onCancel} title={title} description={description}>
      <DialogActions>
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          {cancelText}
        </Button>
        <Button
          variant={danger ? "danger" : "primary"}
          onClick={onConfirm}
          loading={loading}
        >
          {confirmText}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
