from pydantic import BaseModel
from typing import Any


class WebhookPayload(BaseModel):
    event: str
    instance: str | None = None
    data: dict[str, Any] = {}
    destination: str | None = None
    server_url: str | None = None
    apikey: str | None = None
