import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { hasScope, useAuth } from "./AuthContext";
import type { Scope } from "@/api/types";

interface Props {
  children: ReactNode;
  /** 必须具备的 scope；任一未满足则跳走 */
  needs?: Scope[];
  /** scope 不足时跳哪里，默认 /ui/console */
  fallback?: string;
}

export function ProtectedRoute({ children, needs = [], fallback = "/ui/console" }: Props) {
  const { me, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="grid h-screen place-items-center text-subtle">
        <div className="animate-pulse">加载中…</div>
      </div>
    );
  }

  if (!me) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/ui/login?next=${next}`} replace />;
  }

  for (const s of needs) {
    if (!hasScope(me, s)) {
      return (
        <ScopeDeniedScreen needs={needs} fallback={fallback} />
      );
    }
  }
  return <>{children}</>;
}

function ScopeDeniedScreen({ needs, fallback }: { needs: Scope[]; fallback: string }) {
  return (
    <div className="grid h-screen place-items-center p-6">
      <div className="max-w-md rounded-lg border bg-panel p-8 text-center shadow-sm">
        <div className="mb-3 text-2xl font-semibold text-danger">权限不足</div>
        <p className="text-subtle">
          访问此页面需要 scope:{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">{needs.join(", ")}</code>
        </p>
        <p className="mt-2 text-sm text-subtle">3 秒后自动跳转…</p>
        <CountdownRedirect to={fallback} seconds={3} />
      </div>
    </div>
  );
}

function CountdownRedirect({ to, seconds }: { to: string; seconds: number }) {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (window.location.pathname !== to) {
        window.location.assign(to);
      }
    }, seconds * 1000);
    return () => window.clearTimeout(timer);
  }, [seconds, to]);

  return null;
}
