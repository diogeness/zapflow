import httpx
import structlog
from app.config import Settings

logger = structlog.get_logger()


class WhatsAppClient:
    """Client for WhatsApp Service (Baileys) — manages sessions and message sending."""

    def __init__(self, settings: Settings):
        self.base_url = settings.WA_SERVICE_URL.rstrip("/")
        self.api_key = settings.WA_SERVICE_API_KEY
        callback_base = (settings.WA_WEBHOOK_CALLBACK_URL or settings.APP_BASE_URL).rstrip("/")
        self.webhook_url = callback_base + "/api/webhooks/waha"

    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=timeout)

    # ── Session Management ─────────────────────────────

    async def create_session(self, name: str) -> dict:
        """Create and start a new WAHA session with webhook config."""
        async with self._client() as client:
            payload = {
                "name": name,
                "config": {
                    "webhooks": [
                        {
                            "url": self.webhook_url,
                            "events": [
                                "message",
                                "session.status",
                            ],
                        }
                    ],
                },
            }
            resp = await client.post("/api/sessions", json=payload)
            if not resp.is_success:
                logger.error("waha.create_session_error", status=resp.status_code, text=resp.text)
            resp.raise_for_status()
            data = resp.json()
            logger.info("waha.session_created", name=name, status=data.get("status"))
            return data

    async def get_qr(self, name: str) -> str | None:
        """Fetch QR code as base64 data URI for a session awaiting scan.

        Returns the base64 string (data:image/png;base64,...) or None if not available.
        """
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"/api/{name}/auth/qr",
                    headers={**self._headers(), "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    mimetype = data.get("mimetype", "image/png")
                    value = data.get("data", "")
                    if value:
                        return f"data:{mimetype};base64,{value}"
                elif resp.status_code == 404:
                    # Session not in QR state
                    return None
                else:
                    logger.warning("waha.get_qr_unexpected", status=resp.status_code, text=resp.text)
        except Exception as e:
            logger.warning("waha.get_qr_failed", name=name, error=str(e))
        return None

    async def get_session(self, name: str) -> dict | None:
        """Get session info. Returns None if session does not exist."""
        try:
            async with self._client() as client:
                resp = await client.get(f"/api/sessions/{name}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None

    async def list_sessions(self) -> list[dict]:
        """List all sessions (including stopped)."""
        async with self._client() as client:
            resp = await client.get("/api/sessions", params={"all": "true"})
            resp.raise_for_status()
            return resp.json()

    async def delete_session(self, name: str) -> None:
        """Delete a session (stops + removes data)."""
        async with self._client() as client:
            resp = await client.delete(f"/api/sessions/{name}")
            # WAHA delete is idempotent — 404 is fine
            if resp.status_code not in (200, 404):
                resp.raise_for_status()
            logger.info("waha.session_deleted", name=name)

    async def restart_session(self, name: str) -> None:
        """Restart a session."""
        async with self._client() as client:
            resp = await client.post(f"/api/sessions/{name}/restart")
            resp.raise_for_status()
            logger.info("waha.session_restarted", name=name)

    async def logout_session(self, name: str) -> None:
        """Logout a session (clears WhatsApp auth so next start generates fresh QR)."""
        async with self._client() as client:
            resp = await client.post(f"/api/sessions/{name}/logout")
            resp.raise_for_status()
            logger.info("waha.session_logout", name=name)

    async def start_session(self, name: str) -> dict:
        """Start a previously stopped session (re-creates with same config)."""
        async with self._client() as client:
            payload = {
                "name": name,
                "config": {
                    "webhooks": [
                        {
                            "url": self.webhook_url,
                            "events": [
                                "message",
                                "session.status",
                            ],
                        }
                    ],
                },
            }
            resp = await client.post("/api/sessions/start", json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info("waha.session_started", name=name)
            return data

    # ── Sending Messages ───────────────────────────────

    @staticmethod
    def _to_chat_id(phone: str) -> str:
        """Ensure phone number is in WAHA chatId format (number@c.us).

        Accepts: '5511999999999', '5511999999999@c.us', '5511999999999@s.whatsapp.net',
                 or LID format '76029729198206@lid'.
        """
        if "@" in phone:
            return phone
        return f"{phone}@c.us"

    async def send_text(self, session: str, phone: str, text: str) -> dict:
        """Send a text message."""
        async with self._client() as client:
            resp = await client.post(
                "/api/sendText",
                json={
                    "session": session,
                    "chatId": self._to_chat_id(phone),
                    "text": text,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def send_image(self, session: str, phone: str, image_url: str, caption: str = "", view_once: bool = False) -> dict:
        """Send an image message."""
        async with self._client(timeout=120) as client:
            payload = {
                    "session": session,
                    "chatId": self._to_chat_id(phone),
                    "file": {"mimetype": "image/jpeg", "url": image_url},
                    "caption": caption,
            }
            if view_once:
                payload["viewOnce"] = True
            resp = await client.post(
                "/api/sendImage",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def send_document(
        self, session: str, phone: str, doc_url: str, caption: str = "", filename: str = "document"
    ) -> dict:
        """Send a document/file."""
        import mimetypes
        mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        async with self._client(timeout=120) as client:
            resp = await client.post(
                "/api/sendFile",
                json={
                    "session": session,
                    "chatId": self._to_chat_id(phone),
                    "file": {"mimetype": mimetype, "url": doc_url, "filename": filename},
                    "caption": caption,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def send_audio_ptt(self, session: str, phone: str, audio_url: str) -> dict:
        """Send a voice (PTT) message."""
        async with self._client(timeout=120) as client:
            resp = await client.post(
                "/api/sendVoice",
                json={
                    "session": session,
                    "chatId": self._to_chat_id(phone),
                    "file": {"url": audio_url},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def send_video(self, session: str, phone: str, video_url: str, caption: str = "", view_once: bool = False) -> dict:
        """Send a video message."""
        async with self._client(timeout=120) as client:
            payload = {
                    "session": session,
                    "chatId": self._to_chat_id(phone),
                    "file": {"mimetype": "video/mp4", "url": video_url},
                    "caption": caption,
            }
            if view_once:
                payload["viewOnce"] = True
            resp = await client.post(
                "/api/sendVideo",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Presence / Typing Status ────────────────────────

    async def set_presence(self, session: str, phone: str, status: str = "typing") -> None:
        """Send typing or recording presence.

        status: 'typing' | 'recording' | 'paused'
        """
        try:
            async with self._client() as client:
                await client.post(
                    f"/api/{session}/presence",
                    json={"chatId": self._to_chat_id(phone), "presence": status},
                )
        except Exception as e:
            logger.warning("waha.presence_failed", error=str(e))

    # ── Media Download ──────────────────────────────────

    async def download_media(self, media_url: str) -> bytes | None:
        """Download media from a WAHA media URL.

        WAHA provides direct download URLs in webhook payloads (media.url field).
        """
        if not media_url:
            return None
        try:
            async with self._client() as client:
                resp = await client.get(media_url)
                if resp.status_code == 200:
                    return resp.content
        except Exception as e:
            logger.warning("waha.media_download_failed", url=media_url, error=str(e))
        return None

    # ── Contacts ────────────────────────────────────────

    async def get_contacts(self, session_name: str) -> list[str]:
        """Get saved contacts (phone numbers) for a session."""
        try:
            async with self._client() as client:
                resp = await client.get(f"/api/sessions/{session_name}/contacts")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning("waha.contacts_fetch_failed", session=session_name, error=str(e))
        return []

    async def get_session_health(self, session_name: str) -> dict | None:
        """Get session health/diagnostic info from whatsapp-service."""
        try:
            async with self._client() as client:
                resp = await client.get(f"/api/sessions/{session_name}/health")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning("waha.health_fetch_failed", session=session_name, error=str(e))
        return None

    async def resume_antiban(self, session_name: str) -> bool:
        """Resume message sending after anti-ban auto-pause."""
        try:
            async with self._client() as client:
                resp = await client.post(f"/api/sessions/{session_name}/antiban/resume")
                return resp.status_code == 200
        except Exception as e:
            logger.warning("waha.resume_antiban_failed", session=session_name, error=str(e))
        return False
