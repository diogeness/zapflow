# ── Build stage ──
FROM node:20-alpine AS builder
WORKDIR /build
COPY package.json ./
RUN apk add --no-cache git build-base python3 && \
  npm install
COPY tsconfig.json ./
COPY src/ ./src/
RUN npx tsc

# ── Runtime stage ──
FROM node:20-alpine
WORKDIR /app

RUN apk add --no-cache ffmpeg su-exec && \
  addgroup -S appgroup && adduser -S appuser -G appgroup

COPY --from=builder /build/package.json ./
COPY --from=builder /build/node_modules ./node_modules
COPY --from=builder /build/dist/ ./dist/

COPY entrypoint.sh /entrypoint.sh
RUN mkdir -p /data/sessions /data/media && chown -R appuser:appgroup /data \
  && chmod +x /entrypoint.sh

ENV PORT=3100
ENV SESSIONS_DIR=/data/sessions
ENV MEDIA_DIR=/data/media

EXPOSE 3100
ENTRYPOINT ["/entrypoint.sh"]

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD wget -q --spider http://localhost:3100/health || exit 1

CMD ["node", "--dns-result-order=ipv4first", "dist/index.js"]
