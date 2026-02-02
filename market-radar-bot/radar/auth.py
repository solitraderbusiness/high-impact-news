"""
Simple admin authentication for the admin panel.
Uses session-based auth with signed cookies.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request, Response, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from radar.config import get_settings


SESSION_COOKIE_NAME = "radar_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours


def get_serializer() -> URLSafeTimedSerializer:
    """Get the session serializer."""
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key)


def create_session_token(username: str) -> str:
    """Create a signed session token."""
    serializer = get_serializer()
    data = {
        "username": username,
        "created_at": datetime.utcnow().isoformat(),
    }
    return serializer.dumps(data)


def verify_session_token(token: str) -> Optional[dict]:
    """
    Verify and decode a session token.
    Returns the session data if valid, None otherwise.
    """
    if not token:
        return None

    serializer = get_serializer()
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


def verify_credentials(username: str, password: str) -> bool:
    """Verify admin credentials."""
    settings = get_settings()
    return (
        username == settings.admin_username and
        password == settings.admin_password
    )


def set_session_cookie(response: Response, username: str) -> None:
    """Set the session cookie on the response."""
    token = create_session_token(username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)


def get_current_session(request: Request) -> Optional[dict]:
    """Get the current session from the request cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


def require_auth(request: Request) -> dict:
    """
    Dependency that requires authentication.
    Raises HTTPException if not authenticated.
    """
    session = get_current_session(request)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return session


def require_auth_redirect(request: Request) -> Optional[dict]:
    """
    Check authentication for HTML pages.
    Returns None if not authenticated (caller should redirect).
    """
    return get_current_session(request)


class AuthMiddleware:
    """
    Middleware to check authentication for admin routes.
    Redirects to login if not authenticated.
    """

    def __init__(self, app, exclude_paths: list[str] = None):
        self.app = app
        self.exclude_paths = exclude_paths or ["/admin/login", "/admin/static"]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # Check if path should be excluded from auth
        for exclude in self.exclude_paths:
            if path.startswith(exclude):
                await self.app(scope, receive, send)
                return

        # Only check auth for admin paths
        if path.startswith("/admin"):
            # Build a minimal request to check cookies
            from starlette.requests import Request
            request = Request(scope, receive)
            session = get_current_session(request)

            if not session:
                # Redirect to login
                from starlette.responses import RedirectResponse
                response = RedirectResponse(url="/admin/login", status_code=302)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
