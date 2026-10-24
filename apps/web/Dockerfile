FROM node:22-alpine AS deps
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_*는 빌드 시점에 번들에 들어간다. 기본값은 lib/api.ts(localhost:8000).
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_WS_URL
ARG NEXT_PUBLIC_VOICE_ENABLED
ARG NEXT_PUBLIC_MOCK
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-alpine
WORKDIR /app
# HOSTNAME=::는 IPv6·IPv4 둘 다 받는다. alpine의 localhost가 ::1이라 healthcheck(wget localhost)가 닿아야 한다.
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=::
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
