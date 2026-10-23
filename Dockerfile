# One runtime stage per service, selected by `target:` in docker-compose.yml.

FROM python:3.12-slim AS backend
WORKDIR /app
ENV SOURCES_FILE=/app/config/sources.yaml
COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/app ./app
COPY config ./config
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# Debian-based (not alpine) so TZ resolves against system tzdata for the 08:00 schedule.
FROM node:20-slim AS renderer
WORKDIR /app
COPY frontend/package.json .
COPY frontend/src ./src
CMD ["node", "src/main.js"]

FROM nginx:1.27-alpine AS nginx
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
