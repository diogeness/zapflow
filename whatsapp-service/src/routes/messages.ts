import { Router, type Request, type Response } from "express";
import {
  type AnyMessageContent,
} from "@whiskeysockets/baileys";
import type { SessionManager } from "../session-manager.js";
import { resolveMedia } from "../media.js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import pino from "pino";

const execFileAsync = promisify(execFile);

const logger = pino({ name: "messages" });

/**
 * Generate a waveform array (64 samples, 0-100) from raw audio bytes.
 * WhatsApp uses this to render the frequency bars in PTT messages.
 */
function generateWaveform(buf: Buffer): Uint8Array {
  const samples = 64;
  const waveform = new Uint8Array(samples);
  const chunkSize = Math.max(1, Math.floor(buf.length / samples));

  for (let i = 0; i < samples; i++) {
    const start = i * chunkSize;
    const end = Math.min(start + chunkSize, buf.length);
    let sum = 0;
    for (let j = start; j < end; j++) {
      // Treat each byte as signed amplitude (-128..127)
      sum += Math.abs(buf[j]! - 128);
    }
    const avg = sum / (end - start);
    // Scale to 0-100 range; multiply to amplify subtle audio
    waveform[i] = Math.min(100, Math.round((avg / 128) * 100 * 2.5));
  }
  return waveform;
}

/**
 * Check if buffer is already OGG format (starts with "OggS" magic bytes).
 */
function isOgg(buf: Buffer): boolean {
  return buf.length >= 4 && buf[0] === 0x4f && buf[1] === 0x67 && buf[2] === 0x67 && buf[3] === 0x53;
}

/**
 * Convert audio buffer to OGG Opus using ffmpeg if it isn't already.
 * Supports WAV, MP3, and other formats ffmpeg can decode.
 */
async function ensureOggOpus(buf: Buffer): Promise<Buffer> {
  if (isOgg(buf)) return buf;

  const tmpDir = os.tmpdir();
  const inputPath = path.join(tmpDir, `ptt_in_${Date.now()}`);
  const outputPath = path.join(tmpDir, `ptt_out_${Date.now()}.ogg`);

  try {
    fs.writeFileSync(inputPath, buf);
    await execFileAsync("ffmpeg", [
      "-y", "-i", inputPath,
      "-ac", "1", "-ar", "48000",
      "-c:a", "libopus", "-b:a", "64k",
      outputPath,
    ]);
    return fs.readFileSync(outputPath);
  } catch (err) {
    logger.warn({ err }, "ffmpeg conversion failed, sending as-is");
    return buf;
  } finally {
    try { fs.unlinkSync(inputPath); } catch {}
    try { fs.unlinkSync(outputPath); } catch {}
  }
}

