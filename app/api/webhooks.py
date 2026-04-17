import asyncio
import json
import structlog
from fastapi import APIRouter, Request, HTTPException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session
from app.models.instance import Instance
from app.models.campaign import Campaign
from app.models.lead import Lead

logger = structlog.get_logger()

# In-memory QR code store: {instance_name: base64_string}
_qrcode_store: dict[str, str] = {}


def get_qrcode_store() -> dict[str, str]:
    return _qrcode_store


router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/waha")
async def waha_webhook(request: Request):
    """Receive webhooks from WAHA (WhatsApp HTTP API).

    WAHA sends events with structure:
      {event: "message"|"session.status", session: "name", payload: {...}, me: {...}}
    """
    body = await request.json()
    event = body.get("event", "")
    session_name = body.get("session", "")

    if event == "ping":
        return {"ok": True}

    logger.info("webhook.received", webhook_event=event, session=session_name)

    if event == "message":
        asyncio.create_task(_handle_message(body, session_name))
    elif event == "session.status":
        asyncio.create_task(_handle_session_status(body, session_name))
    elif event == "session.health":
        logger.info("webhook.health_update", session=session_name,
                     risk_level=body.get("payload", {}).get("riskLevel"),
                     paused=body.get("payload", {}).get("paused"))
    else:
        logger.info("webhook.unmatched_event", event=event, session=session_name)

    return {"ok": True}


async def _handle_session_status(body: dict, session_name: str):
    """Update instance status in DB when WAHA session status changes.

    WAHA statuses: STOPPED, STARTING, SCAN_QR_CODE, WORKING, FAILED
    """
    try:
        payload = body.get("payload", {})
        status = payload.get("status", "")

        status_map = {
            "WORKING": "connected",
            "SCAN_QR_CODE": "connecting",
            "STARTING": "connecting",
            "STOPPED": "disconnected",
            "FAILED": "disconnected",
        }
        db_status = status_map.get(status, "disconnected")

        async with async_session() as session:
            result = await session.execute(
                select(Instance).where(Instance.name == session_name)
            )
            instance = result.scalar_one_or_none()
            if not instance:
                logger.warning("webhook.instance_not_found", name=session_name)
                return

            instance.status = db_status

            if db_status == "connected":
                # Clear QR code from store
                _qrcode_store.pop(session_name, None)
                # Extract phone number from me.id (e.g. "5511999999999@c.us")
                me = body.get("me") or {}
                me_id = me.get("id", "")
                if me_id:
                    instance.phone_number = me_id.split("@")[0]
                # Schedule contacts sync (slight delay to allow Baileys to populate)
                asyncio.create_task(_sync_contacts(session_name, instance.id))
            elif status == "SCAN_QR_CODE":
                # Fetch fresh QR code from WAHA API
                from app.main import get_app_services
                waha = get_app_services()["waha"]
                qr_data = await waha.get_qr(session_name)
                if qr_data:
                    _qrcode_store[session_name] = qr_data

            await session.commit()
            logger.info("webhook.session_status_updated", session=session_name, status=db_status)
    except Exception as e:
        logger.error("webhook.session_status_error", error=str(e))


async def _sync_contacts(session_name: str, instance_id: int):
    """Fetch contacts from WhatsApp service and store on Instance."""
    try:
        await asyncio.sleep(10)  # Wait for Baileys to sync contacts
        from app.main import get_app_services
        waha = get_app_services()["waha"]
        contacts = await waha.get_contacts(session_name)
        if contacts:
            async with async_session() as session:
                result = await session.execute(
                    select(Instance).where(Instance.id == instance_id)
                )
                instance = result.scalar_one_or_none()
                if instance:
                    instance.saved_contacts = json.dumps(contacts)
                    await session.commit()
                    logger.info("webhook.contacts_synced", session=session_name, count=len(contacts))
    except Exception as e:
        logger.warning("webhook.contacts_sync_error", error=str(e))


