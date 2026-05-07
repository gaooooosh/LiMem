import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { LogOut, Moon, Sun, Database, Shield, KeyRound, Users } from "lucide-react";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { ScopeBadgeList } from "./ScopeBadge";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/utils";

function useTheme(): [string, (next: string) => void] {
  const [theme, setTheme] = useState<string>(() => {
    if (typeof window === "undefined") return "light";
    return localStorage.getItem("limem_theme") || "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("limem_theme", theme);
  }, [theme]);
  return [theme, setTheme];
}

interface NavItem {
  to: string;
  label: string;
  icon: JSX.Element;
  needAdmin?: boolean;
}

const navItems: NavItem[] = [
  { to: "/ui/console", label: "我的库", icon: <Database className="h-4 w-4" /> },
  { to: "/ui/console/keys", label: "我的 Key", icon: <KeyRound className="h-4 w-4" /> },
  { to: "/ui/admin", label: "管理后台", icon: <Shield className="h-4 w-4" />, needAdmin: true },
  { to: "/ui/admin/users", label: "用户管理", icon: <Users className="h-4 w-4" />, needAdmin: true },
  { to: "/ui/admin/databases", label: "全部库", icon: <Database className="h-4 w-4" />, needAdmin: true },
];

export function TopBar() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [theme, setTheme] = useTheme();

  const isAdmin = !!me && (me.is_root || me.scopes.includes("admin"));

  return (
    <header className="sticky top-0 z-30 border-b border-border/70 lm-glass">
      {me?.is_root && (
        <div
          className={cn(
            "flex items-center justify-center gap-2 px-4 py-1.5 text-xs font-medium text-white",
            "bg-gradient-to-r from-danger via-danger to-rose-500",
          )}
        >
          <Shield className="h-3.5 w-3.5" />
          <span>
            您正以 ROOT 身份登录。建议改用具名 admin Key（前往
            <Link to="/ui/admin/users" className="underline underline-offset-2 ml-1">
              用户管理
            </Link>
            创建）
          </span>
        </div>
      )}
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4">
        <Link
          to="/ui/console"
          className="group flex items-center gap-2.5 font-semibold tracking-tight"
        >
          <span
            className={cn(
              "grid h-9 w-9 place-items-center rounded-xl text-white shadow-glow",
              "bg-gradient-brand transition-transform duration-300 ease-out-soft",
              "group-hover:scale-[1.06] group-hover:rotate-[-3deg]",
            )}
          >
            <span className="text-base font-bold">L</span>
          </span>
          <div className="hidden sm:flex sm:flex-col sm:leading-tight">
            <span className="text-sm font-semibold">LiMem</span>
            <span className="text-[11px] font-normal text-subtle">控制台</span>
          </div>
        </Link>

        <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
          {navItems.map((item) => {
            if (item.needAdmin && !isAdmin) return null;
            const active =
              location.pathname === item.to ||
              location.pathname.startsWith(item.to + "/");
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "group/nav relative inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm",
                  "transition-colors duration-150 ease-out-soft",
                  active
                    ? "text-text"
                    : "text-subtle hover:bg-muted/60 hover:text-text",
                )}
              >
                <span className={cn("transition-transform", active && "text-accent")}>
                  {item.icon}
                </span>
                <span className="hidden md:inline">{item.label}</span>
                {active && (
                  <span
                    className="absolute inset-x-2 -bottom-[7px] h-[2px] rounded-full bg-gradient-brand"
                    aria-hidden
                  />
                )}
              </Link>
            );
          })}
        </nav>

        {me && (
          <div className="flex items-center gap-2">
            <div className="hidden flex-col items-end text-right sm:flex">
              <div className="flex items-center gap-1.5 text-sm font-medium leading-tight">
                <span>{me.user_name}</span>
                {me.is_root && (
                  <Badge variant="danger" dot>ROOT</Badge>
                )}
              </div>
              <div className="mt-0.5 text-[10px] leading-tight">
                <ScopeBadgeList scopes={me.scopes} />
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label="切换主题"
              title={theme === "dark" ? "切到亮色" : "切到暗色"}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                logout();
                navigate("/ui/login", { replace: true });
              }}
              aria-label="登出"
              title="登出"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>
    </header>
  );
}
