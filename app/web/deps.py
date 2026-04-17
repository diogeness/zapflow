from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse
from app.api.auth import verify_token


async def require_login(request: Request) -> str:
    """Dependency for web routes — redirects to login if not authenticated."""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login", status_code=302)
    username = verify_token(token)
    if not username:
        return RedirectResponse("/login", status_code=302)
    return username


def get_web_user(request: Request) -> str | None:
    """Get current user from cookie without raising — returns None if not logged in."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    return verify_token(token)
