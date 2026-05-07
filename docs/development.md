# Development

## Setup

LiMem uses a Python `src/` layout. Use `PYTHONPATH=src` when running scripts or
one-off Python commands.

```bash
uv sync
PYTHONPATH=src uv run python -c "from limem import create_ltm; print(create_ltm)"
```

For shell sessions:

```bash
export PYTHONPATH=src
```

Copy the environment template before running the service:

```bash
cp .env.example .env
```

At minimum, configure:

```bash
DASHSCOPE_API_KEY=your-api-key
ROOT_API_KEY=change-me-to-a-long-random-token
```

`ROOT_API_KEY` is required in service mode. The service exits at
startup when it is missing.

## Backend Service

Start the FastAPI service locally:

```bash
ROOT_API_KEY=change-me-to-a-long-random-token \
PYTHONPATH=src uv run python -m service.main
```

Default URL: `http://127.0.0.1:8000`

The service uses these defaults unless overridden:

```bash
AUTH_DB_PATH=./DB/auth.sqlite
MULTI_DB_BASE_DIR=./DB
MULTI_AUDIT_BASE_DIR=./outputs/audit
LTM_POOL_MAX_SIZE=16
LTM_POOL_IDLE_TIMEOUT_SEC=1800
```

Create a development user and key with root:

```bash
export BASE=http://127.0.0.1:8000
export ROOT_KEY=change-me-to-a-long-random-token

curl -sS -X POST "$BASE/admin/users" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"dev"}'

curl -sS -X POST "$BASE/admin/users/{user_id}/keys" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label":"dev","scopes":"r,w,admin"}'
```

## Frontend Console

The React console lives in `web/`.

Install dependencies:

```bash
cd web
npm install
```

Run the Vite dev server:

```bash
npm run dev
```

Default dev URL: `http://127.0.0.1:5173/ui/login`

`web/vite.config.ts` proxies API calls to `http://127.0.0.1:8000`:

- `/me`
- `/admin`
- `/databases`
- `/db`
- `/graph`
- `/logs`

Build the frontend:

```bash
npm run build
```

The Dockerfile builds `web/dist` in a Node stage and copies it into the Python
runtime image at `src/service/static/ui`.

## Docker Workflow

Build and run the full service:

```bash
docker compose up -d --build
```

Default Compose URL: `http://127.0.0.1:8012/ui/login`

Compose bind mounts:

- `./DB:/app/DB`
- `./outputs:/app/outputs`

The container healthcheck calls `/admin/health` with `ROOT_API_KEY`.

## Tests

Run the service test suite:

```bash
PYTHONPATH=src uv run pytest tests/service -q
```

Run the full Python test suite:

```bash
PYTHONPATH=src uv run pytest tests -q
```

Build-check the frontend:

```bash
cd web
npm run build
```

Service tests use temporary auth databases and temporary user database
directories. Avoid pointing tests at a shared `DB/` directory.

## Useful Commands

Run the pipeline visualizer:

```bash
PYTHONPATH=src uv run python src/script/run_pipeline_demo.py
```

Generate graph visualization from an existing database:

```bash
PYTHONPATH=src uv run python src/script/visualize_ltm.py --db ./DB/service.kz --serve
```

Inspect Docker service health:

```bash
docker compose ps
docker logs --tail 80 limem-service
```

## Repository Hygiene

The public repository should include source code, prompts, tests, Docker files,
frontend source, lockfiles, and maintained documentation. It should not include:

- `.env` or other local secret files
- Kuzu databases under `DB/`
- generated files under `outputs/`
- Python virtual environments such as `.venv/`
- frontend dependency/build output such as `web/node_modules/` and `web/dist/`
- TypeScript incremental build info such as `*.tsbuildinfo`
- local datasets such as `trips.json`, `session_v1.json`, and `example.json`
- private assistant notes such as `CLAUDE.md` and `可视化指令.txt`
- local learning/error logs under `.learnings/`

If a file has already been tracked by Git, adding it to `.gitignore` is not
enough. Remove it from the index with:

```bash
git rm --cached <path>
```

Before committing, run:

```bash
git status --short --untracked-files=all
git diff --check
PYTHONPATH=src uv run pytest tests/service -q
(cd web && npm run build)
```
