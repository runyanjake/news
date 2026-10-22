#!/usr/bin/env bash
# Runs the backend, renderer and nginx together; the container exits when any of them does.
mkdir -p /app/data/reports /app/data/site
trap 'kill $(jobs -p) 2>/dev/null' TERM INT

(cd /app/backend && exec uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000) &
(cd /app/frontend && exec node src/main.js) &
nginx -g 'daemon off;' &

wait -n
status=$?
kill $(jobs -p) 2>/dev/null
wait
exit $status
