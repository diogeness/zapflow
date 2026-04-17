from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class Instance(SQLModel, table=True):
    __tablename__ = "instances"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=100)
    phone_number: str | None = Field(default=None, max_length=20)
    status: str = Field(default="disconnected", max_length=20)  # disconnected, connecting, connected
    api_key: str | None = Field(default=None)
    saved_contacts: str | None = Field(default=None)  # JSON array of phone numbers from phonebook
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
