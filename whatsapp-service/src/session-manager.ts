import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  isLidUser,
  jidNormalizedUser,
  type WASocket,
  type BaileysEventMap,
  type proto,
  downloadMediaMessage,
  getContentType,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import fs from "node:fs";
import path from "node:path";
import pino from "pino";
import QRCode from "qrcode";

import { config } from "./config.js";
import { sendWebhook } from "./webhook.js";
import { saveMedia } from "./media.js";
import { RateLimiter } from "./rate-limiter.js";
import { HealthMonitor } from "./health-monitor.js";

const logger = pino({ name: "session-manager" });

interface WebhookConfig {
  url: string;
  events: string[];
}

interface SessionState {
  socket: WASocket | null;
  status: "STARTING" | "SCAN_QR_CODE" | "WORKING" | "STOPPED" | "FAILED";
  qrCode: string | null; // base64 PNG data (no data: prefix)
  phoneNumber: string | null;
  webhooks: WebhookConfig[];
  restartAttempts: number;
  contacts: Set<string>;
  lidMap: Map<string, string>; // LID -> phone number
  lastError: string | null;
  connectedSince: number | null; // epoch ms
  rateLimiter: RateLimiter;
  healthMonitor: HealthMonitor;
}

export class SessionManager {
  private sessions = new Map<string, SessionState>();
  private maxRestartAttempts = 5;

  /**
   * Restore all previously persisted sessions on startup.
   */
  async restoreAll(): Promise<void> {
    fs.mkdirSync(config.sessionsDir, { recursive: true });
    const dirs = fs.readdirSync(config.sessionsDir, { withFileTypes: true });
    for (const d of dirs) {
      if (d.isDirectory()) {
        logger.info({ session: d.name }, "restoring session");
        try {
          const webhooks = this.loadWebhooks(d.name);
          await this.create(d.name, webhooks);
        } catch (err) {
          logger.error({ session: d.name, err }, "failed to restore session");
        }
      }
    }
  }

  /**
   * Persist webhooks config to disk for a session.
   */
  private saveWebhooks(name: string, webhooks: WebhookConfig[]): void {
    try {
      const configPath = path.join(config.sessionsDir, name, "webhooks.json");
      fs.writeFileSync(configPath, JSON.stringify(webhooks, null, 2));
    } catch (err) {
      logger.warn({ name, err }, "failed to save webhooks config");
    }
  }

  /**
   * Load persisted webhooks config from disk.
   */
  private loadWebhooks(name: string): WebhookConfig[] {
    try {
      const configPath = path.join(config.sessionsDir, name, "webhooks.json");
      if (fs.existsSync(configPath)) {
        return JSON.parse(fs.readFileSync(configPath, "utf-8"));
      }
    } catch (err) {
      logger.warn({ name, err }, "failed to load webhooks config");
    }
    return [];
  }

  /**
   * Create and connect a new Baileys session.
   */
  async create(name: string, webhooks: WebhookConfig[] = []): Promise<SessionState> {
    // If already exists, return existing
    const existing = this.sessions.get(name);
    if (existing?.socket) {
      // Update webhooks if provided
      if (webhooks.length > 0) {
        existing.webhooks = webhooks;
        this.saveWebhooks(name, webhooks);
      }
      return existing;
    }

    // If no webhooks provided, try to load from disk
    if (webhooks.length === 0) {
      webhooks = this.loadWebhooks(name);
    } else {
      this.saveWebhooks(name, webhooks);
    }

    const state: SessionState = {
      socket: null,
      status: "STARTING",
      qrCode: null,
      phoneNumber: null,
      webhooks,
      restartAttempts: 0,
      contacts: new Set(),
      lidMap: new Map(),
      lastError: null,
      connectedSince: null,
      rateLimiter: new RateLimiter(),
      healthMonitor: new HealthMonitor(undefined, (level, score) => {
        sendWebhook("session.health", name, { riskLevel: level, riskScore: score, paused: level === "high" || level === "critical" });
      }),
    };
    this.sessions.set(name, state);

    await this.connect(name, state);
    return state;
  }

