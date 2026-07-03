from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current time as naive UTC — the convention for all stored datetimes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
