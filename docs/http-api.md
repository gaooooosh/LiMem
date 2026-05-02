# HTTP API

The FastAPI service is defined in `src/service/app.py`. It provides ingestion, search, graph maintenance, audit inspection, and graph visualization endpoints.

Start the service locally:

```bash
PYTHONPATH=src uv run python -m service.main
```

Default URL: `http://127.0.0.1:8000`

## Health

```http
GET /health
```

Returns service status, database path, audit log path, active event count, and BM25 index size.

## Ingest

```http
POST /ingest
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

## Query

```http
POST /query
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

## Consolidation

```http
POST /evolve
```

Runs consolidation and rebuilds the BM25 index.

```http
POST /rebuild-index
```

Rebuilds the active-event BM25 index without running consolidation.

## Stats

```http
GET /stats
```

Returns graph node and relationship counts from the current Kuzu database.

## Audit and Visualization

| Endpoint | Purpose |
| --- | --- |
| `GET /api/audit/recent?limit=200` | Recent audit records |
| `GET /graph` | Interactive graph page |
| `GET /logs` | Audit log viewer |
| `GET /api/graph/snapshot` | Graph snapshot consumed by the graph UI |
| `POST /api/graph/write` | Manually write an event or context |
| `POST /api/graph/update` | Update an event or context |
| `POST /api/graph/delete` | Soft or hard delete a node |
| `GET /api/graph/node/{kind}/{node_id}` | Fetch event/context details |

The graph mutation endpoints are intended for trusted operators and local debugging tools. Put authentication or network-level access control in front of them before exposing the service outside a private environment.

