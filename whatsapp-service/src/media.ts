import fs from "node:fs";
import path from "node:path";
import { config } from "./config.js";
import pino from "pino";

const logger = pino({ name: "media" });

/**
 * Ensure the media directory for a session exists and return its path.
 */
function sessionMediaDir(session: string): string {
  const dir = path.join(config.mediaDir, session);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * Save media bytes to disk and return the relative URL path for serving.
 */
export function saveMedia(
  session: string,
  msgId: string,
  ext: string,
  data: Buffer | Uint8Array,
): string {
  const dir = sessionMediaDir(session);
  const safeMsgId = msgId.replace(/[^a-zA-Z0-9_-]/g, "_");
  const filename = `${safeMsgId}.${ext}`;
  const filepath = path.join(dir, filename);
  fs.writeFileSync(filepath, data);
  logger.debug({ session, filename }, "media saved");
  return `/media/${session}/${filename}`;
}

/**
 * Resolve the absolute file path for a media file.
 */
export function getMediaPath(session: string, filename: string): string | null {
  const safeFilename = path.basename(filename);
  const filepath = path.join(config.mediaDir, session, safeFilename);
  if (fs.existsSync(filepath)) return filepath;
  return null;
}

/**
 * Fetch media from a URL or decode base64 data.
 * Supports: http(s) URLs and raw base64 strings.
 */
export async function resolveMedia(
  urlOrBase64: string,
): Promise<Buffer | null> {
  if (!urlOrBase64) return null;

  if (urlOrBase64.startsWith("http://") || urlOrBase64.startsWith("https://")) {
    try {
      const resp = await fetch(urlOrBase64, {
        signal: AbortSignal.timeout(30_000),
      });
      if (!resp.ok) return null;
      return Buffer.from(await resp.arrayBuffer());
    } catch {
      logger.warn({ url: urlOrBase64 }, "media download failed");
      return null;
    }
  }

  // Treat as base64
  try {
    return Buffer.from(urlOrBase64, "base64");
  } catch {
    return null;
  }
}
