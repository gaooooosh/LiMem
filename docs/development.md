# Development

## Setup

LiMem currently uses a `src/` layout without package build metadata. Use `PYTHONPATH=src` when running scripts or one-off Python commands.

```bash
uv sync
PYTHONPATH=src uv run python -c "from limem import create_ltm; print(create_ltm)"
```

For shell sessions:

```bash
export PYTHONPATH=src
```

## Tests

Run the unit test suite:

```bash
PYTHONPATH=src uv run python -m unittest discover tests
```

Some integration-style tests create temporary Kuzu databases and may be slower than pure unit tests. Avoid pointing tests at a shared `DB/` directory.

## Useful Commands

Start the service:

```bash
PYTHONPATH=src uv run python -m service.main
```

Run the pipeline visualizer:

```bash
PYTHONPATH=src uv run python src/script/run_pipeline_demo.py
```

Generate graph visualization from an existing database:

```bash
PYTHONPATH=src uv run python src/script/visualize_ltm.py --db ./DB/service.kz --serve
```

## Repository Hygiene

The public repository should include source code, prompts, tests, Docker files, and maintained documentation. It should not include:

- `.env` or other local secret files
- Kuzu databases under `DB/`
- generated files under `outputs/`
- local datasets such as `trips.json`, `session_v1.json`, and `example.json`
- private assistant notes such as `CLAUDE.md` and `可视化指令.txt`
- local learning/error logs under `.learnings/`

If a file has already been tracked by Git, adding it to `.gitignore` is not enough. Remove it from the index with:

```bash
git rm --cached <path>
```
