# Debian-based (not alpine) so TZ resolves against system tzdata for the 08:00 schedule.
FROM node:20-slim

WORKDIR /app
COPY package.json .
COPY src ./src

CMD ["node", "src/main.js"]
