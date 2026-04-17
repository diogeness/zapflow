from datetime import datetime, timezone, timedelta
from fastapi.templating import Jinja2Templates

# Brazil timezone (UTC-3)
BR_TZ = timezone(timedelta(hours=-3))


def to_brtime(dt: datetime) -> datetime:
    """Convert a UTC datetime to Brazil time (UTC-3)."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BR_TZ)


def brtime_filter(dt: datetime, fmt: str = "%d/%m %H:%M") -> str:
    """Jinja2 filter: convert UTC datetime to Brazil time and format."""
    if dt is None:
        return ""
    return to_brtime(dt).strftime(fmt)


def get_templates() -> Jinja2Templates:
    """Return Jinja2Templates instance with custom filters registered."""
    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters["brtime"] = brtime_filter
    return templates


# Singleton instance
templates = get_templates()
