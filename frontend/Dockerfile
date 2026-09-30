# syntax=docker/dockerfile:1
# Production frontend image (Deployment-2A): Next.js production server (no static export; three
# routes are dynamic). NEXT_PUBLIC_API_BASE_URL is inlined into the browser bundle at build time,
# so the image is built for one public origin (https://<APP_DOMAIN>); changing it needs a rebuild.
FROM node:24-bookworm-slim AS build
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
RUN test -n "$NEXT_PUBLIC_API_BASE_URL" || { echo "NEXT_PUBLIC_API_BASE_URL build arg is required" >&2; exit 1; }
RUN npm run build && rm -rf .next/cache

FROM node:24-bookworm-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build --chown=node:node /app/.next ./.next
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["npm", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
