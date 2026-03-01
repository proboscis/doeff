"""Internal validation helpers for doeff-time."""


from datetime import datetime


def ensure_aware_datetime(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware datetime")
    return value
