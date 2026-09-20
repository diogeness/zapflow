import asyncio
import tempfile
import structlog
from groq import AsyncGroq
from app.config import Settings

logger = structlog.get_logger()


class GroqWhisperClient:
    """Client for Groq Whisper — audio transcription with rate limiting."""

    def __init__(self, settings: Settings):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model = settings.GROQ_WHISPER_MODEL
        self._semaphore = asyncio.Semaphore(2)

    async def transcribe(self, audio_bytes: bytes, language: str = "pt") -> str:
        """Transcribe audio bytes to text."""
        async with self._semaphore:
            try:
                # Write to temp file (Groq SDK requires file-like object)
                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
                    tmp.write(audio_bytes)
                    tmp.flush()
                    tmp.seek(0)

                    transcription = await self.client.audio.transcriptions.create(
                        file=("audio.ogg", tmp.read()),
                        model=self.model,
                        language=language,
                        response_format="text",
                    )

                text = str(transcription).strip()
                logger.info("groq.whisper_transcribed", length=len(text))
                return text
            except Exception as e:
                logger.error("groq.whisper_error", error=str(e))
                return "[Error transcribing audio]"
