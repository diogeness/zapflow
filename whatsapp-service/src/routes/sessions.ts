import { Router, type Request, type Response } from "express";
import type { SessionManager } from "../session-manager.js";

export function createSessionsRouter(manager: SessionManager): Router {
  const router = Router();

  // ── Create session ──
  router.post("/sessions", async (req: Request, res: Response) => {
    try {
      const { name, config: cfg } = req.body;
      if (!name) {
        res.status(400).json({ error: "name is required" });
        return;
      }

      const webhooks = cfg?.webhooks || [];
      const state = await manager.create(name, webhooks);

      res.json({
        name,
        status: state.status,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Start session (alias for create with config) ──
  router.post("/sessions/start", async (req: Request, res: Response) => {
    try {
      const { name, config: cfg } = req.body;
      if (!name) {
        res.status(400).json({ error: "name is required" });
        return;
      }

      const webhooks = cfg?.webhooks || [];
      const state = await manager.create(name, webhooks);

      res.json({
        name,
        status: state.status,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── List sessions ──
  router.get("/sessions", (_req: Request, res: Response) => {
    const sessions = manager.list();
    res.json(sessions);
  });

  // ── Get session info ──
  router.get("/sessions/:name", (req: Request, res: Response) => {
    const state = manager.get(req.params.name as string);
    if (!state) {
      res.status(404).json({ error: "session not found" });
      return;
    }
    res.json({
      name: req.params.name,
      status: state.status,
      phone: state.phoneNumber,
    });
  });

  // ── Delete session ──
  router.delete("/sessions/:name", async (req: Request, res: Response) => {
    try {
      await manager.delete(req.params.name as string);
      res.json({ ok: true });
    } catch {
      // Idempotent delete — 200 even if not found
      res.json({ ok: true });
    }
  });

  // ── Restart session ──
  router.post("/sessions/:name/restart", async (req: Request, res: Response) => {
    try {
      await manager.restart(req.params.name as string);
      res.json({ ok: true });
    } catch (err: any) {
      res.status(404).json({ error: err.message });
    }
  });

  // ── Logout session ──
  router.post("/sessions/:name/logout", async (req: Request, res: Response) => {
    try {
      await manager.logout(req.params.name as string);
      res.json({ ok: true });
    } catch (err: any) {
      res.status(404).json({ error: err.message });
    }
  });

  // ── Get contacts ──
  router.get("/sessions/:name/contacts", (req: Request, res: Response) => {
    const contacts = manager.getContacts(req.params.name as string);
    res.json(contacts);
  });

  // ── Refresh encryption keys ──
  router.post("/sessions/:name/refresh-keys", async (req: Request, res: Response) => {
    try {
      await manager.refreshKeys(req.params.name as string);
      res.json({ ok: true, message: "Pre-keys re-uploaded to WhatsApp servers" });
    } catch (err: any) {
      res.status(400).json({ error: err.message });
    }
  });

  // ── Get session health / diagnostics ──
  router.get("/sessions/:name/health", (req: Request, res: Response) => {
    const health = manager.getHealth(req.params.name as string);
    if (!health) {
      res.status(404).json({ error: "session not found" });
      return;
    }
    res.json(health);
  });

  // ── Resume anti-ban (unpause sending) ──
  router.post("/sessions/:name/antiban/resume", (req: Request, res: Response) => {
    try {
      manager.resumeAntiban(req.params.name as string);
      res.json({ ok: true, message: "Envio de mensagens retomado" });
    } catch (err: any) {
      res.status(404).json({ error: err.message });
    }
  });

  // ── Get QR code ──
  router.get("/:name/auth/qr", (req: Request, res: Response) => {
    const state = manager.get(req.params.name as string);
    if (!state || !state.qrCode) {
      res.status(404).json({ error: "QR not available" });
      return;
    }
    res.json({
      mimetype: "image/png",
      data: state.qrCode,
    });
  });

  return router;
}
