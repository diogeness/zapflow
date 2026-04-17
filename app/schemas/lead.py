from pydantic import BaseModel


class LeadUpdate(BaseModel):
    status: str | None = None
    campaign_id: int | None = None
