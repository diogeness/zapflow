import os
import re
import json
import shutil
import structlog
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from typing import List
from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.auth import get_current_user
from app.models.campaign import Campaign, CampaignStep
from app.models.lead import Lead
from app.models.instance import Instance
from app.schemas.campaign import CampaignCreate, CampaignClone, CampaignUpdate, StepReorder, StepUpdate

logger = structlog.get_logger()

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

MEDIA_DIR = "data/media"
MAX_MEDIA_SIZE = 25 * 1024 * 1024  # 25MB

# Allowed MIME types for media uploads
ALLOWED_MEDIA_TYPES = {
    "image": {"image/jpeg", "image/png", "image/gif", "image/webp"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
    "document": {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "audio_ptt": {"audio/ogg", "audio/mpeg", "audio/wav", "audio/mp4"},
}


def _sanitize_filename(name: str) -> str:
    """Remove path traversal characters and non-safe characters."""
    # Remove path components
    name = os.path.basename(name)
    # Remove any non-alphanumeric except dots, hyphens, underscores
    name = re.sub(r'[^\w.\-]', '_', name)
    # Prevent hidden files
    name = name.lstrip('.')
    return name or 'file'


@router.post("")
async def create_campaign(
    body: CampaignCreate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    # Verify instance exists
    result = await session.execute(select(Instance).where(Instance.id == body.instance_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    campaign = Campaign(
        instance_id=body.instance_id,
        name=body.name,
        system_prompt=body.system_prompt,
        ai_model=body.ai_model,
        ai_temperature=body.ai_temperature,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)

    return {"id": campaign.id, "name": campaign.name}


@router.get("")
async def list_campaigns(
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Campaign).order_by(Campaign.created_at.desc()))
    return [{"id": c.id, "name": c.name, "instance_id": c.instance_id, "is_active": c.is_active} for c in result.scalars().all()]


@router.get("/list-cards")
async def list_campaign_cards(
    request: Request,
    instance_id: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return HTML partial of campaign cards for HTMX."""
    query = select(Campaign).order_by(Campaign.created_at.desc())
    if instance_id:
        query = query.where(Campaign.instance_id == instance_id)
    result = await session.execute(query)
    campaigns = list(result.scalars().all())

    enriched = []
    for c in campaigns:
        # Get instance name
        inst_result = await session.execute(select(Instance.name).where(Instance.id == c.instance_id))
        inst_name = inst_result.scalar_one_or_none() or "?"

        # Count steps
        steps_result = await session.execute(
            select(func.count(CampaignStep.id)).where(CampaignStep.campaign_id == c.id)
        )
        steps_count = steps_result.scalar() or 0

        # Count leads
        leads_result = await session.execute(
            select(func.count(Lead.id)).where(Lead.campaign_id == c.id)
        )
        leads_count = leads_result.scalar() or 0

        enriched.append(type("Camp", (), {
            "id": c.id, "name": c.name, "is_active": c.is_active,
            "system_prompt": c.system_prompt, "instance_name": inst_name,
            "steps_count": steps_count, "leads_count": leads_count,
        })())

    from app.web.templates import templates
    return templates.TemplateResponse(request, "campaigns/_cards.html", {"campaigns": enriched})


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    # Guard: only one active campaign per instance
    if body.is_active is True and not campaign.is_active:
        conflict = await session.execute(
            select(Campaign.id, Campaign.name).where(
                Campaign.instance_id == campaign.instance_id,
                Campaign.is_active == True,
                Campaign.id != campaign_id,
            )
        )
        existing = conflict.first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Já existe uma campanha ativa nesta instância: \"{existing.name}\". Pause-a antes de ativar esta.",
            )

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)

    await session.commit()
    return {"ok": True}


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    # Delete associated steps
    steps_result = await session.execute(
        select(CampaignStep).where(CampaignStep.campaign_id == campaign_id)
    )
    for step in steps_result.scalars().all():
        await session.delete(step)

    # Delete associated leads and their messages
    from app.models.message import Message
    leads_result = await session.execute(
        select(Lead).where(Lead.campaign_id == campaign_id)
    )
    for lead in leads_result.scalars().all():
        msgs_result = await session.execute(
            select(Message).where(Message.lead_id == lead.id)
        )
        for msg in msgs_result.scalars().all():
            await session.delete(msg)
        await session.delete(lead)

    await session.delete(campaign)
    await session.commit()
    return {"ok": True}


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    return campaign


# ── Clone ────────────────────────────────────────

@router.post("/{campaign_id}/clone")
async def clone_campaign(
    campaign_id: int,
    body: CampaignClone,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Clone a campaign with all steps and media to a (possibly different) instance."""
    # Verify source
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    # Verify target instance
    inst_result = await session.execute(select(Instance).where(Instance.id == body.instance_id))
    if not inst_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Instância não encontrada")

    # Create new campaign (always inactive)
    new_campaign = Campaign(
        instance_id=body.instance_id,
        name=body.name,
        system_prompt=source.system_prompt,
        ai_model=source.ai_model,
        ai_temperature=source.ai_temperature,
        is_active=False,
        ignore_groups=source.ignore_groups,
        ignore_contacts=source.ignore_contacts,
        ai_enabled=source.ai_enabled,
        max_ai_interactions=source.max_ai_interactions,
    )
    session.add(new_campaign)
    await session.flush()  # get new_campaign.id

    # Copy context file
    if source.context_file and os.path.isfile(source.context_file):
        new_dir = os.path.join(MEDIA_DIR, str(new_campaign.id))
        os.makedirs(new_dir, exist_ok=True)
        new_context_path = os.path.join(new_dir, os.path.basename(source.context_file))
        shutil.copy2(source.context_file, new_context_path)
        new_campaign.context_file = new_context_path

    # Copy steps
    steps_result = await session.execute(
        select(CampaignStep).where(CampaignStep.campaign_id == campaign_id).order_by(CampaignStep.step_order)
    )
    for step in steps_result.scalars().all():
        media_entries = []
        # Duplicate media files
        for media in step.media_urls_parsed:
            src_path = media.get("path", "")
            if src_path and os.path.isfile(src_path):
                new_dir = os.path.join(MEDIA_DIR, str(new_campaign.id))
                os.makedirs(new_dir, exist_ok=True)
                new_filename = f"step_{step.step_order}_{len(media_entries)}_{_sanitize_filename(media.get('name', 'file'))}"
                new_path = os.path.join(new_dir, new_filename)
                shutil.copy2(src_path, new_path)
                media_entries.append({"path": new_path, "name": media.get("name", "file")})

        new_step = CampaignStep(
            campaign_id=new_campaign.id,
            step_order=step.step_order,
            message_type=step.message_type,
            content=step.content,
            media_urls=json.dumps(media_entries) if media_entries else None,
            delay_seconds=step.delay_seconds,
            wait_reply=step.wait_reply,
            view_once=step.view_once,
        )
        session.add(new_step)

    await session.commit()
    await session.refresh(new_campaign)
    logger.info("campaign.cloned", source_id=campaign_id, new_id=new_campaign.id)
    return {"id": new_campaign.id, "name": new_campaign.name}


# ── Steps ────────────────────────────────────────

@router.post("/{campaign_id}/steps")
async def add_step(
    campaign_id: int,
    message_type: str = Form(...),
    content: str = Form(""),
    delay_seconds: int = Form(3),
    wait_reply: bool = Form(True),
    view_once: bool = Form(False),
    media: List[UploadFile] = File([]),
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    # Verify campaign exists
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    # Get next step order
    result = await session.execute(
        select(func.max(CampaignStep.step_order)).where(CampaignStep.campaign_id == campaign_id)
    )
    max_val = result.scalar()
    max_order = max_val if max_val is not None else -1
    next_order = max_order + 1

    # First step (order 0) must always wait for reply
    if next_order == 0:
        wait_reply = True

    media_entries = []
    # Filter out empty file entries (browser sends empty file when no file selected)
    actual_files = [f for f in media if f.filename]
    for idx, file in enumerate(actual_files):
        # Validate MIME type
        content_type = file.content_type or ""
        allowed = ALLOWED_MEDIA_TYPES.get(message_type, set())
        if allowed and content_type not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de arquivo não permitido para {message_type}. Tipos aceitos: {', '.join(allowed)}",
            )

        file_content = await file.read()

        # Validate file size
        if len(file_content) > MAX_MEDIA_SIZE:
            raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo: {MAX_MEDIA_SIZE // (1024*1024)}MB")

        # Save media file
        campaign_media_dir = os.path.join(MEDIA_DIR, str(campaign_id))
        os.makedirs(campaign_media_dir, exist_ok=True)
        safe_name = f"step_{next_order}_{idx}_{_sanitize_filename(file.filename)}"
        media_path = os.path.join(campaign_media_dir, safe_name)
        with open(media_path, "wb") as f:
            f.write(file_content)
        media_entries.append({"path": media_path, "name": file.filename})

    step = CampaignStep(
        campaign_id=campaign_id,
        step_order=next_order,
        message_type=message_type,
        content=content,
        media_urls=json.dumps(media_entries) if media_entries else None,
        delay_seconds=delay_seconds,
        wait_reply=wait_reply,
        view_once=view_once if message_type in ("image", "video") else False,
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)

    return {"id": step.id, "step_order": step.step_order}


@router.put("/{campaign_id}/steps/reorder")
async def reorder_steps(
    campaign_id: int,
    body: StepReorder,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    for idx, step_id in enumerate(body.step_ids):
        result = await session.execute(
            select(CampaignStep).where(
                CampaignStep.id == step_id,
                CampaignStep.campaign_id == campaign_id,
            )
        )
        step = result.scalar_one_or_none()
        if step:
            step.step_order = idx
            # First step must always wait for reply
            if idx == 0:
                step.wait_reply = True

    await session.commit()
    return {"ok": True}


@router.patch("/{campaign_id}/steps/{step_id}")
async def update_step(
    campaign_id: int,
    step_id: int,
    body: StepUpdate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(
        select(CampaignStep).where(
            CampaignStep.id == step_id,
            CampaignStep.campaign_id == campaign_id,
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step não encontrado")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(step, field, value)

    # First step must always wait for reply
    if step.step_order == 0:
        step.wait_reply = True

    await session.commit()
    return {"ok": True}


@router.delete("/{campaign_id}/steps/{step_id}")
async def delete_step(
    campaign_id: int,
    step_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(
        select(CampaignStep).where(
            CampaignStep.id == step_id,
            CampaignStep.campaign_id == campaign_id,
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step não encontrado")

    await session.delete(step)
    await session.commit()
    return {"ok": True}


@router.post("/{campaign_id}/steps/{step_id}/media")
async def add_step_media(
    campaign_id: int,
    step_id: int,
    media: List[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Add media files to an existing step."""
    result = await session.execute(
        select(CampaignStep).where(
            CampaignStep.id == step_id,
            CampaignStep.campaign_id == campaign_id,
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step não encontrado")

    existing = json.loads(step.media_urls) if step.media_urls else []
    actual_files = [f for f in media if f.filename]

    for idx, file in enumerate(actual_files):
        content_type = file.content_type or ""
        allowed = ALLOWED_MEDIA_TYPES.get(step.message_type, set())
        if allowed and content_type not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de arquivo não permitido para {step.message_type}.",
            )
        file_content = await file.read()
        if len(file_content) > MAX_MEDIA_SIZE:
            raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo: {MAX_MEDIA_SIZE // (1024*1024)}MB")

        campaign_media_dir = os.path.join(MEDIA_DIR, str(campaign_id))
        os.makedirs(campaign_media_dir, exist_ok=True)
        safe_name = f"step_{step.step_order}_{len(existing)+idx}_{_sanitize_filename(file.filename)}"
        media_path = os.path.join(campaign_media_dir, safe_name)
        with open(media_path, "wb") as f:
            f.write(file_content)
        existing.append({"path": media_path, "name": file.filename})

    step.media_urls = json.dumps(existing)
    await session.commit()
    return {"ok": True, "media_urls": existing}


@router.delete("/{campaign_id}/steps/{step_id}/media/{media_index}")
async def remove_step_media(
    campaign_id: int,
    step_id: int,
    media_index: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Remove a specific media file from a step by index."""
    result = await session.execute(
        select(CampaignStep).where(
            CampaignStep.id == step_id,
            CampaignStep.campaign_id == campaign_id,
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step não encontrado")

    entries = json.loads(step.media_urls) if step.media_urls else []
    if media_index < 0 or media_index >= len(entries):
        raise HTTPException(status_code=400, detail="Índice de mídia inválido")

    removed = entries.pop(media_index)
    # Delete file from disk
    if os.path.isfile(removed.get("path", "")):
        os.remove(removed["path"])

    step.media_urls = json.dumps(entries) if entries else None
    await session.commit()
    return {"ok": True}


# ── Context File ──────────────────────────────────

@router.post("/{campaign_id}/context-file")
async def upload_context_file(
    campaign_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    from app.config import get_settings
    settings = get_settings()

    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    # Validate file extension
    allowed_extensions = {".txt", ".md", ".csv", ".pdf", ".doc", ".docx"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Formato não suportado. Use: {', '.join(allowed_extensions)}")

    content = await file.read()
    if len(content) > settings.CONTEXT_FILE_MAX_SIZE:
        raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo: {settings.CONTEXT_FILE_MAX_SIZE // 1024}KB")

    # Save file
    campaign_media_dir = os.path.join(MEDIA_DIR, str(campaign_id))
    os.makedirs(campaign_media_dir, exist_ok=True)
    safe_name = f"context{ext}"
    file_path = os.path.join(campaign_media_dir, safe_name)
    with open(file_path, "wb") as f:
        f.write(content)

    # Remove old context file if different path
    if campaign.context_file and campaign.context_file != file_path and os.path.isfile(campaign.context_file):
        os.remove(campaign.context_file)

    campaign.context_file = file_path
    await session.commit()

    return {"ok": True, "path": file_path}


@router.delete("/{campaign_id}/context-file")
async def delete_context_file(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")

    if campaign.context_file and os.path.isfile(campaign.context_file):
        os.remove(campaign.context_file)

    campaign.context_file = None
    await session.commit()
    return {"ok": True}
