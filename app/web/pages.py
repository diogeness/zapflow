from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.web.deps import get_web_user
from app.web.templates import templates
from app.models.instance import Instance
from app.models.campaign import Campaign, CampaignStep
from app.models.lead import Lead

router = APIRouter(tags=["web"])


def _check_auth(user):
    """Return RedirectResponse if not authenticated, else None."""
    if user is None or isinstance(user, RedirectResponse):
        return RedirectResponse("/login", status_code=302)
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_web_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html")


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir
    return templates.TemplateResponse(request, "dashboard.html", {
        "username": user, "active_page": "dashboard",
    })


@router.get("/instances", response_class=HTMLResponse)
async def instances_page(request: Request):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir
    return templates.TemplateResponse(request, "instances/list.html", {
        "username": user, "active_page": "instances",
    })


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir

    # Get instances for the create form
    result = await session.execute(select(Instance).order_by(Instance.name))
    instances = list(result.scalars().all())

    return templates.TemplateResponse(request, "campaigns/list.html", {
        "username": user, "active_page": "campaigns",
        "instances": instances,
    })


@router.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def campaign_edit_page(
    request: Request,
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir

    result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        return RedirectResponse("/campaigns", status_code=302)

    # Get instance name
    inst_result = await session.execute(select(Instance.name).where(Instance.id == campaign.instance_id))
    instance_name = inst_result.scalar_one_or_none() or "?"

    # Get steps
    steps_result = await session.execute(
        select(CampaignStep)
        .where(CampaignStep.campaign_id == campaign_id)
        .order_by(CampaignStep.step_order)
    )
    steps = list(steps_result.scalars().all())

    return templates.TemplateResponse(request, "campaigns/edit.html", {
        "username": user, "active_page": "campaigns",
        "campaign": campaign, "instance_name": instance_name, "steps": steps,
    })


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir

    result = await session.execute(select(Campaign).order_by(Campaign.name))
    campaigns = list(result.scalars().all())

    return templates.TemplateResponse(request, "leads/list.html", {
        "username": user, "active_page": "leads",
        "campaigns": campaigns,
    })


@router.get("/leads/export")
async def export_redirect():
    return RedirectResponse("/api/leads/export", status_code=307)


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail_page(
    request: Request,
    lead_id: int,
    session: AsyncSession = Depends(get_session),
):
    user = get_web_user(request)
    redir = _check_auth(user)
    if redir:
        return redir

    result = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        return RedirectResponse("/leads", status_code=302)

    camp_result = await session.execute(select(Campaign.name).where(Campaign.id == lead.campaign_id))
    campaign_name = camp_result.scalar_one_or_none() or "?"

    return templates.TemplateResponse(request, "leads/detail.html", {
        "username": user, "active_page": "leads",
        "lead": lead, "campaign_name": campaign_name,
    })