  private async connect(name: string, state: SessionState): Promise<void> {
    const authDir = path.join(config.sessionsDir, name);
    fs.mkdirSync(authDir, { recursive: true });

    const { state: authState, saveCreds } = await useMultiFileAuthState(authDir);
    const { version } = await fetchLatestBaileysVersion();

    const socket = makeWASocket({
      version,
      auth: {
        creds: authState.creds,
        keys: makeCacheableSignalKeyStore(authState.keys, pino({ level: "silent" })),
      },
      logger: pino({ level: "silent" }) as any,
      printQRInTerminal: false,
      generateHighQualityLinkPreview: false,
      syncFullHistory: false,
    });

    state.socket = socket;

    // ── Credential persistence ──
    socket.ev.on("creds.update", saveCreds);

    // ── Connection updates ──
    socket.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        // Convert QR string to base64 PNG
        try {
          const pngBase64 = await QRCode.toDataURL(qr, { type: "image/png", width: 300 });
          // pngBase64 is "data:image/png;base64,..." — strip prefix for API response
          state.qrCode = pngBase64.replace(/^data:image\/png;base64,/, "");
          state.status = "SCAN_QR_CODE";
          this.emitStatus(name, state);
        } catch (err) {
          logger.error({ name, err }, "QR generation failed");
        }
      }

      if (connection === "open") {
        state.status = "WORKING";
        state.qrCode = null;
        state.restartAttempts = 0;
        state.lastError = null;
        state.connectedSince = Date.now();

        // Extract phone number from user JID
        const me = socket.user;
        if (me?.id) {
          state.phoneNumber = me.id.split(":")[0].split("@")[0];
        }

        this.emitStatus(name, state);
        logger.info({ name, phone: state.phoneNumber }, "session connected");
      }

