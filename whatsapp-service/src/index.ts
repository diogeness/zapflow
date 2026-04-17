import express from "express";
import path from "node:path";
import pino from "pino";

import { config } from "./config.js";
import { SessionManager } from "./session-manager.js";
import { createSessionsRouter } from "./routes/sessions.js";
import { createMessagesRouter } from "./routes/messages.js";
import { getMediaPath } from "./media.js";

const logger = pino({ name: "server" });

const app = express();
const manager = new SessionManager();

// ── Middleware ──
app.use(express.json({ limit: "50mb" }));

// API key authentication
app.use("/api", (req, res, next) => {
  if (config.apiKey) {
    const provided = req.headers["x-api-key"];
    if (provided !== config.apiKey) {
      res.status(401).json({ error: "unauthorized" });
      return;
    }
  }
  next();
});

// ── Health check ──
app.get("/health", (_req, res) => {
  res.json({ status: "ok", sessions: manager.list().length });
});

// ── Media serving ──
app.get("/media/:session/:filename", (req, res) => {
  const filepath = getMediaPath(req.params.session, req.params.filename);
  if (!filepath) {
    res.status(404).json({ error: "not found" });
    return;
  }
  res.sendFile(path.resolve(filepath));
});

// ── API routes ──
app.use("/api", createSessionsRouter(manager));
app.use("/api", createMessagesRouter(manager));

// ── Start server ──
async function waitForWebhookTarget(maxWait = 60_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    try {
      const resp = await fetch(config.webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event: "ping", session: "_healthcheck", payload: {} }),
        signal: AbortSignal.timeout(5_000),
      });
      // Any HTTP response (even 4xx) means the server is up
      logger.info({ status: resp.status }, "webhook target is reachable");
      return;
    } catch {
      logger.debug("waiting for webhook target (app)...");
      await new Promise((r) => setTimeout(r, 2_000));
    }
  }
  logger.warn("webhook target not reachable after timeout, proceeding anyway");
}

async function main() {
  // Start HTTP server first (so healthcheck passes and app container starts)
  await new Promise<void>((resolve) => {
    app.listen(config.port, "0.0.0.0", () => {
      logger.info({ port: config.port }, "whatsapp-service started");
      resolve();
    });
  });

  // Wait for Python app to be reachable before restoring sessions
  await waitForWebhookTarget();

  logger.info("restoring sessions...");
  await manager.restoreAll();
}

main().catch((err) => {
  logger.fatal({ err }, "failed to start");
  process.exit(1);
});
