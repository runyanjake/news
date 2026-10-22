FROM node:20-slim AS node

FROM python:3.12-slim AS backend
RUN python -m venv /opt/venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx tzdata \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default \
    && ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=backend /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY config ./config
COPY backend/app ./backend/app
COPY frontend/package.json ./frontend/
COPY frontend/src ./frontend/src

WORKDIR /app/frontend
VOLUME /app/data
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/api/health', timeout=4)"
CMD ["entrypoint.sh"]
