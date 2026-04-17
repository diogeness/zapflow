import csv
import io
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlmodel import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.auth import get_current_user
from app.models.lead import Lead
from app.models.message import Message
from app.models.campaign import Campaign
from app.schemas.lead import LeadUpdate

logger = structlog.get_logger()

router = APIRouter(prefix="/api/leads", tags=["leads"])

PAGE_SIZE = 20


from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from app.web.templates import BR_TZ, to_brtime

@router.get("/table")
async def leads_table(
    request: Request,
    search: str = Query("", max_length=100),
    filter_campaign: str = Query("", max_length=20),
    filter_status: str = Query("", max_length=20),
    filter_interest: str = Query("", max_length=20),
    filter_date: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return HTML partial of leads table for HTMX."""
    query = select(Lead).order_by(Lead.last_message_at.desc().nullslast(), Lead.created_at.desc())

    if search:
        query = query.where(
            or_(Lead.name.icontains(search), Lead.phone.icontains(search))
        )
    if filter_campaign and filter_campaign.isdigit():
        query = query.where(Lead.campaign_id == int(filter_campaign))
    if filter_status:
        query = query.where(Lead.status == filter_status)
    if filter_interest:
        query = query.where(Lead.tab_interest_level == filter_interest)
    
    if filter_date:
        now = datetime.now(BR_TZ)
        if filter_date == "today":
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            query = query.where(Lead.last_message_at >= start_of_day)
        elif filter_date == "yesterday":
            start_of_yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            end_of_yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            query = query.where(Lead.last_message_at >= start_of_yesterday, Lead.last_message_at < end_of_yesterday)
        elif filter_date == "last_7":
            start_of_period = (now - timedelta(days=7)).astimezone(timezone.utc)
            query = query.where(Lead.last_message_at >= start_of_period)
        elif filter_date == "last_30":
            start_of_period = (now - timedelta(days=30)).astimezone(timezone.utc)
            query = query.where(Lead.last_message_at >= start_of_period)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total_leads = total_result.scalar() or 0
    total_pages = max(1, (total_leads + PAGE_SIZE - 1) // PAGE_SIZE)

    # Paginate
    query = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    result = await session.execute(query)
    leads = list(result.scalars().all())

    # Enrich with campaign name (use dicts to add extra fields)
    enriched = []
    for lead in leads:
        camp_result = await session.execute(select(Campaign.name).where(Campaign.id == lead.campaign_id))
        campaign_name = camp_result.scalar_one_or_none() or "?"
        lead_dict = lead.model_dump()
        lead_dict["campaign_name"] = campaign_name
        enriched.append(type("LeadRow", (), lead_dict)())

    from app.web.templates import templates
    return templates.TemplateResponse(request, "leads/_table.html", {
        "leads": enriched,
        "page": page,
        "total_pages": total_pages,
        "total_leads": total_leads,
    })


@router.get("/{lead_id}/messages")
async def lead_messages(
    request: Request,
    lead_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return HTML partial of chat messages for HTMX."""
    result = await session.execute(
        select(Message).where(Message.lead_id == lead_id).order_by(Message.created_at.asc())
    )
    messages = list(result.scalars().all())

    from app.web.templates import templates
    return templates.TemplateResponse(request, "leads/_messages.html", {
        "messages": messages,
    })


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: int,
    body: LeadUpdate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(lead, field, value)

    await session.commit()
    return {"ok": True}


@router.post("/{lead_id}/block")
async def block_lead(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Block a specific lead."""
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    
    lead.status = "blocked"
    await session.commit()
    
    # Return empty string with a trigger to update the leads list, and a toast message
    from fastapi import Response
    return Response(
        status_code=200, 
        content="", 
        headers={
            "HX-Trigger": '{"updateLeads": true, "showToast": {"message": "Lead bloqueado com sucesso!", "type": "warning"}}'
        }
    )


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Delete a specific lead and its messages."""
    # Check if lead exists
    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    
    # Delete associated messages first
    await session.execute(delete(Message).where(Message.lead_id == lead_id))
    
    # Delete the lead
    await session.delete(lead)
    await session.commit()
    
    # Return empty string with a trigger to update the leads list
    from fastapi import Response
    return Response(
        status_code=200, 
        content="", 
        headers={
            "HX-Trigger": '{"updateLeads": true, "showToast": {"message": "Lead deletado com sucesso!", "type": "success"}}'
        }
    )


@router.get("/export")
async def export_leads_csv(
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Export all leads as CSV."""
    result = await session.execute(select(Lead).order_by(Lead.created_at.desc()))
    leads = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nome", "Telefone", "Status", "Interesse", "Tabulação", "Criado em", "Última msg"])

    for lead in leads:
        writer.writerow([
            lead.id, lead.name or "", lead.phone, lead.status,
            lead.tab_interest_level or "", lead.tabulation or "",
            to_brtime(lead.created_at).strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
            to_brtime(lead.last_message_at).strftime("%Y-%m-%d %H:%M") if lead.last_message_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )
