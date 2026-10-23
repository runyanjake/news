# Debian-based (not alpine) so TZ resolves against system tzdata for the 08:00 schedule.
# Stages: base (runtime) -> test (CI only, `docker build --target test`) -> final image = base.
FROM node:20-slim AS base

WORKDIR /app
COPY package.json .
COPY src ./src

CMD ["node", "src/main.js"]

FROM base AS test
COPY test ./test
RUN find src test -name "*.js" -print0 | xargs -0 -n1 node --check && npm test --silent

FROM base
