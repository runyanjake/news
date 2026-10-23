# Built from the repo root (see docker-compose.yml) so config/ can be baked in.
# Stages: base (runtime) -> test (lint + tests, CI only) -> final image = base. `docker build --target test`
# runs the checks against the exact layers that ship; cached layers make unchanged dependencies free.
FROM python:3.12-slim AS base

WORKDIR /app
ENV SOURCES_FILE=/app/config/sources.yaml
COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/app ./app
COPY config ./config

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
COPY backend/requirements-dev.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-dev.txt
COPY backend/pytest.ini .
COPY backend/tests ./tests
RUN ruff check --no-cache --select E9,F . && python -m pytest -q -p no:cacheprovider

FROM base
