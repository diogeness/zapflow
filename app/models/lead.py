from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class Lead(SQLModel, table=True):
    __tablename__ = "leads"

    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    instance_id: int = Field(foreign_key="instances.id", index=True)
    phone: str = Field(max_length=50, index=True)
    name: str | None = Field(default=None, max_length=200)
    current_step: int = Field(default=0)  # 0..N = fixed phase, -1 = AI phase
    status: str = Field(default="active", max_length=20)  # active, completed, paused, blocked
    tabulation: str | None = Field(default=None)
    tab_interest_level: str | None = Field(default=None, max_length=20)  # hot, warm, cold, unresponsive
    ai_interactions_count: int = Field(default=0)  # count of AI responses sent to this lead
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_message_at: datetime | None = Field(default=None)
