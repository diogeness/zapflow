import json

from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"

    id: int | None = Field(default=None, primary_key=True)
    instance_id: int = Field(foreign_key="instances.id", index=True)
    name: str = Field(max_length=200)
    system_prompt: str = Field(default="")
    ai_model: str = Field(default="llama-3.1-8b-instant", max_length=100)
    ai_temperature: float = Field(default=0.7)
    is_active: bool = Field(default=False)
    context_file: str | None = Field(default=None)  # path to knowledge base file
    ignore_groups: bool = Field(default=True)
    ignore_contacts: bool = Field(default=False)  # ignore contacts saved in phonebook
    ai_enabled: bool = Field(default=True)  # enable/disable AI phase after fixed steps
    max_ai_interactions: int = Field(default=0)  # 0 = unlimited, >0 = max AI responses per lead
    created_at: datetime = Field(default_factory=utcnow)


class CampaignStep(SQLModel, table=True):
    __tablename__ = "campaign_steps"

    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    step_order: int = Field(default=0)
    message_type: str = Field(max_length=20)  # text, image, video, document, audio_ptt
    content: str = Field(default="")  # message text or caption
    media_urls: str | None = Field(default=None)  # JSON array: [{"path": "...", "name": "..."}]
    delay_seconds: int = Field(default=3)
    wait_reply: bool = Field(default=True)
    view_once: bool = Field(default=False)  # send as view-once (image/video only)

    @property
    def media_urls_parsed(self) -> list[dict]:
        if not self.media_urls:
            return []
        try:
            return json.loads(self.media_urls)
        except (json.JSONDecodeError, TypeError):
            return []
