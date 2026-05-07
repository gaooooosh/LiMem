# HTTP API

The FastAPI service is defined in `src/service/app.py`. It provides scoped
authentication, user and API key management, database lifecycle management,
memory ingestion and retrieval, graph maintenance, audit inspection, and the web
console mount.

## Start The Service

Local development:

```bash
ROOT_API_KEY=change-me-to-a-long-random-token \
PYTHONPATH=src uv run python -m service.main
```

Default URL: `http://127.0.0.1:8000`

Docker Compose:

```bash
docker compose up -d --build
```

Compose maps the service to `http://127.0.0.1:8012` by default.

## Authentication

All API routes require an API key except public HTML/static routes such as
`/ui/login`, `/graph`, and `/logs`.

Recommended header:

```http
X-API-Key: <token>
```

Also accepted:

```http
Authorization: Bearer <token>
```

Debug HTML pages may pass `?key=<token>` because they are static pages that call
the API from browser JavaScript.

## Scopes

| Scope | Access |
| --- | --- |
| `r` | Read owned databases, query, health, stats, audit logs, graph snapshots |
| `w` | Write owned databases, ingest, evolve, rebuild indexes, graph mutations. `w` also reads |
| `admin` | Access `/admin/*` |

Root access comes from `ROOT_API_KEY`. Root is not stored in the auth database.

## Service Index

```http
GET /
```

Returns service metadata and endpoint groups.

## Admin API

Admin routes require root or an API key with `admin` scope.

### Create User

```http
POST /admin/users
Content-Type: application/json
```

```json
{
  "name": "alice"
}
```

Response:

```json
{
  "id": "user_id",
  "name": "alice",
  "created_at": "2026-05-07T00:00:00+00:00"
}
```

### List Users

```http
GET /admin/users
```

### Get User Detail

```http
GET /admin/users/{user_id}
```

Returns the user, all API keys for that user, and that user's databases.

### Issue Key For User

```http
POST /admin/users/{user_id}/keys
Content-Type: application/json
```

```json
{
  "label": "laptop",
  "scopes": "r,w"
}
```

Response includes the plaintext token exactly once:

```json
{
  "key": {
    "id": "key_id",
    "user_id": "user_id",
    "label": "laptop",
    "scopes": "r,w",
    "created_at": "2026-05-07T00:00:00+00:00",
    "last_used_at": null,
    "revoked_at": null
  },
  "token": "plaintext-token"
}
```

### Revoke Key

```http
DELETE /admin/keys/{key_id}
```

Returns `204 No Content`.

### List All Databases

```http
GET /admin/databases?include_archived=true
```

### Admin Health

```http
GET /admin/health
```

Returns service status and LTM pool stats.

## Current User API

### Who Am I

```http
GET /me
```

Response:

```json
{
  "is_root": false,
  "user_id": "user_id",
  "user_name": "alice",
  "key_id": "key_id",
  "key_label": "laptop",
  "scopes": ["r", "w"],
  "created_at": "2026-05-07T00:00:00+00:00",
  "last_used_at": "2026-05-07T00:01:00+00:00"
}
```

### List My Keys

```http
GET /me/keys
```

Root returns an empty list because the root token is environment-managed and is
not stored as a SQL API key.

### Issue My Key

```http
POST /me/keys
Content-Type: application/json
```

```json
{
  "label": "readonly-dashboard",
  "scopes": "r"
}
```

The requested scopes must be a subset of the caller's scopes.

### Revoke My Key

```http
DELETE /me/keys/{key_id}
```

Users can revoke only their own keys. Cross-user key IDs return `404` to avoid
leaking existence.

## Database API

### Create My Database

Requires `w` scope. Root cannot create a database here because every database
must belong to a real user.

```http
POST /databases
Content-Type: application/json
```

```json
{
  "display_name": "my-memory"
}
```

Response:

```json
{
  "db_id": "my-memory-8e03af",
  "owner_user_id": "user_id",
  "display_name": "my-memory",
  "created_at": "2026-05-07T00:00:00+00:00",
  "last_accessed_at": null,
  "status": "active"
}
```

