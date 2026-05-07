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
    <header className="sticky top-0 z-20 border-b border-border bg-panel/80 backdrop-blur">
      {me?.is_root && (
        <div className="flex items-center justify-center gap-2 bg-danger px-4 py-1.5 text-xs font-medium text-white">
          <Shield className="h-3.5 w-3.5" /> 您正以 ROOT 身份登录。建议改用具名 admin Key（前往 用户管理 创建）
        </div>
      )}
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
        <Link to="/ui/console" className="flex items-center gap-2 font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-white">L</span>
          <span className="hidden sm:inline">LiMem 控制台</span>
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
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-accent/10 text-accent"
                    : "text-subtle hover:bg-muted hover:text-text",
                )}
              >
                {item.icon}
                <span className="hidden md:inline">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        {me && (
          <div className="flex items-center gap-3">
            <div className="hidden flex-col items-end text-right sm:flex">
              <div className="text-sm font-medium leading-tight">
                {me.user_name}
                {me.is_root && (
                  <Badge variant="danger" className="ml-1.5">ROOT</Badge>
                )}
              </div>
              <div className="text-[10px] leading-tight">
                <ScopeBadgeList scopes={me.scopes} />
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label="切换主题"
              title="切换主题"
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
