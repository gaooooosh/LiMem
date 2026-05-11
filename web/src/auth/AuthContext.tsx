import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  clearStoredKey,
  getStoredKey,
  meApi,
  setLastKey,
  setStoredKey,
} from "@/api/client";
import type { Me } from "@/api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  loginError: string | null;
  /** 用 Key 探测并登录；成功返回 Me，失败抛错 */
  login: (key: string) => Promise<Me>;
  logout: () => void;
  /** 已登录状态下重新拉一次 /me（如签发/撤销 Key 后刷新 last_used） */
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [loginError, setLoginError] = useState<string | null>(null);

  // 启动时若 sessionStorage 有 key，自动恢复会话
  useEffect(() => {
    const k = getStoredKey();
    if (!k) {
      setLoading(false);
      return;
    }
    meApi
      .whoami(k)
      .then((data) => setMe(data))
      .catch(() => {
        clearStoredKey();
        setMe(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (key: string) => {
    setLoginError(null);
    try {
      const trimmed = key.trim();
      const data = await meApi.whoami(trimmed);
      setStoredKey(trimmed);
      // 仅在验证通过后写入持久化记录，避免污染下次预填
      setLastKey(trimmed);
      setMe(data);
      return data;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "登录失败";
      setLoginError(msg === "unauthorized" ? "API Key 无效或已撤销" : msg);
      throw e;
    }
  }, []);

  const logout = useCallback(() => {
    clearStoredKey();
    setMe(null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await meApi.whoami();
      setMe(data);
    } catch {
      logout();
    }
  }, [logout]);

  const value = useMemo<AuthState>(
    () => ({ me, loading, loginError, login, logout, refresh }),
    [me, loading, loginError, login, logout, refresh],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}

export function hasScope(me: Me | null, scope: "r" | "w" | "admin"): boolean {
  if (!me) return false;
  if (me.is_root) return true;
  return me.scopes.includes(scope);
}
