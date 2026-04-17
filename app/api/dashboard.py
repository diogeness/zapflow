import structlog
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import select, func, col
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.auth import get_current_user
from app.models.instance import Instance
from app.models.campaign import Campaign, CampaignStep
from app.models.lead import Lead
from app.models.message import Message
from app.web.templates import BR_TZ

logger = structlog.get_logger()

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return HTML partial with dashboard stats for HTMX."""
    # Instance counts
    total_instances = (await session.execute(select(func.count(Instance.id)))).scalar() or 0
    connected_instances = (await session.execute(
        select(func.count(Instance.id)).where(Instance.status == "connected")
    )).scalar() or 0

    # Lead counts
    total_leads = (await session.execute(select(func.count(Lead.id)))).scalar() or 0
    active_leads = (await session.execute(
        select(func.count(Lead.id)).where(Lead.status == "active")
    )).scalar() or 0
    hot_leads = (await session.execute(
        select(func.count(Lead.id)).where(Lead.tab_interest_level == "hot")
    )).scalar() or 0

    # Campaign counts
    active_campaigns = (await session.execute(
        select(func.count(Campaign.id)).where(Campaign.is_active == True)
    )).scalar() or 0

    # Recent leads with last message content
    result = await session.execute(
        select(Lead)
        .where(Lead.status == "active")
        .order_by(Lead.last_message_at.desc().nullslast())
        .limit(10)
    )
    recent_leads_raw = list(result.scalars().all())

    recent_leads = []
    for lead in recent_leads_raw:
        # Get last message content
        msg_result = await session.execute(
            select(Message.content)
            .where(Message.lead_id == lead.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_content = msg_result.scalar_one_or_none()
        lead_dict = lead.model_dump()
        lead_dict["last_content"] = last_content
        recent_leads.append(type("LeadRow", (), lead_dict)())

    stats = type("Stats", (), {
        "total_instances": total_instances,
        "connected_instances": connected_instances,
        "total_leads": total_leads,
        "active_leads": active_leads,
        "hot_leads": hot_leads,
        "active_campaigns": active_campaigns,
        "recent_leads": recent_leads,
    })()

    from app.web.templates import templates
    return templates.TemplateResponse(request, "components/_dashboard_stats.html", {
        "stats": stats,
    })


@router.get("/campaigns-for-filter")
async def campaigns_for_filter(
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return campaigns list for dashboard filter dropdown."""
    result = await session.execute(
        select(Campaign.id, Campaign.name, Campaign.is_active)
        .order_by(Campaign.name)
    )
    return [
        {"id": row.id, "name": row.name, "is_active": row.is_active}
        for row in result.all()
    ]


@router.get("/funnel-chart")
async def funnel_chart(
    campaign_id: int,
    date_from: date = Query(default=None),
    date_to: date = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Return stacked bar chart data: leads per day grouped by current_step."""
    if date_to is None:
        date_to = datetime.now(BR_TZ).date()
    if date_from is None:
        date_from = date_to - timedelta(days=29)

    # Fetch campaign and its steps
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        return {"labels": [], "datasets": [], "campaign_name": ""}

    steps_result = await session.execute(
        select(CampaignStep.step_order)
        .where(CampaignStep.campaign_id == campaign_id)
        .order_by(CampaignStep.step_order)
    )
    step_orders = [row[0] for row in steps_result.all()]
    max_step = max(step_orders) if step_orders else 0

    # Build step labels: 0 = "Sem interação", 1..N = "Etapa N", -1 = "Fase IA"
    step_keys = list(range(0, max_step + 1)) + [-1]
    step_labels = {}
    step_labels[0] = "Sem interação"
    for s in range(1, max_step + 1):
        step_labels[s] = f"Etapa {s}"
    step_labels[-1] = "Fase IA"

    # Query leads grouped by date and current_step
    day_col = func.date(Lead.created_at).label("day")
    result = await session.execute(
        select(
            day_col,
            Lead.current_step,
            func.count(Lead.id),
        )
        .where(
            Lead.campaign_id == campaign_id,
            func.date(Lead.created_at) >= date_from.isoformat(),
            func.date(Lead.created_at) <= date_to.isoformat(),
        )
        .group_by(day_col, Lead.current_step)
        .order_by(day_col)
    )
    rows = result.all()

    # Build date range (all days, even with 0 leads)
    all_dates = []
    d = date_from
    while d <= date_to:
        all_dates.append(d.isoformat())
        d += timedelta(days=1)

    # Pivot data: {step: {date_str: count}}
    pivot = defaultdict(lambda: defaultdict(int))
    seen_steps = set()
    for day_val, step_val, count in rows:
        day_str = day_val.isoformat() if hasattr(day_val, "isoformat") else str(day_val)
        pivot[step_val][day_str] = count
        seen_steps.add(step_val)

    # Include only steps that have data or are in the campaign definition
    active_steps = sorted(
        [s for s in step_keys if s in seen_steps or s in step_orders],
        key=lambda x: (x == -1, x),  # -1 (AI) last
    )
    # Always include step 0 if there's any data
    if 0 not in active_steps and 0 in seen_steps:
        active_steps.insert(0, 0)

    # Color palette for steps
    colors = [
        "#64748b",  # 0: gray (sem interação)
        "#3b82f6",  # 1: blue
        "#8b5cf6",  # 2: violet
        "#ec4899",  # 3: pink
        "#f59e0b",  # 4: amber
        "#10b981",  # 5: emerald
        "#06b6d4",  # 6: cyan
        "#f97316",  # 7: orange
        "#ef4444",  # 8: red
        "#84cc16",  # 9: lime
    ]
    ai_color = "#22c55e"  # zap-500 for AI phase

    datasets = []
    for step in active_steps:
        label = step_labels.get(step, f"Etapa {step}")
        if step == -1:
            bg = ai_color
        elif step < len(colors):
            bg = colors[step]
        else:
            bg = colors[step % len(colors)]

        datasets.append({
            "label": label,
            "data": [pivot[step].get(d, 0) for d in all_dates],
            "backgroundColor": bg,
        })

    return {
        "labels": all_dates,
        "datasets": datasets,
        "campaign_name": campaign.name,
    }
