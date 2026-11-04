# PWS News — agent notes

See README.md for architecture and commands.

## Layout
- `config/sources.yaml`: the only place sources and pipeline/scoring/report knobs are configured. Secrets are referenced as `${ENV}` and live in `.env`.
- `backend/app/`: FastAPI service. `connectors/` (source adapters), `pipeline/` (collect → group → score → summarize), `llm.py` (OpenAI-compatible client), `storage.py` (report JSON history: `<date>.json` is the newest generation, `generations/<date>/<stamp>.json` keeps every one), `main.py` (routes).
- `frontend/src/`: Node renderer and scheduler. `report-format/` holds ALL page layout and styling. Keep presentation out of `publish.js`, `jobs.js` and `main.js`.
- `nginx/nginx.conf`: static serving and cache headers. Pages that are rewritten in place (front page, `/reports/<date>/`, indexes) must stay `no-cache`, and don't re-enable `open_file_cache`, or readers see stale pages after a new generation.
- `Dockerfile`: runtime image per service (compose `target:`). `Dockerfile.test`: CI lint/tests layered on those images.

## Rules
- New source types: add a `Connector` subclass with `@register("<type>")` and import it in `connectors/__init__.py`. Don't special-case source types in the pipeline.
- The pipeline must still produce a report when the LLM is unreachable (fallback to the top source).
- The Report JSON shape (`backend/app/models.py` `Report`) is the contract between the backend and the renderer. Keep changes backward compatible, because old reports are re-rendered by `npm run rebuild` and automatically at renderer startup whenever `report-format/` changes.
- The frontend has no npm dependencies; keep it that way unless clearly needed.
- Fonts: only self-hosted, freely licensed (SIL OFL) files in `frontend/src/report-format/assets/` (copied to `/assets/` by `publishSite`), with generic `serif`/`sans-serif` fallbacks. Don't name proprietary fonts (Georgia, Times, Courier, Arial...) in CSS; a test enforces this.
- Privacy page: bump `POLICY_UPDATED` in `report-format/index.js` whenever the policy or disclaimer text changes. Its source list comes from `GET /api/sources` (enabled sources in `config/sources.yaml`).

## Checks
- `cd backend && ../.venv/bin/python -m pytest` (venv: `python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt`)
- Changes to merging/similarity: `cd backend && ../.venv/bin/python -m tests.merge_eval eval tests/data/merge/<day>.json`, and raise `BASELINE` in `tests/test_merge_eval.py` when results improve. Test data lives in `backend/tests/data/`.
- `cd frontend && npm test` (Node >= 20)
- CI: `Jenkinsfile` runs the same checks in containers (plus `ruff check --select E9,F` on the backend). If you add env vars to `.env.example`, add them to the Jenkinsfile `environment {}` block and its `.env` writer too.
