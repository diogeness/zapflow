from pydantic import BaseModel


class CampaignCreate(BaseModel):
    instance_id: int
    name: str
    system_prompt: str = ""
    ai_model: str = "llama-3.1-8b-instant"
    ai_temperature: float = 0.7


class CampaignUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    ai_model: str | None = None
    ai_temperature: float | None = None
    is_active: bool | None = None
    ignore_groups: bool | None = None
    ignore_contacts: bool | None = None
    ai_enabled: bool | None = None
    max_ai_interactions: int | None = None


class CampaignClone(BaseModel):
    instance_id: int
    name: str


class StepCreate(BaseModel):
    message_type: str
    content: str = ""
    media_url: str | None = None
    delay_seconds: int = 3
    wait_reply: bool = True


class StepUpdate(BaseModel):
    content: str | None = None
    delay_seconds: int | None = None
    wait_reply: bool | None = None
    view_once: bool | None = None


class StepReorder(BaseModel):
    step_ids: list[int]
