// 与 src/service/models.py 的 Pydantic 模型保持镜像
export type Scope = "r" | "w" | "admin";

export interface Me {
  is_root: boolean;
  user_id: string;
  user_name: string;
  key_id: string;
  key_label: string;
  scopes: Scope[];
  created_at: string | null;
  last_used_at: string | null;
}

export interface UserView {
  id: string;
  name: string;
  created_at: string;
}

export interface ApiKeyView {
  id: string;
  user_id: string;
  label: string;
  scopes: string; // csv
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

export interface IssueKeyResponse {
  key: ApiKeyView;
  token: string; // 仅本次返回的明文
}

export interface DatabaseView {
  db_id: string;
  owner_user_id: string;
  display_name: string;
  created_at: string;
  last_accessed_at?: string | null;
  status: "active" | "archived";
}

export interface UserDetail {
  user: UserView;
  keys: ApiKeyView[];
  databases: DatabaseView[];
}

export interface AdminHealth {
  status: string;
  pool: Record<string, unknown>;
}

export interface IngestResponse {
  event_id: string;
  summary: string;
  is_new: boolean;
  entities_created: number;
  event_count: number;
}

export interface QueryResult {
  event_id: string;
  summary: string;
  action: string;
  causality: string;
  timestamp: number;
  score: number;
}

export interface QueryResponse {
  results: QueryResult[];
  total: number;
}

export interface DbStats {
  [k: string]: unknown;
}

export interface DbHealth {
  [k: string]: unknown;
}

export interface AuditEntry {
  trace_id?: string;
  ts?: string;
  type?: string;
  detail?: unknown;
  [k: string]: unknown;
}

// ---------- 注册实体（与后端 models.py 镜像） ----------
export interface RegisteredEntity {
  id: string;
  type: string;
  description: string;
  aliases: string[];
  registered: boolean;
  status: string;
  canonical_id?: string | null;
  merged_from: string[];
  created_at?: number | null;
  updated_at?: number | null;
  metadata: Record<string, unknown>;
}

export interface RegisterEntityRequest {
  entity_id: string;
  description: string;
  entity_type?: string;
  aliases?: string[];
  metadata?: Record<string, unknown>;
}

export interface UpdateEntityRequest {
  description?: string;
  entity_type?: string;
  add_aliases?: string[];
  remove_aliases?: string[];
  metadata?: Record<string, unknown>;
}

export interface RegisterEntityResponse {
  action: "created" | "promoted" | "updated";
  existed_as_extracted: boolean;
  entity: RegisteredEntity;
}

export interface ListEntitiesResponse {
  items: RegisteredEntity[];
  total: number;
}
