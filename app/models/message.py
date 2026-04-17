from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    lead_id: int = Field(foreign_key="leads.id", index=True)
    direction: str = Field(max_length=10)  # inbound, outbound
    message_type: str = Field(max_length=20)  # text, image, audio, document
    content: str = Field(default="")  # text or transcription
    media_url: str | None = Field(default=None)
    evolution_msg_id: str | None = Field(default=None)
    is_from_ai: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
