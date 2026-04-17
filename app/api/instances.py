import asyncio
import structlog
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.auth import get_current_user
from app.models.instance import Instance
from app.schemas.instance import InstanceCreate

logger = structlog.get_logger()

router = APIRouter(prefix="/api/instances", tags=["instances"])


@router.post("")
async def create_instance(
    body: InstanceCreate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Create a new WhatsApp instance and start connection (QR via webhook)."""
    # Check if name already exists
    result = await session.execute(select(Instance).where(Instance.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Nome de instância já existe")

    from app.main import get_app_services
    waha = get_app_services()["waha"]

    # Clean up orphaned WAHA session (from past failed deletes)
    try:
        existing = await waha.get_session(body.name)
        if existing:
            logger.warning("instance.orphan_cleanup", name=body.name)
            await waha.delete_session(body.name)
    except Exception:
        pass  # Non-critical — proceed with creation

    try:
        await waha.create_session(body.name)
        logger.info("instance.waha_session_created", name=body.name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro no WAHA: {str(e)}")

    from app.api.webhooks import get_qrcode_store
    qrcode_store = get_qrcode_store()

    # Fetch QR code (WAHA may need a moment to generate it)
    qr_data = await _fetch_qr_with_retry(waha, body.name, qrcode_store, max_attempts=5)

    # Save to DB
    instance = Instance(
        name=body.name,
        status="connecting",
    )
    session.add(instance)
    await session.commit()
    await session.refresh(instance)

    return {
        "id": instance.id,
        "name": instance.name,
        "status": instance.status,
        "qrcode": qrcode_store.get(body.name),
    }


async def _fetch_qr_with_retry(waha, name: str, qrcode_store: dict, max_attempts: int = 5) -> str | None:
    """Fetch QR code from WAHA with retries (session may need a moment to enter QR state)."""
    for attempt in range(1, max_attempts + 1):
        try:
            qr_data = await waha.get_qr(name)
            if qr_data:
                qrcode_store[name] = qr_data
                logger.info("instance.qr_fetched", name=name, attempt=attempt)
                return qr_data
        except Exception as e:
            logger.warning("instance.qr_fetch_failed", name=name, attempt=attempt, error=str(e))
        if attempt < max_attempts:
            await asyncio.sleep(2)
    logger.info("instance.qr_will_arrive_via_webhook", name=name)
    return None


@router.get("")
async def list_instances(
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Instance).order_by(Instance.created_at.desc()))
    return [{"id": i.id, "name": i.name, "phone_number": i.phone_number, "status": i.status} for i in result.scalars().all()]


@router.get("/list-cards")
async def list_instance_cards(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return HTML partial of instance cards for HTMX."""
    result = await session.execute(select(Instance).order_by(Instance.created_at.desc()))
    instances = list(result.scalars().all())

    # Enrich with QR code for connecting instances
    from app.api.webhooks import get_qrcode_store
    qrcode_store = get_qrcode_store()

    enriched = []
    for inst in instances:
        data = {"id": inst.id, "name": inst.name, "phone_number": inst.phone_number, "status": inst.status, "qrcode": None}
        if inst.status == "connecting":
            data["qrcode"] = qrcode_store.get(inst.name)
        enriched.append(type("Inst", (), data)())

    from app.web.templates import templates
    return templates.TemplateResponse(request, "instances/_cards.html", {"instances": enriched})


@router.post("/{instance_id}/reconnect")
async def reconnect_instance(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.main import get_app_services
    from app.api.webhooks import get_qrcode_store
    waha = get_app_services()["waha"]
    qrcode_store = get_qrcode_store()
    try:
        # Logout + restart to force new QR code generation
        try:
            await waha.logout_session(instance.name)
        except Exception:
            pass
        await waha.restart_session(instance.name)
        instance.status = "connecting"
        instance.updated_at = datetime.now(timezone.utc)
        await session.commit()

        # Fetch QR after restart
        await _fetch_qr_with_retry(waha, instance.name, qrcode_store, max_attempts=3)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"ok": True}


@router.get("/{instance_id}/qrcode")
async def get_instance_qrcode(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Actively fetch QR code for a connecting instance."""
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.api.webhooks import get_qrcode_store
    qrcode_store = get_qrcode_store()

    # Check if we already have a QR in store
    qr_data = qrcode_store.get(instance.name)
    if qr_data:
        return {"qrcode": qr_data}

    # If status is connecting and no QR, try to get one from WAHA
    if instance.status == "connecting":
        from app.main import get_app_services
        waha = get_app_services()["waha"]
        try:
            qr_data = await waha.get_qr(instance.name)
            if qr_data:
                qrcode_store[instance.name] = qr_data
                return {"qrcode": qr_data}
        except Exception as e:
            logger.warning("instance.qr_active_fetch_failed", name=instance.name, error=str(e))

    return {"qrcode": None}


@router.get("/{instance_id}/health")
async def get_instance_health(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Get health/diagnostic info for an instance's WhatsApp session."""
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.main import get_app_services
    waha = get_app_services()["waha"]

    health_data = await waha.get_session_health(instance.name)
    if not health_data:
        return {
            "status": instance.status,
            "phone": instance.phone_number,
            "health": "critical" if instance.status == "disconnected" else "warning",
            "diagnostics": [{
                "type": "unreachable",
                "message": "Serviço WhatsApp não respondeu. Verifique se o container está rodando.",
                "action": "check_service",
            }],
            "restart_attempts": 0,
            "contacts_count": 0,
            "connected_since": None,
            "last_error": None,
        }

    return {
        "status": health_data.get("status", instance.status),
        "phone": health_data.get("phoneNumber", instance.phone_number),
        "health": health_data.get("health", "ok"),
        "diagnostics": health_data.get("diagnostics", []),
        "restart_attempts": health_data.get("restartAttempts", 0),
        "contacts_count": health_data.get("contactsCount", 0),
        "connected_since": health_data.get("connectedSince"),
        "last_error": health_data.get("lastError"),
        "antiban": health_data.get("antiban"),
    }


@router.post("/{instance_id}/reset-auth")
async def reset_instance_auth(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Force full re-authentication: clear encryption keys and generate fresh QR.

    This fixes "Bad MAC" / corrupted encryption state by forcing a new key exchange.
    """
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.main import get_app_services
    from app.api.webhooks import get_qrcode_store
    waha = get_app_services()["waha"]
    qrcode_store = get_qrcode_store()

    try:
        # Full logout clears auth/encryption state files
        try:
            await waha.logout_session(instance.name)
        except Exception:
            pass
        # Restart to generate fresh QR for re-auth
        await waha.restart_session(instance.name)

        instance.status = "connecting"
        instance.phone_number = None
        instance.updated_at = datetime.now(timezone.utc)
        await session.commit()

        await _fetch_qr_with_retry(waha, instance.name, qrcode_store, max_attempts=3)
        logger.info("instance.auth_reset", name=instance.name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao redefinir sessão: {str(e)}")

    return {"ok": True, "message": "Sessão redefinida. Escaneie o novo QR Code para reconectar."}


@router.post("/{instance_id}/antiban/resume")
async def resume_antiban(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Resume message sending after anti-ban auto-pause."""
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.main import get_app_services
    waha = get_app_services()["waha"]

    success = await waha.resume_antiban(instance.name)
    if not success:
        raise HTTPException(status_code=502, detail="Não foi possível retomar o envio")

    return {"ok": True, "message": "Envio de mensagens retomado"}


@router.delete("/{instance_id}")
async def delete_instance(
    instance_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    from app.main import get_app_services
    from app.api.webhooks import get_qrcode_store
    waha = get_app_services()["waha"]
    try:
        await waha.delete_session(instance.name)
    except Exception as e:
        logger.warning("instance.waha_delete_failed", name=instance.name, error=str(e))

    # Clean up QR code from store
    get_qrcode_store().pop(instance.name, None)

    await session.delete(instance)
    await session.commit()

    return {"ok": True}
