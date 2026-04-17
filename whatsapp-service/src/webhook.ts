import { config } from "./config.js";
import pino from "pino";

const logger = pino({ name: "webhook" });

const MAX_ATTEMPTS = 8;
const RETRY_DELAYS = [2000, 3000, 5000, 5000, 5000, 5000, 5000, 5000]; // longer backoff for startup race

/**
 * Send a webhook event to the FastAPI backend.
 * Format mirrors WAHA's webhook structure so the Python side needs zero changes.
 */
export async function sendWebhook(
  event: string,
  session: string,
  payload: Record<string, unknown>,
  me?: { id: string } | null,
): Promise<void> {
  const body: Record<string, unknown> = { event, session, payload };
  if (me) body.me = me;

  let attempt = 0;
  while (attempt < MAX_ATTEMPTS) {
    try {
      const resp = await fetch(config.webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(10_000),
      });
      if (!resp.ok) {
        logger.warn({ event, session, status: resp.status, attempt }, "webhook delivery failed (HTTP non-200)");
      } else {
        return; // success
      }
    } catch (err) {
      logger.warn({ event, session, err: err instanceof Error ? err.message : err, attempt }, "webhook delivery error");
    }
    
    attempt++;
    if (attempt < MAX_ATTEMPTS) {
      await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt - 1] || 5000));
    }
  }
  logger.error({ event, session }, `webhook delivery completely failed after ${MAX_ATTEMPTS} attempts`);
}
