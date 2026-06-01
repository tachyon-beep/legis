"""FastAPI application factory.

The read API mirrors Clarion's consumer model: consumers are HTTP clients.
Sprint 0 ships only the health endpoint; the git/CI surfaces (Sprint 1) and the
enforcement surfaces (Sprint 2+) mount here later.
"""

from __future__ import annotations

from fastapi import FastAPI

from legis import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="legis", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "legis", "version": __version__}

    return app
