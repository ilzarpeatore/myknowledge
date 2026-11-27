import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kos.api.routes import (
    ai,
    analysis,
    auth,
    definitions,
    evidence,
    extraction,
    graph,
    knowledge,
    learning,
    library,
    queries,
    references,
    search,
    sections,
    study,
    system,
    tokens,
)
from kos.config import get_settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    settings = get_settings()
    from kos.mcp_server.server import asgi_app

    mcp_app, mcp = asgi_app(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Knowledge OS API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.mcp = mcp

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
    async def mcp_path(request: Request, call_next):
        # Clients are configured with ".../api/mcp"; the mounted app lives at ".../api/mcp/".
        if request.scope["path"] == "/api/mcp":
            request.scope["path"] = "/api/mcp/"
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
    app.include_router(knowledge.router)
    app.include_router(learning.router)
    app.include_router(graph.router)
    app.include_router(extraction.router)
    app.include_router(evidence.router)
    app.include_router(tokens.router)
    app.include_router(queries.router)
    app.include_router(study.router)
    app.include_router(sections.router)
    app.include_router(references.router)
    app.include_router(analysis.router)
    app.include_router(definitions.router)
    app.mount("/api/mcp", mcp_app)
    return app


app = create_app()