### List My Databases

```http
GET /databases
```

Normal users see only their active databases. Root sees all active databases.

### Archive My Database

Requires `w` scope.

```http
DELETE /databases/{db_id}
```

Returns `204 No Content`. Archived databases are hidden from `/databases` and
cannot be accessed through `/db/{db_id}/...`.

## Memory API

All memory routes are scoped to one database:

```text
/db/{db_id}/...
```

The caller must be root or the owner of the database.

### Ingest

Requires `w` scope.

```http
POST /db/{db_id}/ingest
Content-Type: application/json
```

Request:

```json
{
  "data": {
    "source": "car_assistant",
    "payload": {
      "speaker": "driver",
      "query": "导航去公司",
      "reply": "已开始导航"
    }
  },
  "timestamp": 1777008724
}
```

`data` may be any JSON value. The service flattens it to text before extraction.

Response:

```json
{
  "event_id": "event_id",
  "summary": "用户请求导航去公司，系统开始导航",
  "is_new": true,
  "entities_created": 2,
  "event_count": 1
}
```

### Query

Requires read access.

```http
POST /db/{db_id}/query
Content-Type: application/json
```

Request:

```json
{
  "query": "用户最近导航去了哪里",
  "top_k": 5
}
```

Response:

```json
{
  "results": [
    {
      "event_id": "event_id",
      "summary": "用户请求导航去公司，系统开始导航",
      "action": "请求导航",
      "causality": "用户需要前往公司",
      "timestamp": 1777008724,
      "score": 4.2
    }
  ],
  "total": 1
}
```

### Evolve

Requires `w` scope.

```http
POST /db/{db_id}/evolve
```

Runs memory evolution/consolidation and rebuilds the BM25 index.

### Rebuild Index

Requires `w` scope.

```http
POST /db/{db_id}/rebuild-index
```

Rebuilds the active-event BM25 index without running consolidation.

### Health

```http
GET /db/{db_id}/health
```

Returns status, database ID, audit log path, event count, and BM25 index size.

### Stats

```http
GET /db/{db_id}/stats
```

Returns graph node and relationship counts from the current Kuzu database.

### Audit

```http
GET /db/{db_id}/api/audit/recent?limit=200
```

Returns recent audit records for the selected database.

## Graph API

Graph API routes are used by the debug graph UI and trusted operators.

| Endpoint | Scope | Purpose |
| --- | --- | --- |
| `GET /db/{db_id}/api/graph/snapshot?limit=100&include_inactive=false&text=` | `r` | Graph snapshot consumed by the graph UI |
| `GET /db/{db_id}/api/graph/node/{kind}/{node_id}` | `r` | Fetch event/context details |
| `POST /db/{db_id}/api/graph/write` | `w` | Manually write an event or context |
| `POST /db/{db_id}/api/graph/update` | `w` | Update an event or context |
| `POST /db/{db_id}/api/graph/delete` | `w` | Soft or hard delete a node |

Supported `kind` values are currently `event` and `context`.

## UI Routes

| Route | Purpose |
| --- | --- |
| `/ui/login` | React console login page |
| `/ui/console` | User console |
| `/ui/console/keys` | User API key management |
| `/ui/console/db/{db_id}` | Database detail and memory operations |
| `/ui/admin` | Admin dashboard |
| `/ui/admin/users` | User management |
| `/ui/admin/users/{user_id}` | User detail, keys, and databases |
| `/ui/admin/databases` | Global database list |
| `/graph?db={db_id}&key={api_key}` | Standalone graph debug page |
| `/logs?db={db_id}&key={api_key}` | Standalone audit log viewer |

The `/ui` SPA is public static HTML. Authentication is enforced by the API calls
that the browser makes after login.

## Common Error Codes

| Status | Meaning |
| --- | --- |
| `400` | Invalid request, unknown scope, root attempted a user-only operation |
| `401` | Missing, invalid, or revoked API key |
| `403` | Authenticated but insufficient scope or wrong database owner |
| `404` | Database/key/node not found, archived database, or hidden cross-user key |
