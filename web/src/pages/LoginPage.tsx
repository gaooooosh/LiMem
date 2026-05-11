import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { useAuth } from "@/auth/AuthContext";
import { getLastKey } from "@/api/client";
import { Eye, EyeOff, KeyRound, Sparkles } from "lucide-react";

export function LoginPage() {
  const { login, loginError, me } = useAuth();
  // 惰性初始化：进入登录页时预填上次成功登录的 Key（持久化于 localStorage）
  const [key, setKey] = useState<string>(() => getLastKey() ?? "");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "";

  // 已登录直接跳走
  if (me) {
    setTimeout(
      () =>
        navigate(next || (me.is_root ? "/ui/admin" : "/ui/console"), {
          replace: true,
        }),
      0,
    );
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    try {
      const data = await login(key);
      const dest =
        next ||
        (data.is_root || data.scopes.includes("admin") ? "/ui/admin" : "/ui/console");
      navigate(dest, { replace: true });
    } catch {
      // 错误已在 AuthContext 中转为 loginError
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden bg-bg p-6">
      {/* 装饰：模糊光斑 */}
      <div
        className="pointer-events-none absolute -top-32 -left-32 h-[420px] w-[420px] rounded-full opacity-60 blur-3xl"
        style={{ background: "radial-gradient(circle, hsl(var(--accent)/0.35), transparent 60%)" }}
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -bottom-40 -right-40 h-[460px] w-[460px] rounded-full opacity-50 blur-3xl"
        style={{ background: "radial-gradient(circle, hsl(var(--accent-2)/0.35), transparent 60%)" }}
        aria-hidden
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "radial-gradient(circle at 1px 1px, currentColor 1px, transparent 0)",
          backgroundSize: "22px 22px",
        }}
        aria-hidden
      />

      <div className="lm-anim-fade-up relative w-full max-w-md">
        {/* 品牌标识 */}
        <div className="mb-7 flex flex-col items-center gap-3 text-center">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-brand text-white shadow-glow">
            <span className="text-2xl font-bold">L</span>
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">
              欢迎使用 <span className="lm-gradient-text">LiMem</span>
            </div>
            <div className="mt-1 text-sm text-subtle">
              长程记忆图谱 · 多租户控制台
            </div>
          </div>
        </div>

        <Card glass className="border-border/60 shadow-lg">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-accent" />
              使用 API Key 登录
            </CardTitle>
            <CardDescription>
              普通用户进入"我的库"，管理员 / ROOT 进入管理后台。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <div>
                <Label htmlFor="key" className="normal-case tracking-normal">
                  API Key
                </Label>
                <div className="relative">
                  <KeyRound
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle"
                    aria-hidden
                  />
                  <Input
                    id="key"
                    type={show ? "text" : "password"}
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="X-API-Key 或 Bearer token"
                    autoFocus
                    required
                    className="pl-9 pr-10 font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShow((s) => !s)}
                    className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-subtle transition hover:bg-muted hover:text-text"
                    aria-label={show ? "隐藏" : "显示"}
                  >
                    {show ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
                {loginError && (
                  <div className="mt-2 flex items-start gap-1.5 rounded-md border border-danger/30 bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
                    {loginError}
                  </div>
                )}
              </div>
              <Button
                type="submit"
                size="lg"
                className="w-full"
                loading={busy}
              >
                <KeyRound className="h-4 w-4" /> 登录
              </Button>
              <p className="pt-1 text-xs leading-relaxed text-subtle">
                没有 Key？请联系管理员通过{" "}
                <code className="bg-muted">/admin/users/&#123;id&#125;/keys</code>{" "}
                接口签发，或在已有 Key 登录后到"我的 Key"页面自助签发。
              </p>
            </form>
          </CardContent>
        </Card>

        <div className="mt-5 text-center text-[11px] text-subtle">
          LiMem · powered by Kuzu Graph & DashScope LLM
        </div>
      </div>
    </div>
  );
}
