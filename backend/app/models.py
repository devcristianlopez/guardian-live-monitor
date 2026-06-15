from pydantic import BaseModel, field_validator


class EventPayload(BaseModel):
    camera_id: str
    event_type: str
    severity: str
    confidence: float

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        allowed = {"low", "medium", "high"}
        if value.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return value.lower()
