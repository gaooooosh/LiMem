import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { useAuth } from "@/auth/AuthContext";
import { Eye, EyeOff, KeyRound } from "lucide-react";

export function LoginPage() {
  const { login, loginError, me } = useAuth();
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "";

  // 已登录直接跳走
  if (me) {
    setTimeout(() => navigate(next || (me.is_root ? "/ui/admin" : "/ui/console"), { replace: true }), 0);
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    try {
      const data = await login(key);
      const dest = next || (data.is_root || data.scopes.includes("admin") ? "/ui/admin" : "/ui/console");
      navigate(dest, { replace: true });
    } catch {
      // 错误已在 AuthContext 中转为 loginError
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center bg-bg p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-md bg-accent text-white">L</span>
            LiMem 控制台登录
          </CardTitle>
          <CardDescription>
            输入您的 API Key 进入控制台。普通用户进入"我的库"，管理员/ROOT 进入管理后台。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <Label htmlFor="key">API Key</Label>
              <div className="relative">
                <Input
                  id="key"
                  type={show ? "text" : "password"}
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder="X-API-Key 或 Bearer token"
                  autoFocus
                  required
                  className="pr-10 font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-subtle hover:bg-muted"
                  aria-label={show ? "隐藏" : "显示"}
                >
                  {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {loginError && (
                <div className="mt-2 text-xs text-danger">{loginError}</div>
              )}
            </div>
            <Button type="submit" className="w-full" loading={busy}>
              <KeyRound className="h-4 w-4" /> 登录
            </Button>
            <p className="text-xs text-subtle">
              没有 Key？请联系管理员通过 <code>/admin/users/&#123;id&#125;/keys</code> 接口签发，或在已有 Key 登录后到"我的 Key"页面自助签发。
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
