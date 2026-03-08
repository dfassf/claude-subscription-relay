from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class AskRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000)
    system_prompt: str | None = Field(None, max_length=5000)
    output_format: str = Field("text", pattern="^(text|json)$")
    timeout: int | None = Field(None, ge=10, le=600)


class AskResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.queued


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    result: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    queue_size: int = 0
    current_task: str | None = None
