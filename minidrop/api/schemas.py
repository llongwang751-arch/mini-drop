from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    agent_id: str
    pid: int = Field(gt=0)
    duration: int = Field(default=5, ge=1, le=300)
    rate: int = Field(default=49, ge=1, le=999)
    collector: str
    continuous: bool = False


class NaturalLanguageRequest(BaseModel):
    text: str


class UploadPayload(BaseModel):
    raw: dict
    reason: str = "agent uploaded raw profile"


class FailurePayload(BaseModel):
    reason: str = "unspecified agent failure"


class ScheduleRequest(TaskRequest):
    interval_seconds: int = Field(default=300, ge=30, le=86400)
