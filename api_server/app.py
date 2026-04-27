"""FastAPI application assembly."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .core.config import APP_VERSION, CORS_ALLOW_ORIGINS
from .core.errors import (
    openai_http_exception_handler,
    openai_unhandled_exception_handler,
    openai_validation_exception_handler,
)
from .core.lifecycle import lifespan
from .core.middleware import GracefulShutdownMiddleware, RequestLoggingMiddleware
from .routers.admin import router as admin_router
from .routers.inference import router as inference_router
from .routers.memory import router as memory_router
from .routers.models import router as models_router
from .routers.root_health import router as root_health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ruyi Serving API",
        description=(
            "OpenAI API-compatible serving interface backed by the Ruyi Compute Server. "
            "Model metadata stays on the compute side; this API layer exposes normalized "
            "discovery, health, metrics, cancellation, and inference endpoints."
        ),
        version=APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=bool(CORS_ALLOW_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(GracefulShutdownMiddleware)

    app.add_exception_handler(HTTPException, openai_http_exception_handler)
    app.add_exception_handler(RequestValidationError, openai_validation_exception_handler)
    app.add_exception_handler(Exception, openai_unhandled_exception_handler)

    app.include_router(root_health_router)
    app.include_router(models_router)
    app.include_router(inference_router)
    app.include_router(memory_router)
    app.include_router(admin_router)

    return app


app = create_app()
