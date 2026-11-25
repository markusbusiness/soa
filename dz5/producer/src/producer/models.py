import uuid
from datetime import timezone
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

INT32_MAX = 2**31 - 1


class EventType(StrEnum):
    VIEW_STARTED = "VIEW_STARTED"
    VIEW_FINISHED = "VIEW_FINISHED"
    VIEW_PAUSED = "VIEW_PAUSED"
    VIEW_RESUMED = "VIEW_RESUMED"
    LIKED = "LIKED"
    SEARCHED = "SEARCHED"


VIEW_EVENTS = {EventType.VIEW_STARTED, EventType.VIEW_FINISHED, EventType.VIEW_PAUSED, EventType.VIEW_RESUMED}


class DeviceType(StrEnum):
    MOBILE = "MOBILE"
    DESKTOP = "DESKTOP"
    TV = "TV"
    TABLET = "TABLET"


class MovieEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID | None = None
    user_id: str = Field(min_length=1, max_length=128)
    movie_id: str = Field(min_length=1, max_length=128)
    event_type: EventType
    timestamp: AwareDatetime
    device_type: DeviceType
    session_id: str = Field(min_length=1, max_length=128)
    progress_seconds: int | None = Field(default=None, ge=0, le=INT32_MAX)
    search_query: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def check_event_specific_fields(self) -> "MovieEventIn":
        if self.event_type in VIEW_EVENTS and self.progress_seconds is None:
            raise ValueError(f"progress_seconds is required for {self.event_type}")
        if self.search_query is not None and self.event_type is not EventType.SEARCHED:
            raise ValueError("search_query is allowed only for SEARCHED")
        return self

    def to_record(self) -> dict:
        return {
            "event_id": str(self.event_id or uuid.uuid4()),
            "user_id": self.user_id,
            "movie_id": self.movie_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.astimezone(timezone.utc),
            "device_type": self.device_type.value,
            "session_id": self.session_id,
            "progress_seconds": self.progress_seconds,
            "search_query": self.search_query,
        }


class PublishedEvent(BaseModel):
    event_id: str
    partition: int
    offset: int
