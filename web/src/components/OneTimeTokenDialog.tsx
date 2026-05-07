import { useState } from "react";
import { Dialog, DialogActions } from "./ui/Dialog";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { Check, Copy, ShieldAlert } from "lucide-react";
import { copyToClipboard } from "@/lib/utils";
import { toast } from "./Toaster";

interface Props {
  open: boolean;
  token: string | null;
  keyLabel?: string;
  onClose: () => void;
}

export function OneTimeTokenDialog({ open, token, keyLabel, onClose }: Props) {
  const [copied, setCopied] = useState(false);
  if (!token) return null;
  const handleCopy = async () => {
    try {
      await copyToClipboard(token);
      setCopied(true);
      toast.success("已复制到剪贴板");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("复制失败，请手动选择复制");
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      dismissOnOverlay={false}
      title={
        <span className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-warning" />
          API Key 签发成功{keyLabel ? `（${keyLabel}）` : ""}
        </span>
      }
      className="max-w-xl"
    >
      <div className="space-y-4">
        <div className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          <strong>此 Token 仅本次显示，关闭后将永远无法再次查看</strong>。请立即复制并妥善保存（如密码管理器）。
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium">明文 Token</div>
          <div className="flex items-center gap-2">
            <Input
              readOnly
              value={token}
              className="font-mono text-xs"
              onFocus={(e) => e.currentTarget.select()}
            />
            <Button onClick={handleCopy} variant={copied ? "secondary" : "primary"}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
        </div>
        <div className="text-xs text-subtle">
          关闭此对话框后，列表中只会显示前 8 位摘要。可用此 Token 通过 <code>X-API-Key</code> Header 调用所有 LiMem 接口。
        </div>
      </div>
      <DialogActions>
        <Button onClick={onClose}>我已保存</Button>
      </DialogActions>
    </Dialog>
  );
}
