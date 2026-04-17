from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "ZapFlow"
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "CHANGE_ME"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    APP_BASE_URL: str = "http://localhost:8000"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/zapflow.db"

    # WhatsApp Service (Baileys microservice)
    WA_SERVICE_URL: str = "http://localhost:3100"
    WA_SERVICE_API_KEY: str = ""
    WA_WEBHOOK_CALLBACK_URL: str = ""  # Internal URL reachable from whatsapp-service container

    # Groq
    GROQ_API_KEY: str = ""
    GROQ_DEFAULT_MODEL: str = "llama-3.1-8b-instant"
    GROQ_WHISPER_MODEL: str = "whisper-large-v3-turbo"
    GROQ_MAX_CONCURRENT: int = 2
    GROQ_MIN_INTERVAL: float = 2.0

    # Tunning
    HUMANIZER_MIN_DELAY: float = 2.0
    HUMANIZER_MAX_DELAY: float = 15.0
    HISTORY_LIMIT: int = 10
    TABULATION_IDLE_MINUTES: int = 30
    AI_DEBOUNCE_SECONDS: float = 5.0
    CONTEXT_FILE_MAX_SIZE: int = 51200  # 50KB

    # Cloudflare Tunnel (used by cloudflared container, not by the app)
    CLOUDFLARE_TUNNEL_TOKEN: str = ""

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
