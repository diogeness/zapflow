import asyncio
import structlog
from groq import AsyncGroq
from app.config import Settings

logger = structlog.get_logger()


class GroqLLMClient:
    """Client for Groq Cloud LLM — chat completions with rate limiting."""

    def __init__(self, settings: Settings):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.default_model = settings.GROQ_DEFAULT_MODEL
        self._semaphore = asyncio.Semaphore(settings.GROQ_MAX_CONCURRENT)
        self._min_interval = settings.GROQ_MIN_INTERVAL
        self._last_call = 0.0

    async def _rate_limit(self):
        """Ensure minimum gap between API calls to stay within rate limits."""
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = asyncio.get_event_loop().time()

    async def generate_response(
        self,
        system_prompt: str,
        messages_history: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Generate a chat response from the LLM."""
        await self._rate_limit()

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(messages_history)

        try:
            completion = await self.client.chat.completions.create(
                messages=messages,
                model=model or self.default_model,
                temperature=temperature,
                max_tokens=1024,
            )
            content = completion.choices[0].message.content
            logger.info("groq.llm_response", model=model or self.default_model, tokens=completion.usage.total_tokens)
            return content or ""
        except Exception as e:
            logger.error("groq.llm_error", error=str(e))
            raise

    async def generate_tabulation(self, messages_history: list[dict]) -> dict:
        """Generate a tabulation summary for a lead's conversation."""
        await self._rate_limit()

        system = (
            "You are a sales analyst. Analyze the conversation history and return EXACTLY in the following format:\n"
            "SUMMARY: (2 short sentences describing the customer's interest)\n"
            "LEVEL: (hot|warm|cold|unresponsive)\n"
            "INTEREST: (product or service of interest, or 'undefined')\n\n"
            "Rules for LEVEL:\n"
            "- hot: clear intent to buy (asked for price, wants to close)\n"
            "- warm: asked questions or showed curiosity but no decision yet\n"
            "- cold: uninterested or gave short answers\n"
            "- unresponsive: did not reply or abandoned the conversation"
        )

        messages = [{"role": "system", "content": system}]
        messages.extend(messages_history)

        try:
            completion = await self.client.chat.completions.create(
                messages=messages,
                model=self.default_model,
                temperature=0.3,
                max_tokens=300,
            )
            text = completion.choices[0].message.content or ""

            # Parse response
            result = {"tabulation": text, "interest_level": "unresponsive"}
            for line in text.split("\n"):
                line = line.strip()
                if line.upper().startswith("NIVEL:"):
                    level = line.split(":", 1)[1].strip().lower()
                    if level in ("hot", "warm", "cold", "unresponsive"):
                        result["interest_level"] = level

            logger.info("groq.tabulation_done", level=result["interest_level"])
            return result
        except Exception as e:
            logger.error("groq.tabulation_error", error=str(e))
            return {"tabulation": "Tabulation error", "interest_level": "unresponsive"}
