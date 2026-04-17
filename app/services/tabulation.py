import asyncio
import structlog
from datetime import datetime, timezone, timedelta
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.message import Message
from app.services.groq_llm import GroqLLMClient
from app.config import Settings
from app.database import async_session

logger = structlog.get_logger()


class TabulationService:
    """Auto-tabulation: uses LLM to summarize lead interest level."""

    def __init__(self, settings: Settings, groq_llm: GroqLLMClient):
        self.groq_llm = groq_llm
        self.idle_minutes = settings.TABULATION_IDLE_MINUTES
        self._running = False

    async def tabulate_lead(self, session: AsyncSession, lead: Lead) -> None:
        """Generate tabulation for a specific lead."""
        result = await session.execute(
            select(Message)
            .where(Message.lead_id == lead.id)
            .order_by(Message.created_at.desc())
            .limit(20)
        )
        messages = list(result.scalars().all())
        messages.reverse()

        if len(messages) < 3:
            return  # Not enough context

        llm_messages = []
        for msg in messages:
            role = "assistant" if msg.direction == "outbound" else "user"
            llm_messages.append({"role": role, "content": msg.content})

        tab_result = await self.groq_llm.generate_tabulation(llm_messages)

        lead.tabulation = tab_result["tabulation"]
        lead.tab_interest_level = tab_result["interest_level"]
        lead.updated_at = datetime.now(timezone.utc)
        await session.commit()

        logger.info("tabulation.done", lead_id=lead.id, level=tab_result["interest_level"])

    async def check_idle_leads(self) -> None:
        """Background task: check for idle leads and tabulate them."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.idle_minutes)

        async with async_session() as session:
            result = await session.execute(
                select(Lead).where(
                    Lead.status == "active",
                    Lead.current_step == -1,  # AI phase only
                    Lead.last_message_at < cutoff,
                    Lead.tabulation.is_(None),
                )
            )
            leads = list(result.scalars().all())

            for lead in leads:
                try:
                    await self.tabulate_lead(session, lead)
                except Exception as e:
                    logger.error("tabulation.error", lead_id=lead.id, error=str(e))
                # Gentle pace for free tier
                await asyncio.sleep(3)

    async def run_periodic(self) -> None:
        """Run tabulation check periodically."""
        self._running = True
        logger.info("tabulation.periodic_started", interval_min=5)
        while self._running:
            try:
                await self.check_idle_leads()
            except Exception as e:
                logger.error("tabulation.periodic_error", error=str(e))
            await asyncio.sleep(300)  # Check every 5 minutes

    def stop(self):
        self._running = False