export function createMessagesRouter(manager: SessionManager): Router {
  const router = Router();

  // ── Helper: resolve chatId ──
  function toChatId(chatId: string): string {
    if (chatId.includes("@")) return chatId;
    return `${chatId}@s.whatsapp.net`;
  }

  // ── Send text ──
  router.post("/sendText", async (req: Request, res: Response) => {
    try {
      const { session, chatId, text } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.status(404).json({ error: `session "${session}" not connected` });
        return;
      }

      const jid = toChatId(chatId);

      // Anti-ban: check rate limits (informational only — never blocks sending)
      const check = manager.checkSend(session, jid, text || "");
      if (!check.allowed) {
        logger.warn({ session, jid, reason: check.reason }, "rate limit warning (sending anyway)");
      }

      // Anti-ban: apply recommended delay
      if (check.delayMs > 0) {
        await new Promise(r => setTimeout(r, check.delayMs));
      }

      try {
        const result = await socket.sendMessage(jid, { text });
        manager.recordSend(session, jid, text || "");
        res.json({ id: result?.key?.id, status: "sent", delayApplied: check.delayMs });
      } catch (sendErr: any) {
        manager.recordFailedSend(session);
        throw sendErr;
      }
    } catch (err: any) {
      logger.error({ err }, "sendText failed");
      res.status(500).json({ error: err.message });
    }
  });

  // ── Send image ──
  router.post("/sendImage", async (req: Request, res: Response) => {
    try {
      const { session, chatId, file, caption, viewOnce } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.status(404).json({ error: `session "${session}" not connected` });
        return;
      }

      const mediaBuffer = await resolveMedia(file?.url || "");
      if (!mediaBuffer) {
        res.status(400).json({ error: "could not resolve media" });
        return;
      }

      const jid = toChatId(chatId);

      // Anti-ban check (informational only — never blocks sending)
      const check = manager.checkSend(session, jid, caption || "[image]");
      if (!check.allowed) {
        logger.warn({ session, jid, reason: check.reason }, "rate limit warning (sending anyway)");
      }
      if (check.delayMs > 0) {
        await new Promise(r => setTimeout(r, check.delayMs));
      }

      const content: AnyMessageContent = {
        image: mediaBuffer,
        caption: caption || undefined,
        mimetype: file?.mimetype || "image/jpeg",
        ...(viewOnce && { viewOnce: true }),
      };

      try {
        const result = await socket.sendMessage(jid, content);
        manager.recordSend(session, jid, caption || "[image]");
        res.json({ id: result?.key?.id, status: "sent", delayApplied: check.delayMs });
      } catch (sendErr: any) {
        manager.recordFailedSend(session);
        throw sendErr;
      }
    } catch (err: any) {
      logger.error({ err }, "sendImage failed");
      res.status(500).json({ error: err.message });
    }
  });

  // ── Send file/document ──
  router.post("/sendFile", async (req: Request, res: Response) => {
    try {
      const { session, chatId, file, caption } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.status(404).json({ error: `session "${session}" not connected` });
        return;
      }

      const mediaBuffer = await resolveMedia(file?.url || "");
      if (!mediaBuffer) {
        res.status(400).json({ error: "could not resolve media" });
        return;
      }

      const jid = toChatId(chatId);

      // Anti-ban check (informational only — never blocks sending)
      const check = manager.checkSend(session, jid, caption || "[document]");
      if (!check.allowed) {
        logger.warn({ session, jid, reason: check.reason }, "rate limit warning (sending anyway)");
      }
      if (check.delayMs > 0) {
        await new Promise(r => setTimeout(r, check.delayMs));
      }

      const content: AnyMessageContent = {
        document: mediaBuffer,
        mimetype: file?.mimetype || "application/octet-stream",
        fileName: file?.filename || "document",
        caption: caption || undefined,
      };

      try {
        const result = await socket.sendMessage(jid, content);
        manager.recordSend(session, jid, caption || "[document]");
        res.json({ id: result?.key?.id, status: "sent", delayApplied: check.delayMs });
      } catch (sendErr: any) {
        manager.recordFailedSend(session);
        throw sendErr;
      }
    } catch (err: any) {
      logger.error({ err }, "sendFile failed");
      res.status(500).json({ error: err.message });
    }
  });

  // ── Send voice (PTT) ──
  router.post("/sendVoice", async (req: Request, res: Response) => {
    try {
      const { session, chatId, file } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.status(404).json({ error: `session "${session}" not connected` });
        return;
      }

      let mediaBuffer = await resolveMedia(file?.url || "");
      if (!mediaBuffer) {
        res.status(400).json({ error: "could not resolve media" });
        return;
      }

      const jid = toChatId(chatId);

      // Anti-ban check (informational only — never blocks sending)
      const check = manager.checkSend(session, jid, "[audio]");
      if (!check.allowed) {
        logger.warn({ session, jid, reason: check.reason }, "rate limit warning (sending anyway)");
      }
      if (check.delayMs > 0) {
        await new Promise(r => setTimeout(r, check.delayMs));
      }

      // Convert non-OGG audio (e.g. .wav, .mp3) to OGG Opus via ffmpeg
      mediaBuffer = await ensureOggOpus(mediaBuffer);

      // Generate waveform from audio bytes so WhatsApp shows the frequency bars
      const waveform = generateWaveform(mediaBuffer);

      // Estimate duration in seconds (OGG Opus ~6KB/s for voice)
      const seconds = Math.max(1, Math.round(mediaBuffer.length / 6000));

      const content = {
        audio: mediaBuffer,
        mimetype: "audio/ogg; codecs=opus",
        ptt: true,
        waveform,
        seconds,
      } as AnyMessageContent;

      try {
        const result = await socket.sendMessage(jid, content);
        manager.recordSend(session, jid, "[audio]");
        res.json({ id: result?.key?.id, status: "sent", delayApplied: check.delayMs });
      } catch (sendErr: any) {
        manager.recordFailedSend(session);
        throw sendErr;
      }
    } catch (err: any) {
      logger.error({ err }, "sendVoice failed");
      res.status(500).json({ error: err.message });
    }
  });

  // ── Send video ──
  router.post("/sendVideo", async (req: Request, res: Response) => {
    try {
      const { session, chatId, file, caption, viewOnce } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.status(404).json({ error: `session "${session}" not connected` });
        return;
      }

      const mediaBuffer = await resolveMedia(file?.url || "");
      if (!mediaBuffer) {
        res.status(400).json({ error: "could not resolve media" });
        return;
      }

      const jid = toChatId(chatId);

      // Anti-ban check (informational only — never blocks sending)
      const check = manager.checkSend(session, jid, caption || "[video]");
      if (!check.allowed) {
        logger.warn({ session, jid, reason: check.reason }, "rate limit warning (sending anyway)");
      }
      if (check.delayMs > 0) {
        await new Promise(r => setTimeout(r, check.delayMs));
      }

      const content: AnyMessageContent = {
        video: mediaBuffer,
        caption: caption || undefined,
        mimetype: file?.mimetype || "video/mp4",
        ...(viewOnce && { viewOnce: true }),
      };

      try {
        const result = await socket.sendMessage(jid, content);
        manager.recordSend(session, jid, caption || "[video]");
        res.json({ id: result?.key?.id, status: "sent", delayApplied: check.delayMs });
      } catch (sendErr: any) {
        manager.recordFailedSend(session);
        throw sendErr;
      }
    } catch (err: any) {
      logger.error({ err }, "sendVideo failed");
      res.status(500).json({ error: err.message });
    }
  });

  // ── Set presence (typing/recording) ──
  router.post("/:session/presence", async (req: Request, res: Response) => {
    try {
      const session = req.params.session as string;
      const { chatId, presence } = req.body;
      const socket = manager.getSocket(session);
      if (!socket) {
        res.json({ ok: true }); // Non-critical, don't fail
        return;
      }

      const jid = toChatId(chatId);
      const presenceMap: Record<string, string> = {
        typing: "composing",
        recording: "recording",
        paused: "paused",
      };
      const waPresence = presenceMap[presence] || "composing";
      await socket.sendPresenceUpdate(waPresence as any, jid);

      res.json({ ok: true });
    } catch {
      res.json({ ok: true }); // Presence is best-effort
    }
  });

  return router;
}
