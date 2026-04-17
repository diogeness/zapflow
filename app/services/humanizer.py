import asyncio
import random
import structlog
from app.config import Settings
from app.services.waha import WhatsAppClient

logger = structlog.get_logger()


class Humanizer:
    """Simulates human-like delays and typing/recording presence before messages."""

    def __init__(self, settings: Settings, waha: WhatsAppClient):
        self.waha = waha
        self.min_delay = settings.HUMANIZER_MIN_DELAY
        self.max_delay = settings.HUMANIZER_MAX_DELAY

    def _calculate_delay(self, text: str) -> float:
        """Calculate a human-like delay based on text length."""
        # ~50ms per character + 1-3s random jitter
        base = len(text) * 0.05
        jitter = random.uniform(1.0, 3.0)
        delay = base + jitter
        return max(self.min_delay, min(delay, self.max_delay))

    async def simulate_typing(self, instance: str, number: str, text: str) -> None:
        """Show 'typing...' status and wait a human-like delay."""
        delay = self._calculate_delay(text)
        await self.waha.set_presence(instance, number, "typing")
        logger.debug("humanizer.typing", delay=f"{delay:.1f}s", chars=len(text))
        await asyncio.sleep(delay)

    async def simulate_recording(self, instance: str, number: str, duration: float = 5.0) -> None:
        """Show 'recording audio...' status and wait."""
        delay = max(self.min_delay, min(duration * 0.5, self.max_delay))
        await self.waha.set_presence(instance, number, "recording")
        logger.debug("humanizer.recording", delay=f"{delay:.1f}s")
        await asyncio.sleep(delay)

    async def step_delay(self, seconds: int) -> None:
        """Simple delay between funnel steps with slight randomization."""
        jitter = random.uniform(0.5, 1.5)
        await asyncio.sleep(seconds + jitter)
