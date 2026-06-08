FROM node:24-alpine AS builder

ARG API_INTERNAL_URL=http://localhost:8400
ARG WS_INTERNAL_URL=http://localhost:8400
ARG NEXT_PUBLIC_API_URL=/api
ARG NEXT_PUBLIC_ASK_HTTP_FALLBACK=0
ARG NEXT_PUBLIC_ASK_SSE_IDLE_TIMEOUT_MS=0

ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_ASK_HTTP_FALLBACK=${NEXT_PUBLIC_ASK_HTTP_FALLBACK}
ENV NEXT_PUBLIC_ASK_SSE_IDLE_TIMEOUT_MS=${NEXT_PUBLIC_ASK_SSE_IDLE_TIMEOUT_MS}

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:24-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 5173

ENV PORT=5173
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
