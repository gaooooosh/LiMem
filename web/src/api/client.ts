// 统一 fetch 包装：注入 X-API-Key、统一错误处理、401 自动跳登录
import { toast } from "@/components/Toaster";
import type {
  AdminHealth,
  ApiKeyView,
  AuditEntry,
  DatabaseView,
  DbHealth,
  DbStats,
  IngestResponse,
  IssueKeyResponse,
  ListEntitiesResponse,
  Me,
  QueryResponse,
  RegisterEntityRequest,
  RegisterEntityResponse,
  RegisteredEntity,
  UpdateEntityRequest,
  UserDetail,
  UserView,
} from "./types";

const KEY_STORAGE = "limem_key";
// 持久化"上次成功登录的 Key"，仅用于登录页输入框预填，不参与请求注入
const LAST_KEY_STORAGE = "limem_last_key";

export function getStoredKey(): string | null {
  return sessionStorage.getItem(KEY_STORAGE);
}

export function setStoredKey(key: string) {
  sessionStorage.setItem(KEY_STORAGE, key);
}

export function clearStoredKey() {
  sessionStorage.removeItem(KEY_STORAGE);
}

export function getLastKey(): string | null {
  try {
    return localStorage.getItem(LAST_KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setLastKey(key: string) {
  if (!key) return;
  try {
    localStorage.setItem(LAST_KEY_STORAGE, key);
  } catch {
    // 隐私模式 / 配额超限：静默降级，不影响登录主流程
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestInitX extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** 401 时不要自动登出/跳转，由调用方处理（登录页探测时用） */
  silent401?: boolean;
}

export async function api<T = unknown>(
  path: string,
  init: RequestInitX = {},
): Promise<T> {
  const key = getStoredKey();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(key ? { "X-API-Key": key } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };

  let body: BodyInit | undefined;
  if (init.body !== undefined && init.body !== null) {
    if (typeof init.body === "string" || init.body instanceof FormData) {
      body = init.body as BodyInit;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }
  }

  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers,
      body,
    });
  } catch (e) {
    toast.error("网络错误，请检查后端是否在线");
    throw e;
  }

  if (resp.status === 401) {
    if (!init.silent401) {
      clearStoredKey();
      toast.error("登录已失效，请重新输入 API Key");
      const next = encodeURIComponent(location.pathname + location.search);
      // 用 replace 避免回退栈污染
      setTimeout(() => {
        if (!location.pathname.startsWith("/ui/login")) {
          location.replace(`/ui/login?next=${next}`);
        }
      }, 400);
    }
    const detail = await resp.json().catch(() => ({}));
    throw new ApiError(401, detail, "unauthorized");
  }

  if (!resp.ok) {
    const detail = (await resp.json().catch(() => ({}))) as { detail?: unknown };
    const msg =
      typeof detail.detail === "string"
        ? detail.detail
        : JSON.stringify(detail.detail ?? resp.statusText);
    if (!init.silent401) toast.error(`${resp.status}: ${msg}`);
    throw new ApiError(resp.status, detail, msg);
  }

  if (resp.status === 204) return undefined as T;
  // 部分管理路由没有响应体
  const text = await resp.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// ---------- /me ----------
export const meApi = {
  whoami: (key?: string) =>
    api<Me>("/me", {
      silent401: true,
      headers: key ? { "X-API-Key": key } : undefined,
    }),
  listKeys: () => api<ApiKeyView[]>("/me/keys"),
  issueKey: (label: string, scopes: string) =>
    api<IssueKeyResponse>("/me/keys", {
      method: "POST",
      body: { label, scopes },
    }),
  revokeKey: (key_id: string) =>
    api<void>(`/me/keys/${encodeURIComponent(key_id)}`, { method: "DELETE" }),
};

// ---------- /databases (用户自助) ----------
export const dbApi = {
  list: () => api<DatabaseView[]>("/databases"),
  create: (display_name: string) =>
    api<DatabaseView>("/databases", { method: "POST", body: { display_name } }),
  archive: (db_id: string) =>
    api<void>(`/databases/${encodeURIComponent(db_id)}`, { method: "DELETE" }),
};

// ---------- /db/{id}/* (业务操作) ----------
export const memoryApi = {
  health: (db_id: string) => api<DbHealth>(`/db/${encodeURIComponent(db_id)}/health`),
  stats: (db_id: string) => api<DbStats>(`/db/${encodeURIComponent(db_id)}/stats`),
  ingest: (db_id: string, data: unknown, timestamp?: number) =>
    api<IngestResponse>(`/db/${encodeURIComponent(db_id)}/ingest`, {
      method: "POST",
      body: { data, timestamp },
    }),
  query: (db_id: string, query: string, top_k = 5) =>
    api<QueryResponse>(`/db/${encodeURIComponent(db_id)}/query`, {
      method: "POST",
      body: { query, top_k },
    }),
  evolve: (db_id: string) =>
    api<{ message: string; details: Record<string, number> }>(
      `/db/${encodeURIComponent(db_id)}/evolve`,
      { method: "POST" },
    ),
  rebuildIndex: (db_id: string) =>
    api<{ message: string }>(`/db/${encodeURIComponent(db_id)}/rebuild-index`, {
      method: "POST",
    }),
  audit: (db_id: string, limit = 200) =>
    api<AuditEntry[] | { entries: AuditEntry[] }>(
      `/db/${encodeURIComponent(db_id)}/api/audit/recent?limit=${limit}`,
    ),
};

// ---------- /db/{id}/api/entities (注册实体管理) ----------
export const entityApi = {
  list: (db_id: string) =>
    api<ListEntitiesResponse>(`/db/${encodeURIComponent(db_id)}/api/entities`),
  get: (db_id: string, eid: string) =>
    api<RegisteredEntity>(
      `/db/${encodeURIComponent(db_id)}/api/entities/${encodeURIComponent(eid)}`,
    ),
  register: (db_id: string, body: RegisterEntityRequest) =>
    api<RegisterEntityResponse>(
      `/db/${encodeURIComponent(db_id)}/api/entities`,
      { method: "POST", body },
    ),
  update: (db_id: string, eid: string, body: UpdateEntityRequest) =>
    api<RegisterEntityResponse>(
      `/db/${encodeURIComponent(db_id)}/api/entities/${encodeURIComponent(eid)}`,
      { method: "PATCH", body },
    ),
};

// ---------- /admin/* ----------
export const adminApi = {
  health: () => api<AdminHealth>("/admin/health"),
  listUsers: () => api<UserView[]>("/admin/users"),
  createUser: (name: string) =>
    api<UserView>("/admin/users", { method: "POST", body: { name } }),
  getUser: (user_id: string) =>
    api<UserDetail>(`/admin/users/${encodeURIComponent(user_id)}`),
  issueKey: (user_id: string, label: string, scopes: string) =>
    api<IssueKeyResponse>(
      `/admin/users/${encodeURIComponent(user_id)}/keys`,
      { method: "POST", body: { label, scopes } },
    ),
  revokeKey: (key_id: string) =>
    api<void>(`/admin/keys/${encodeURIComponent(key_id)}`, { method: "DELETE" }),
  listAllDatabases: (include_archived = true) =>
    api<DatabaseView[]>(
      `/admin/databases?include_archived=${include_archived}`,
    ),
};
