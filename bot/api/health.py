from __future__ import annotations

from fastapi import FastAPI

from bot.context import ctx


def create_app() -> FastAPI:
    app = FastAPI(title="Temp Mail Xpress", docs_url=None, redoc_url=None)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "Temp Mail Xpress", "status": "running"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        database = await ctx().database.ping()
        try:
            await ctx().bot.get_me()
            telegram = "running"
        except Exception:
            telegram = "unavailable"
        try:
            await ctx().mailtm.domains()
            mailtm = "reachable"
        except Exception:
            mailtm = "unreachable"
        return {
            "status": "ok" if database and telegram == "running" else "degraded",
            "database": "connected" if database else "unavailable",
            "telegram": telegram,
            "mailtm": mailtm,
        }

    return app
