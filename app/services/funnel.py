import asyncio
import base64
import json
import mimetypes
import os
import structlog
from datetime import datetime, timezone
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignStep
from app.models.lead import Lead
from app.models.message import Message
from app.services.waha import WhatsAppClient
from app.services.groq_llm import GroqLLMClient
from app.services.groq_whisper import GroqWhisperClient
from app.services.humanizer import Humanizer
from app.config import Settings

logger = structlog.get_logger()

# Anti-flood: track recent messages per lead
_flood_tracker: dict[int, list[float]] = {}
FLOOD_WINDOW = 30.0  # seconds
FLOOD_MAX = 5  # max messages in window

# Debounce: pending AI responses per lead
_debounce_tasks: dict[int, asyncio.Task] = {}
_debounce_messages: dict[int, list[str]] = {}

# Per-lead locks to prevent race conditions on concurrent webhook messages
_lead_locks: dict[int, asyncio.Lock] = {}


def _get_lead_lock(lead_id: int) -> asyncio.Lock:
    """Get or create an asyncio.Lock for a specific lead."""
    if lead_id not in _lead_locks:
        _lead_locks[lead_id] = asyncio.Lock()
    return _lead_locks[lead_id]


class FunnelEngine:
    """Hybrid funnel engine: fixed script phase + AI phase.

    This engine is RECEPTIVE ONLY — it never initiates contact.
    The funnel starts when a lead sends the first message.
    """

    def __init__(
        self,
        settings: Settings,
        waha: WhatsAppClient,
        groq_llm: GroqLLMClient,
        groq_whisper: GroqWhisperClient,
        humanizer: Humanizer,
    ):
        self.waha = waha
        self.groq_llm = groq_llm
        self.groq_whisper = groq_whisper
        self.humanizer = humanizer
        self.history_limit = settings.HISTORY_LIMIT
        self.debounce_seconds = settings.AI_DEBOUNCE_SECONDS

    async def process_inbound(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        inbound_text: str,
        inbound_type: str = "text",
        audio_bytes: bytes | None = None,
        instance_name: str = "",
        evolution_msg_id: str = "",
    ) -> None:
        """Process an inbound message through the funnel."""

        # Anti-flood check
        if self._is_flooded(lead.id):
            logger.warning("funnel.flood_detected", lead_id=lead.id, phone=lead.phone)
            return

        # If lead is not active, skip
        if lead.status != "active":
            logger.info("funnel.lead_not_active", lead_id=lead.id, status=lead.status)
            return

        # Handle audio: transcribe first
        final_text = inbound_text
        if inbound_type == "audio" and audio_bytes:
            final_text = await self.groq_whisper.transcribe(audio_bytes)
            logger.info("funnel.audio_transcribed", lead_id=lead.id, text=final_text[:100])

        # Save inbound message
        inbound_msg = Message(
            lead_id=lead.id,
            direction="inbound",
            message_type=inbound_type,
            content=final_text,
            is_from_ai=False,
            evolution_msg_id=evolution_msg_id or None,
        )
        session.add(inbound_msg)
        lead.last_message_at = datetime.now(timezone.utc)
        lead.updated_at = datetime.now(timezone.utc)

        # Commit inbound message first so it's not lost if step sending fails
        await session.commit()

        # Acquire per-lead lock to prevent race conditions on concurrent messages
        async with _get_lead_lock(lead.id):
            # Re-fetch lead state from DB to get fresh current_step (another task may have advanced it)
            await session.refresh(lead)
            if lead.status != "active":
                return

            try:
                if lead.current_step >= 0:
                    # ── Fixed Phase ──
                    await self._handle_fixed_phase(session, lead, campaign, instance_name)
                elif campaign.ai_enabled:
                    # ── AI Phase (only if AI is enabled for this campaign) ──
                    if campaign.max_ai_interactions > 0 and lead.ai_interactions_count >= campaign.max_ai_interactions:
                        logger.info("funnel.ai_limit_reached", lead_id=lead.id, count=lead.ai_interactions_count, max=campaign.max_ai_interactions)
                    else:
                        await self._handle_ai_phase(session, lead, campaign, instance_name)
                else:
                    logger.info("funnel.ai_disabled", lead_id=lead.id, campaign_id=campaign.id)

                await session.commit()
            except Exception as e:
                logger.error("funnel.step_send_error", lead_id=lead.id, phone=lead.phone, error=str(e))
                await session.rollback()

    async def _is_lead_active(self, session: AsyncSession, lead: Lead) -> bool:
        """Check if lead still exists in DB and is active.

        Returns False if lead was deleted or status changed to non-active.
        """
        try:
            result = await session.execute(select(Lead).where(Lead.id == lead.id))
            fresh = result.scalar_one_or_none()
            if not fresh or fresh.status != "active":
                logger.info("funnel.lead_no_longer_active", lead_id=lead.id,
                            status=fresh.status if fresh else "deleted")
                return False
            return True
        except Exception:
            return False

    async def start_funnel(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        instance_name: str,
    ) -> None:
        """Start the funnel for a new lead by sending the first step."""
        async with _get_lead_lock(lead.id):
            steps = await self._get_steps(session, campaign.id)
            if not steps:
                # No fixed steps — go straight to AI phase
                lead.current_step = -1
                await session.commit()
                return

            if not await self._is_lead_active(session, lead):
                return

            lead.current_step = 0
            first_step = steps[0]
            await self.humanizer.step_delay(first_step.delay_seconds)
            await self._send_step(session, lead, first_step, instance_name, campaign)
            lead.current_step = 1
            await session.commit()  # Commit first step immediately

            # Continue sending next steps as long as they don't require waiting
            await self._advance_non_wait_steps(session, lead, campaign, steps, 1, instance_name)

            await session.commit()

    # ── Internal: Fixed Phase ──────────────────────────

    async def _handle_fixed_phase(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        instance_name: str,
    ) -> None:
        """Handle a message during the fixed script phase."""
        steps = await self._get_steps(session, campaign.id)
        current = lead.current_step

        if current >= len(steps):
            # All fixed steps done → transition to AI (if enabled)
            if campaign.ai_enabled:
                lead.current_step = -1
                await session.commit()
                if campaign.max_ai_interactions > 0 and lead.ai_interactions_count >= campaign.max_ai_interactions:
                    logger.info("funnel.ai_limit_reached", lead_id=lead.id, count=lead.ai_interactions_count, max=campaign.max_ai_interactions)
                else:
                    await self._handle_ai_phase(session, lead, campaign, instance_name)
            else:
                lead.current_step = -1
                await session.commit()
                logger.info("funnel.ai_disabled_after_steps", lead_id=lead.id, campaign_id=campaign.id)
            return

        if not await self._is_lead_active(session, lead):
            return

        step = steps[current]
        await self.humanizer.step_delay(step.delay_seconds)
        await self._send_step(session, lead, step, instance_name, campaign)
        lead.current_step = current + 1
        await session.commit()  # Commit step immediately to prevent data loss on later failure

        # Continue sending next steps as long as they don't require waiting
        await self._advance_non_wait_steps(session, lead, campaign, steps, current + 1, instance_name)

        # Check if we just finished all steps
        if lead.current_step >= len(steps):
            if campaign.ai_enabled:
                lead.current_step = -1
            else:
                lead.current_step = -1
                logger.info("funnel.ai_disabled_after_steps", lead_id=lead.id, campaign_id=campaign.id)
            await session.commit()

    async def _advance_non_wait_steps(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        steps: list[CampaignStep],
        start_idx: int,
        instance_name: str,
    ) -> None:
        """Send consecutive steps that don't require waiting for a reply."""
        for i in range(start_idx, len(steps)):
            step = steps[i]
            if step.wait_reply:
                break
            # Check if lead was deleted/blocked between steps
            if not await self._is_lead_active(session, lead):
                return
            await self.humanizer.step_delay(step.delay_seconds)
            await self._send_step(session, lead, step, instance_name, campaign)
            lead.current_step = i + 1
            await session.commit()  # Commit each step immediately
        if lead.current_step >= len(steps):
            lead.current_step = -1
            await session.commit()

    @staticmethod
    def _resolve_media(path: str) -> str:
        """Convert a local file path to base64 data URI for WAHA.

        WAHA accepts URLs or base64-encoded data in the file.data field.
        """
        if not path or path.startswith(("http://", "https://")):
            return path
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        logger.warning("funnel.media_file_not_found", path=path)
        return path

    def _apply_variables(self, text: str, lead: Lead, campaign: Campaign) -> str:
        """Replace template variables in step text."""
        display_phone = lead.phone.split("@")[0] if "@" in lead.phone else lead.phone
        return (
            text
            .replace("{nome}", lead.name or "")
            .replace("{telefone}", display_phone)
            .replace("{campanha}", campaign.name)
        )

    @staticmethod
    def _parse_media_urls(step: CampaignStep) -> list[dict]:
        """Parse media_urls JSON field into list of {path, name} dicts."""
        if not step.media_urls:
            return []
        try:
            return json.loads(step.media_urls)
        except (json.JSONDecodeError, TypeError):
            return []

    async def _send_media_with_retry(
        self,
        step_type: str,
        instance_name: str,
        phone: str,
        media: str,
        caption: str,
        filename: str = "document",
        max_retries: int = 2,
        view_once: bool = False,
    ) -> bool:
        """Send a single media file with retry logic. Returns True on success."""
        for attempt in range(max_retries + 1):
            try:
                if step_type == "image":
                    await self.waha.send_image(instance_name, phone, media, caption, view_once=view_once)
                elif step_type == "video":
                    await self.waha.send_video(instance_name, phone, media, caption, view_once=view_once)
                elif step_type == "document":
                    await self.waha.send_document(instance_name, phone, media, caption, filename=filename)
                elif step_type == "audio_ptt":
                    await self.waha.send_audio_ptt(instance_name, phone, media)
                return True
            except Exception as e:
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)  # 5s, 10s backoff
                    logger.warning(
                        "funnel.media_send_retry",
                        phone=phone, step_type=step_type,
                        attempt=attempt + 1, wait=wait, error=str(e),
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "funnel.media_send_failed",
                        phone=phone, step_type=step_type,
                        attempts=max_retries + 1, error=str(e),
                    )
                    return False
        return False

    async def _send_step(
        self,
        session: AsyncSession,
        lead: Lead,
        step: CampaignStep,
        instance_name: str,
        campaign: Campaign | None = None,
    ) -> None:
        """Send a single campaign step message (supports multiple media files)."""
        content = step.content
        if campaign:
            content = self._apply_variables(content, lead, campaign)

        media_entries = self._parse_media_urls(step)

        if step.message_type == "text":
            await self.humanizer.simulate_typing(instance_name, lead.phone, content)
            await self.waha.send_text(instance_name, lead.phone, content)
        elif step.message_type in ("image", "video", "document", "audio_ptt"):
            if media_entries:
                for idx, entry in enumerate(media_entries):
                    # Check if lead was deleted/blocked between media files
                    if idx > 0 and not await self._is_lead_active(session, lead):
                        return

                    media = self._resolve_media(entry.get("path", ""))
                    # Only send caption with the first media file
                    caption = content if idx == 0 else ""

                    # Add inter-media delay for multi-file steps (avoids WhatsApp rate-limiting)
                    if idx > 0:
                        await asyncio.sleep(3)

                    if step.message_type == "audio_ptt":
                        await self.humanizer.simulate_recording(instance_name, lead.phone)
                    else:
                        await self.humanizer.simulate_typing(instance_name, lead.phone, caption or "img")

                    filename = entry.get("name", "document")
                    success = await self._send_media_with_retry(
                        step.message_type, instance_name, lead.phone,
                        media, caption, filename=filename,
                        view_once=step.view_once,
                    )
                    if not success:
                        logger.error(
                            "funnel.step_media_skipped",
                            lead_id=lead.id, step_id=step.id,
                            media_index=idx, path=entry.get("path", ""),
                        )
            elif content:
                # Media type step with no files but has text content — send as text
                await self.humanizer.simulate_typing(instance_name, lead.phone, content)
                await self.waha.send_text(instance_name, lead.phone, content)

        # Save outbound message
        outbound = Message(
            lead_id=lead.id,
            direction="outbound",
            message_type=step.message_type,
            content=content,
            media_url=media_entries[0]["path"] if media_entries else None,
            is_from_ai=False,
        )
        session.add(outbound)

    # ── Internal: AI Phase ─────────────────────────────

    async def _handle_ai_phase(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        instance_name: str,
    ) -> None:
        """Handle a message during the AI phase with debounce.

        When multiple messages arrive quickly, they are collected and
        processed together after a short delay to avoid multiple LLM calls.
        """
        lead_id = lead.id

        # Cancel any pending debounce task for this lead
        existing_task = _debounce_tasks.get(lead_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        # Schedule debounced AI response
        _debounce_tasks[lead_id] = asyncio.create_task(
            self._debounced_ai_response(lead_id, campaign.id, campaign.instance_id, instance_name)
        )

    async def _debounced_ai_response(
        self,
        lead_id: int,
        campaign_id: int,
        instance_id: int,
        instance_name: str,
    ) -> None:
        """Wait for debounce period, then generate and send AI response."""
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return  # New message arrived — timer reset

        try:
            from app.database import async_session
            async with async_session() as session:
                # Re-fetch lead and campaign fresh
                result = await session.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()
                if not lead or lead.status != "active" or lead.current_step != -1:
                    return

                result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                campaign = result.scalar_one_or_none()
                if not campaign:
                    return

                # Check AI enabled and interaction limit
                if not campaign.ai_enabled:
                    return
                if campaign.max_ai_interactions > 0 and lead.ai_interactions_count >= campaign.max_ai_interactions:
                    logger.info("funnel.ai_limit_reached", lead_id=lead_id, count=lead.ai_interactions_count, max=campaign.max_ai_interactions)
                    return

                await self._execute_ai_response(session, lead, campaign, instance_name)
                await session.commit()
        except Exception as e:
            logger.error("funnel.debounced_ai_error", lead_id=lead_id, error=str(e))
        finally:
            _debounce_tasks.pop(lead_id, None)

    async def _execute_ai_response(
        self,
        session: AsyncSession,
        lead: Lead,
        campaign: Campaign,
        instance_name: str,
    ) -> None:
        """Build context and generate AI response."""
        # Get last N text/audio messages for context (ignore image/doc placeholders)
        history = await self._get_history(session, lead.id, self.history_limit)

        # Build system prompt with context file if available
        system_prompt = campaign.system_prompt
        if campaign.context_file and os.path.isfile(campaign.context_file):
            try:
                with open(campaign.context_file, "r", encoding="utf-8") as f:
                    context_content = f.read()
                system_prompt = (
                    "--- MATERIAL DE REFERÊNCIA ---\n"
                    f"{context_content}\n"
                    "--- FIM DO MATERIAL ---\n\n"
                    "Use o material acima como base de conhecimento para responder. "
                    "Siga as instruções do prompt abaixo.\n\n"
                    f"{campaign.system_prompt}"
                )
            except Exception as e:
                logger.warning("funnel.context_file_read_error", error=str(e))

        # Build messages array for LLM
        llm_messages = []
        for msg in history:
            role = "assistant" if msg.direction == "outbound" else "user"
            llm_messages.append({"role": role, "content": msg.content})

        # Generate AI response
        try:
            ai_response = await self.groq_llm.generate_response(
                system_prompt=system_prompt,
                messages_history=llm_messages,
                model=campaign.ai_model,
                temperature=campaign.ai_temperature,
            )
        except Exception as e:
            logger.error("funnel.ai_error", lead_id=lead.id, error=str(e))
            return

        if not ai_response:
            return

        # Send with human-like delay
        await self.humanizer.simulate_typing(instance_name, lead.phone, ai_response)
        await self.waha.send_text(instance_name, lead.phone, ai_response)

        # Save outbound AI message
        outbound = Message(
            lead_id=lead.id,
            direction="outbound",
            message_type="text",
            content=ai_response,
            is_from_ai=True,
        )
        session.add(outbound)

        # Increment AI interactions counter
        lead.ai_interactions_count += 1

    # ── Helpers ─────────────────────────────────────────

    async def _get_steps(self, session: AsyncSession, campaign_id: int) -> list[CampaignStep]:
        result = await session.execute(
            select(CampaignStep)
            .where(CampaignStep.campaign_id == campaign_id)
            .order_by(CampaignStep.step_order)
        )
        return list(result.scalars().all())

    async def _get_history(self, session: AsyncSession, lead_id: int, limit: int) -> list[Message]:
        """Get recent message history, filtering out image/document placeholders."""
        result = await session.execute(
            select(Message)
            .where(
                Message.lead_id == lead_id,
                Message.message_type.in_(["text", "audio"]),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()  # chronological order
        return messages

    def _is_flooded(self, lead_id: int) -> bool:
        """Anti-flood: check if lead is sending too many messages too fast."""
        import time
        now = time.time()

        if lead_id not in _flood_tracker:
            _flood_tracker[lead_id] = []

        # Remove old entries
        _flood_tracker[lead_id] = [t for t in _flood_tracker[lead_id] if now - t < FLOOD_WINDOW]
        _flood_tracker[lead_id].append(now)

        return len(_flood_tracker[lead_id]) > FLOOD_MAX
