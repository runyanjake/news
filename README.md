# news
A news aggregator inspired by https://news.dws.rip, offering a periodically rendered summary of what is going on in chosen areas.

## How it works

```mermaid
flowchart LR
  cfg[config/sources.yaml] --> be
  subgraph be[backend · Python/FastAPI]
    c[connectors] --> g[group into topics] --> s[score sources] --> l[LLM tagline + summary]
  end
  be -- report JSON history --> data1[(data/reports)]
  r[renderer · Node] -- 08:00 daily: generate + poll --> be
  r -- static HTML --> data2[(data/site)]
  data2 --> n[nginx] --> u((readers))
```

1. **Collect**: every source in `config/sources.yaml` is fetched by a connector (`backend/app/connectors/`). RSS ships today. Other source types (Twitter/X, JSON APIs) subclass `Connector` and register a new `type`.
2. **Group**: articles are bucketed into topics. The `keywords` method applies manual rules from the config, then title-similarity clustering. The `llm` method asks the LLM to cluster, and falls back to `keywords` if that fails.
3. **Score**: each article gets a weighted mix of source reputation, recency and relevance to its topic. A topic's score is the sum of its best article from each source, so stories covered by several outlets rank higher.
4. **Summarise**: the top N sources of each top topic go to the LLM, which returns a tagline and summary. Sources are listed on the entry. If the LLM is down, the entry falls back to the top source's headline.
5. **Render**: the Node renderer turns the report into static HTML once a day at `RENDER_TIME` (local `TZ`). Every rendering is kept under `data/site/reports/<date>/`. nginx serves the files with cache headers and proxies read-only `/api/` calls through a response cache.

Report layout lives only in `frontend/src/report-format/`. After changing it, run `npm run rebuild` to re-render the whole history from the stored JSON.

## Running

```sh
cp .env.example .env   # set ADMIN_TOKEN, TZ, LLM_BASE_URL / LLM_MODEL
docker compose up -d --build
docker compose exec renderer node src/main.js generate   # first render now instead of waiting for 08:00
```

The site is served on `http://localhost:${HTTP_PORT}`. `config/sources.yaml` and `nginx/nginx.conf` are baked into the images, so re-run `docker compose up -d --build` after editing them. Report JSON and rendered HTML live in the `reports` and `site` named volumes (not `data/`), which survive `docker compose down` but not `down -v`. Any OpenAI-compatible endpoint works for the LLM, e.g. Ollama at `http://host.docker.internal:11434/v1`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
(cd backend && ../.venv/bin/python -m pytest)
(cd frontend && npm test)          # Node >= 20, no npm dependencies

set -a; . ./.env; set +a
(cd backend && ../.venv/bin/uvicorn app.main:create_app --factory --port 8000)
(cd frontend && npm run generate)  # writes data/site/
```

### API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/reports` | stored report dates, newest first |
| GET | `/api/reports/latest`, `/api/reports/{date}` | report JSON |
| POST | `/api/reports/generate` | start generation (`X-Admin-Token`); blocked at nginx |
| GET | `/api/jobs/generate` | generation status |

## CI/CD (Jenkins)

`Jenkinsfile` builds and deploys the stack with `docker compose` on the Jenkins agent: checkout → preflight → lint/tests (`docker build --target test` per Dockerfile) → teardown → build & deploy → health check → smoke test via nginx. It reports every build to Discord.

Non-secret runtime settings (`TZ`, `HTTP_PORT`, `LLM_*`) are set in the `environment {}` block. Each build writes `.env` from them. Jenkins credentials (Secret text) the job needs:

| Credential ID | Used as |
| --- | --- |
| `news-admin-token` | `ADMIN_TOKEN` |
| `llama_cpp-llm-api-key` | `LLM_API_KEY` (any placeholder works for Ollama) |
| `discord-pws-builds-channel-webhook` | Discord webhook for build alerts |

The agent needs Docker with the compose plugin and the Discord Notifier plugin. Jenkins can run in a container against the host's docker daemon: checks run as a `test` stage inside each Dockerfile (sources travel as the build context, and cached layers skip unchanged dependency installs), and the stack uses no host bind mounts. Report history lives in the `pws-news_reports` and `pws-news_site` docker volumes, so wiping the workspace is safe; never run `docker compose down -v` on the agent.
