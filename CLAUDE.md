# PWS News — agent notes

See README.md for architecture and commands.

## Layout
- `config/sources.yaml`: the only place sources and pipeline/scoring/report knobs are configured. Secrets are referenced as `${ENV}` and live in `.env`.
- `backend/app/`: FastAPI service. `connectors/` (source adapters), `pipeline/` (collect → preprocess → embed → cluster → score → select → summarize), `llm.py` (OpenAI-compatible client), `storage.py` (report JSON history: `<date>.json` is the newest generation, `generations/<date>/<stamp>.json` keeps every one), `main.py` (routes), `metrics.py` (Prometheus metrics at `GET /metrics`).
- `frontend/src/`: Node renderer and scheduler. `report-format/` holds ALL page layout and styling. Keep presentation out of `publish.js`, `jobs.js` and `main.js`.
- `nginx/nginx.conf`: static serving and cache headers. Pages that are rewritten in place (front page, `/reports/<date>/`, indexes) must stay `no-cache`, and don't re-enable `open_file_cache`, or readers see stale pages after a new generation.
- `Dockerfile`: runtime image per service (compose `target:`). `Dockerfile.test`: CI lint/tests layered on those images.

## Rules
- New source types: add a `Connector` subclass with `@register("<type>")` and import it in `connectors/__init__.py`. Don't special-case source types in the pipeline.
- The pipeline must still produce a report when the LLM is unreachable (fallback to the top source), and must still cluster when embeddings fail (TF-IDF fallback in `pipeline/grouping.py`).
- The Report JSON shape (`backend/app/models.py` `Report`) is the contract between the backend and the renderer. Keep changes backward compatible, because old reports are re-rendered by `npm run rebuild`.
- The frontend has no npm dependencies; keep it that way unless clearly needed.
- Fonts: only self-hosted, freely licensed (SIL OFL) files in `frontend/src/report-format/assets/` (copied to `/assets/` by `publishSite`), with generic `serif`/`sans-serif` fallbacks. Don't name proprietary fonts (Georgia, Times, Courier, Arial...) in CSS; a test enforces this.
- Metrics: define them in `backend/app/metrics.py` with a `news_` prefix and keep label values bounded (source ids, outcomes; never URLs or headlines). Document new ones in the README Metrics table. `/metrics` stays off the nginx config.
- Site pages: `/privacy/`, `/disclaimer/` and `/sources/` are separate pages linked from the footer. Bump `POLICY_UPDATED` in `report-format/index.js` whenever the privacy policy or disclaimer text changes. The sources page list comes from `GET /api/sources` (enabled sources in `config/sources.yaml`).
- Story scores and selection cutoffs assume each story signal stays in 0..1. Keep new signals in that range, and recheck `report.cutoff_*` in `config/sources.yaml` when you change `scoring.story`.

## Checks
- `cd backend && ../.venv/bin/python -m pytest` (venv: `python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt`)
- `cd frontend && npm test` (Node >= 20)
- CI: `Jenkinsfile` runs the same checks in containers (plus `ruff check --select E9,F` on the backend). If you add env vars to `.env.example`, add them to the Jenkinsfile `environment {}` block and its `.env` writer too.