      if (connection === "close") {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const errorMessage = (lastDisconnect?.error as Boom)?.message || lastDisconnect?.error?.toString() || "";
        const loggedOut = statusCode === DisconnectReason.loggedOut;

        state.lastError = errorMessage || `disconnect (code: ${statusCode})`;
        state.connectedSince = null;

        // Track in health monitor
        state.healthMonitor.trackDisconnect();
        if (statusCode) {
          state.healthMonitor.trackError(statusCode, errorMessage);
        }

        if (loggedOut) {
          // Clear auth state and mark as stopped
          state.status = "STOPPED";
          state.socket = null;
          state.qrCode = null;
          state.phoneNumber = null;
          this.emitStatus(name, state);
          logger.info({ name }, "session logged out");
        } else {
          // Auto-reconnect with backoff
          state.restartAttempts++;
          if (state.restartAttempts <= this.maxRestartAttempts) {
            const delay = Math.min(state.restartAttempts * 2000, 15000);
            logger.info({ name, attempt: state.restartAttempts, delay }, "reconnecting");
            setTimeout(() => this.connect(name, state), delay);
          } else {
            state.status = "FAILED";
            state.socket = null;
            this.emitStatus(name, state);
            logger.error({ name }, "max reconnect attempts reached");
          }
        }
      }
    });

    // ── Contacts sync + LID mapping ──
    socket.ev.on("contacts.upsert", (contacts) => {
      for (const c of contacts) {
        if (c.id && c.id.endsWith("@s.whatsapp.net")) {
          state.contacts.add(c.id.split("@")[0]);
        }
        // Build LID -> phone map
        const lid = (c as any).lid;
        const jid = c.id;
        if (lid && jid && jid.endsWith("@s.whatsapp.net")) {
          const normalizedLid = jidNormalizedUser(lid);
          const phone = jid.split("@")[0];
          state.lidMap.set(normalizedLid, phone);
        }
      }
      logger.info({ name, totalContacts: state.contacts.size, lidMappings: state.lidMap.size }, "contacts synced");
    });

    // ── Phone number share (LID resolution) ──
    socket.ev.on("chats.phoneNumberShare", ({ lid, jid }) => {
      if (lid && jid) {
        const normalizedLid = jidNormalizedUser(lid);
        const phone = jid.split("@")[0];
        state.lidMap.set(normalizedLid, phone);
        logger.debug({ name, lid: normalizedLid, phone }, "LID resolved via phoneNumberShare");
      }
    });

    // ── Inbound messages ──
    socket.ev.on("messages.upsert", async ({ messages, type }) => {
      if (type !== "notify") return;

      for (const msg of messages) {
        try {
          await this.handleMessage(name, state, msg);
        } catch (err) {
          logger.error({ name, msgId: msg.key.id, err }, "message handling error");
        }
      }
    });
  }

  /**
   * Process a single inbound message and forward as webhook.
   */
  private async handleMessage(
    sessionName: string,
    state: SessionState,
    msg: proto.IWebMessageInfo,
  ): Promise<void> {
    const key = msg.key;
    if (!key.remoteJid) return;

    const fromMe = key.fromMe ?? false;
    let from = key.remoteJid;

    // Ignore status broadcasts and newsletters
    if (
      from === "status@broadcast" ||
      from.endsWith("@newsletter")
    ) {
      return;
    }

    // Resolve LID to phone number
    if (isLidUser(from)) {
      const normalizedLid = jidNormalizedUser(from);
      const phone = state.lidMap.get(normalizedLid);
      if (phone) {
        from = `${phone}@s.whatsapp.net`;
        logger.debug({ sessionName, lid: normalizedLid, resolved: from }, "LID resolved from map");
      } else {
        // Try onWhatsApp as fallback
        try {
          const results = await state.socket?.onWhatsApp(from);
          if (results?.[0]?.jid) {
            const resolvedPhone = results[0].jid.split("@")[0];
            state.lidMap.set(normalizedLid, resolvedPhone);
            from = results[0].jid;
            logger.info({ sessionName, lid: normalizedLid, resolved: from }, "LID resolved via onWhatsApp");
          } else {
            logger.warn({ sessionName, lid: from }, "could not resolve LID to phone number");
          }
        } catch (err) {
          logger.warn({ sessionName, lid: from, err }, "onWhatsApp LID resolution failed");
        }
      }
    }

    // Extract message content
    let message = msg.message;
    if (!message) return;

    let contentType = getContentType(message);
    let isViewOnce = false;

    // Unwrap viewOnce wrappers (v1 and v2) to get the inner message
    if (contentType === "viewOnceMessage" || contentType === "viewOnceMessageV2") {
      isViewOnce = true;
      const inner =
        (message as any).viewOnceMessage?.message ||
        (message as any).viewOnceMessageV2?.message;
      if (!inner) return;
      message = inner;
      contentType = getContentType(inner);
    }

    // At this point message is guaranteed non-null
    const msg_content = message!;

    let body = "";
    let hasMedia = false;
    let mediaMimetype = "";
    let mediaUrl = "";

    const pushName = msg.pushName || "";

    if (contentType === "conversation") {
      body = msg_content.conversation || "";
    } else if (contentType === "extendedTextMessage") {
      body = msg_content.extendedTextMessage?.text || "";
    } else if (contentType === "imageMessage") {
      body = msg_content.imageMessage?.caption || "";
      hasMedia = true;
      mediaMimetype = msg_content.imageMessage?.mimetype || "image/jpeg";
    } else if (contentType === "videoMessage") {
      body = msg_content.videoMessage?.caption || "";
      hasMedia = true;
      mediaMimetype = msg_content.videoMessage?.mimetype || "video/mp4";
    } else if (contentType === "audioMessage") {
      hasMedia = true;
      mediaMimetype = msg_content.audioMessage?.mimetype || "audio/ogg; codecs=opus";
    } else if (contentType === "documentMessage") {
      body = msg_content.documentMessage?.caption || msg_content.documentMessage?.fileName || "";
      hasMedia = true;
      mediaMimetype = msg_content.documentMessage?.mimetype || "application/octet-stream";
    } else {
      // Unsupported type — skip
      return;
    }

    // Download media if present
    if (hasMedia && state.socket) {
      try {
        const buffer = await downloadMediaMessage(
          msg,
          "buffer",
          {},
          {
            logger: pino({ level: "silent" }) as any,
            reuploadRequest: state.socket.updateMediaMessage,
          },
        );
        if (buffer) {
          const ext = mimeToExt(mediaMimetype);
          const relUrl = saveMedia(sessionName, key.id || "unknown", ext, buffer as Buffer);
          // Build absolute URL for the Python service to download
          mediaUrl = `http://whatsapp-service:${config.port}${relUrl}`;
        }
      } catch (err) {
        logger.warn({ sessionName, msgId: key.id, err }, "media download failed");
      }
    }

    // Build webhook payload in WAHA format
    const payload: Record<string, unknown> = {
      from,
      fromMe,
      id: key.id,
      notifyName: pushName,
      body,
      hasMedia,
      ...(hasMedia && { media: { url: mediaUrl, mimetype: mediaMimetype } }),
      ...(isViewOnce && { viewOnce: true }),
      _data: { pushName },
    };

    await sendWebhook("message", sessionName, payload);
  }

  /**
   * Send session status webhook.
   */
  private emitStatus(name: string, state: SessionState): void {
    const me = state.phoneNumber
      ? { id: `${state.phoneNumber}@c.us` }
      : null;

    sendWebhook("session.status", name, { status: state.status }, me)
      .catch((err) => logger.error({ name, err }, "emitStatus webhook error"));
  }

  /**
   * Get session info.
   */
  get(name: string): SessionState | undefined {
    return this.sessions.get(name);
  }

  /**
   * List all sessions.
   */
  list(): { name: string; status: string; phone: string | null }[] {
    const result: { name: string; status: string; phone: string | null }[] = [];
    for (const [name, state] of this.sessions) {
      result.push({ name, status: state.status, phone: state.phoneNumber });
    }
    // Also include persisted-but-not-loaded sessions
    try {
      const dirs = fs.readdirSync(config.sessionsDir, { withFileTypes: true });
      for (const d of dirs) {
        if (d.isDirectory() && !this.sessions.has(d.name)) {
          result.push({ name: d.name, status: "STOPPED", phone: null });
        }
      }
    } catch {
      // ignore
    }
    return result;
  }

  /**
   * Delete a session: logout from WhatsApp, close socket, remove auth files, remove from map.
   */
  async delete(name: string): Promise<void> {
    const state = this.sessions.get(name);
    if (state?.socket) {
      try {
        await state.socket.logout();
      } catch {
        // ignore logout errors (e.g. already disconnected)
      }
      try {
        state.socket.ev.removeAllListeners("connection.update");
        state.socket.ev.removeAllListeners("messages.upsert");
        state.socket.ev.removeAllListeners("creds.update");
        state.socket.ev.removeAllListeners("contacts.upsert");
        await state.socket.end(undefined);
      } catch {
        // ignore close errors
      }
    }
    this.sessions.delete(name);

    // Remove auth state directory
    const authDir = path.join(config.sessionsDir, name);
    if (fs.existsSync(authDir)) {
      fs.rmSync(authDir, { recursive: true, force: true });
    }

    logger.info({ name }, "session deleted");
  }

  /**
   * Restart a session: close and reconnect.
   */
  async restart(name: string): Promise<void> {
    const state = this.sessions.get(name);
    if (!state) throw new Error(`Session "${name}" not found`);

    if (state.socket) {
      try {
        state.socket.ev.removeAllListeners("connection.update");
        state.socket.ev.removeAllListeners("messages.upsert");
        state.socket.ev.removeAllListeners("creds.update");
        await state.socket.end(undefined);
      } catch {
        // ignore
      }
    }
    state.socket = null;
    state.status = "STARTING";
    state.qrCode = null;
    state.restartAttempts = 0;

    await this.connect(name, state);
    logger.info({ name }, "session restarted");
  }

  /**
   * Logout: clear auth state so next connect generates a fresh QR.
   */
  async logout(name: string): Promise<void> {
    const state = this.sessions.get(name);
    if (!state) throw new Error(`Session "${name}" not found`);

    if (state.socket) {
      try {
        await state.socket.logout();
      } catch {
        // ignore
      }
      try {
        state.socket.ev.removeAllListeners("connection.update");
        state.socket.ev.removeAllListeners("messages.upsert");
        state.socket.ev.removeAllListeners("creds.update");
        await state.socket.end(undefined);
      } catch {
        // ignore
      }
    }

    state.socket = null;
    state.status = "STOPPED";
    state.qrCode = null;
    state.phoneNumber = null;

    // Clear auth state files
    const authDir = path.join(config.sessionsDir, name);
    if (fs.existsSync(authDir)) {
      fs.rmSync(authDir, { recursive: true, force: true });
    }
    fs.mkdirSync(authDir, { recursive: true });

    this.emitStatus(name, state);
    logger.info({ name }, "session logged out");
  }

  /**
   * Get the underlying Baileys socket for sending messages.
   */
  getSocket(name: string): WASocket | null {
    return this.sessions.get(name)?.socket ?? null;
  }

  /**
   * Get saved contacts for a session.
   */
  getContacts(name: string): string[] {
    const state = this.sessions.get(name);
    return state ? [...state.contacts] : [];
  }

  /**
   * Re-upload pre-keys to WhatsApp servers by reconnecting the session.
   * This forces a fresh key exchange, fixing encryption issues.
   */
  async refreshKeys(name: string): Promise<void> {
    const state = this.sessions.get(name);
    if (!state) {
      throw new Error(`Session "${name}" not found`);
    }
    if (state.status !== "WORKING") {
      throw new Error(`Session "${name}" is not connected (status: ${state.status})`);
    }
    await this.restart(name);
  }

  /**
   * Check if sending is allowed by anti-ban system.
   * Returns { allowed, delayMs, reason? }.
   */
  checkSend(name: string, chatId: string, content: string): { allowed: boolean; delayMs: number; reason?: string } {
    const state = this.sessions.get(name);
    if (!state) return { allowed: false, delayMs: 0, reason: "session_not_found" };
    if (state.healthMonitor.isPaused()) {
      return { allowed: false, delayMs: 0, reason: "health_paused" };
    }
    return state.rateLimiter.check(chatId, content);
  }

  /**
   * Record a successfully sent message in the anti-ban system.
   */
  recordSend(name: string, chatId: string, content: string): void {
    const state = this.sessions.get(name);
    if (state) {
      state.rateLimiter.record(chatId, content);
    }
  }

  /**
   * Record a failed message send in the anti-ban system.
   */
  recordFailedSend(name: string): void {
    const state = this.sessions.get(name);
    if (state) {
      state.healthMonitor.trackFailedMessage();
    }
  }

  /**
   * Resume sending after anti-ban pause.
   */
  resumeAntiban(name: string): void {
    const state = this.sessions.get(name);
    if (state) {
      state.healthMonitor.resume();
    }
  }

  /**
   * Get health/diagnostic info for a session.
   */
  getHealth(name: string): Record<string, unknown> | null {
    const state = this.sessions.get(name);
    if (!state) return null;

    const isBadMac = state.lastError
      ? /bad.mac|decrypt|hmac/i.test(state.lastError)
      : false;

    // Use health monitor risk level as primary health indicator
    const healthReport = state.healthMonitor.getReport();
    const rateStats = state.rateLimiter.getStats();

    let health: "ok" | "warning" | "critical" = "ok";
    const diagnostics: { type: string; message: string; action: string }[] = [];

    // Map health monitor risk to health status
    if (healthReport.riskLevel === "critical") {
      health = "critical";
    } else if (healthReport.riskLevel === "high") {
      health = "critical";
    } else if (healthReport.riskLevel === "medium") {
      health = "warning";
    }

    if (healthReport.paused) {
      diagnostics.push({
        type: "antiban_paused",
        message: "Envio de mensagens pausado automaticamente pelo sistema anti-ban. Risco elevado de banimento detectado.",
        action: "monitor",
      });
    }

    if (state.status === "FAILED") {
      health = "critical";
      diagnostics.push({
        type: "failed",
        message: `Sessão falhou após ${state.restartAttempts} tentativas de reconexão.`,
        action: "reconnect",
      });
    }

    if (isBadMac) {
      health = "critical";
      diagnostics.push({
        type: "bad_mac",
        message:
          "Erro de criptografia detectado (Bad MAC). O estado de criptografia está corrompido — mensagens enviadas ficam ilegíveis para o destinatário. É necessário limpar a sessão e reconectar.",
        action: "reset_auth",
      });
    }

    if (state.restartAttempts > 0 && state.status !== "FAILED") {
      health = health === "ok" ? "warning" : health;
      diagnostics.push({
        type: "unstable",
        message: `Conexão instável — ${state.restartAttempts} reconexões automáticas.`,
        action: "monitor",
      });
    }

    if (state.status === "STOPPED") {
      health = "warning";
      diagnostics.push({
        type: "stopped",
        message: "Sessão parada. Necessário reconectar e escanear QR novamente.",
        action: "reconnect",
      });
    }

    return {
      status: state.status,
      phoneNumber: state.phoneNumber,
      restartAttempts: state.restartAttempts,
      contactsCount: state.contacts.size,
      connectedSince: state.connectedSince,
      lastError: state.lastError,
      health,
      diagnostics,
      antiban: {
        riskLevel: healthReport.riskLevel,
        riskScore: healthReport.riskScore,
        paused: healthReport.paused,
        disconnectsLastHour: healthReport.disconnectsLastHour,
        failedMessagesLastHour: healthReport.failedMessagesLastHour,
        events: healthReport.events,
        rateLimiter: rateStats,
      },
    };
  }
}

/** Map common MIME types to file extensions. */
function mimeToExt(mime: string): string {
  const base = mime.split(";")[0].trim();
  const map: Record<string, string> = {
    "audio/ogg": "ogg",
    "audio/ogg; codecs=opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "application/pdf": "pdf",
    "application/octet-stream": "bin",
  };
  return map[base] || base.split("/")[1] || "bin";
}
