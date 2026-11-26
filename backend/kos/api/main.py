from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kos.api.routes import ai, auth, library, search, system
from kos.config import get_settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Knowledge OS API", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def origin_check(request: Request, call_next):
        """CSRF defence: a state-changing request that carries the session cookie
        must come from the app's own origin. Requests without the cookie (e.g. the
        login form, or future token-authenticated MCP clients) are not affected."""
        if (
            request.method in UNSAFE_METHODS
            and settings.session_cookie_name in request.cookies
            and request.headers.get("origin") != settings.app_origin
        ):
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(library.router)
    app.include_router(search.router)
    app.include_router(ai.router)
    return app


app = create_app()