async def _handle_message(body: dict, session_name: str):
    """Process an inbound message from WAHA through the funnel.

    WAHA message payload:
      {from: "5511...@c.us", fromMe: bool, body: "text", hasMedia: bool,
       media: {url, mimetype, filename}, _data: {pushName: "..."}}
    """
    try:
        payload = body.get("payload", {})

        from_me = payload.get("fromMe", False)
        from_jid = payload.get("from", "")
        msg_id = payload.get("id", "")

        # pushName can be in payload directly or inside _data
        push_name = payload.get("notifyName", "")
        if not push_name:
            _data = payload.get("_data", {})
            if isinstance(_data, dict):
                push_name = _data.get("pushName", _data.get("notifyName", ""))

        logger.info(
            "webhook.message_received",
            session=session_name,
            from_me=from_me,
            from_jid=from_jid,
            push_name=push_name,
        )

        # Ignore outbound messages
        if from_me:
            return

        if not from_jid:
            return

        # Ignore group messages and status broadcasts
        is_group = "@g.us" in from_jid
        if "status@broadcast" in from_jid or "@newsletter" in from_jid:
            return

        # Normalize phone: strip JID suffix, but preserve @lid for unresolved LIDs
        if "@lid" in from_jid:
            # Keep full LID (e.g. "30962486374612@lid") so replies route correctly
            phone = from_jid
        else:
            phone = from_jid.split("@")[0]

        # Determine message type and content
        text_content = payload.get("body", "")
        has_media = payload.get("hasMedia", False)
        media = payload.get("media") or {}
        mimetype = media.get("mimetype", "")
        media_url = media.get("url", "")
        is_view_once = payload.get("viewOnce", False)

        msg_type = "text"
        audio_bytes = None

        if has_media and mimetype:
            if mimetype.startswith("audio/") or mimetype == "application/ogg":
                msg_type = "audio"
                if not text_content:
                    text_content = "[Áudio recebido]"
            elif mimetype.startswith("image/"):
                msg_type = "image"
                if not text_content:
                    text_content = "[Visualização única 📸]" if is_view_once else "[Imagem recebida]"
            elif mimetype.startswith("video/"):
                msg_type = "video"
                if not text_content:
                    text_content = "[Visualização única 🎬]" if is_view_once else "[Vídeo recebido]"
            else:
                msg_type = "document"
                if not text_content:
                    text_content = "[Documento recebido]"

        if not text_content and not has_media:
            logger.debug("webhook.empty_message", from_jid=from_jid)
            return

        async with async_session() as session:
            # Find instance
            result = await session.execute(
                select(Instance).where(Instance.name == session_name)
            )
            instance = result.scalar_one_or_none()
            if not instance:
                logger.warning("webhook.instance_not_found", name=session_name)
                return

            # Find active campaign for this instance
            result = await session.execute(
                select(Campaign).where(
                    Campaign.instance_id == instance.id,
                    Campaign.is_active == True,
                )
            )
            campaign = result.scalar_one_or_none()

            if not campaign:
                logger.info("webhook.no_active_campaign", session=session_name)
                return

            # Deduplicate: skip if this message was already processed
            if msg_id:
                from app.models.message import Message as MsgModel
                dup_result = await session.execute(
                    select(MsgModel).where(MsgModel.evolution_msg_id == msg_id).limit(1)
                )
                if dup_result.scalar_one_or_none():
                    logger.debug("webhook.duplicate_skipped", msg_id=msg_id)
                    return

            # Campaign-aware filters
            if is_group and campaign.ignore_groups:
                logger.debug("webhook.group_filtered", from_jid=from_jid)
                return

            if campaign.ignore_contacts and instance.saved_contacts:
                try:
                    contacts = json.loads(instance.saved_contacts)
                except (json.JSONDecodeError, TypeError):
                    contacts = []
                if phone in contacts:
                    logger.info("webhook.contact_filtered", phone=phone)
                    return

            # Find lead for this phone + campaign combination
            result = await session.execute(
                select(Lead).where(
                    Lead.instance_id == instance.id,
                    Lead.phone == phone,
                    Lead.campaign_id == campaign.id,
                )
            )
            lead = result.scalar_one_or_none()

            is_new_lead = lead is None
            if is_new_lead:
                lead = Lead(
                    campaign_id=campaign.id,
                    instance_id=instance.id,
                    phone=phone,
                    name=push_name or None,
                    current_step=0,
                    status="active",
                )
                session.add(lead)
                await session.flush()
                logger.info("webhook.new_lead", phone=phone, name=push_name)

            # Update name if we didn't have it
            if push_name and not lead.name:
                lead.name = push_name

            # Skip blocked/non-active leads early
            if lead.status in ("blocked", "completed"):
                logger.info("webhook.lead_skipped", lead_id=lead.id, status=lead.status)
                await session.commit()
                return

            # Download audio if needed
            if msg_type == "audio" and media_url:
                from app.main import get_app_services
                services = get_app_services()
                audio_bytes = await services["waha"].download_media(media_url)

            # Process through funnel
            from app.main import get_app_services
            services = get_app_services()
            funnel = services["funnel"]

            if is_new_lead:
                from app.models.message import Message
                from datetime import datetime, timezone

                # Transcribe audio before saving so we store the real text
                save_content = text_content
                if msg_type == "audio" and audio_bytes:
                    transcribed = await services["groq_whisper"].transcribe(audio_bytes)
                    if transcribed and not transcribed.startswith("[Erro"):
                        save_content = transcribed
                        logger.info("webhook.new_lead_audio_transcribed", phone=phone, text=transcribed[:100])

                inbound_msg = Message(
                    lead_id=lead.id,
                    direction="inbound",
                    message_type=msg_type,
                    content=save_content,
                    evolution_msg_id=msg_id,
                    is_from_ai=False,
                )
                session.add(inbound_msg)
                lead.last_message_at = datetime.now(timezone.utc)
                await session.commit()
                await funnel.start_funnel(session, lead, campaign, session_name)
            else:
                await funnel.process_inbound(
                    session=session,
                    lead=lead,
                    campaign=campaign,
                    inbound_text=text_content,
                    inbound_type=msg_type,
                    audio_bytes=audio_bytes,
                    instance_name=session_name,
                    evolution_msg_id=msg_id,
                )

    except Exception as e:
        logger.error("webhook.message_error", error=str(e), session=session_name)
