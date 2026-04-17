from sqlmodel import SQLModel, Field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=50)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)
