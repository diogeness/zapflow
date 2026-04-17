from pydantic import BaseModel


class InstanceCreate(BaseModel):
    name: str


class InstanceResponse(BaseModel):
    id: int
    name: str
    phone_number: str | None
    status: str
