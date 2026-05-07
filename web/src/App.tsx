import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./pages/LoginPage";
import { ConsolePage } from "./pages/ConsolePage";
import { ConsoleKeysPage } from "./pages/ConsoleKeysPage";
import { DbDetailPage } from "./pages/DbDetailPage";
import { AdminDashboardPage } from "./pages/AdminDashboardPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AdminUserDetailPage } from "./pages/AdminUserDetailPage";
import { AdminDatabasesPage } from "./pages/AdminDatabasesPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/ui/login" replace />} />
      <Route path="/ui" element={<Navigate to="/ui/login" replace />} />
      <Route path="/ui/login" element={<LoginPage />} />

      {/* 用户控制台 */}
      <Route
        path="/ui/console"
        element={
          <ProtectedRoute needs={["r"]}>
            <ConsolePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ui/console/keys"
        element={
          <ProtectedRoute needs={["r"]}>
            <ConsoleKeysPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ui/console/db/:dbId"
        element={
          <ProtectedRoute needs={["r"]}>
            <DbDetailPage />
          </ProtectedRoute>
        }
      />

      {/* 管理后台 */}
      <Route
        path="/ui/admin"
        element={
          <ProtectedRoute needs={["admin"]}>
            <AdminDashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ui/admin/users"
        element={
          <ProtectedRoute needs={["admin"]}>
            <AdminUsersPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ui/admin/users/:userId"
        element={
          <ProtectedRoute needs={["admin"]}>
            <AdminUserDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ui/admin/databases"
        element={
          <ProtectedRoute needs={["admin"]}>
            <AdminDatabasesPage />
          </ProtectedRoute>
        }
      />

      {/* 兜底 */}
      <Route path="*" element={<Navigate to="/ui/login" replace />} />
    </Routes>
  );
}
