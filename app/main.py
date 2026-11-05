"""CartPilot backend entry point.

    uvicorn app.main:app --reload
    open http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.routes import router

DESCRIPTION = """
CartPilot – an autonomous shopping agent for quick commerce (AgentX Hackathon 2026, PS-07).

* Plans a basket, queries Blinkit / Zepto / Swiggy Instamart **through tools**, computes the true landed cost,
  splits carts when cheaper, re-verifies everything, and **waits for explicit approval**.
* **Never pays and never places orders.** Approved carts are only *prepared*.
* All prices, fees and ETAs come from platform tools (mock data – realistic, swappable via the adapter).
"""


def create_app() -> FastAPI:
    app = FastAPI(title="CartPilot API", version="1.0.0", description=DESCRIPTION)
    origins = os.getenv("CARTPILOT_CORS_ORIGINS", "http://localhost:3000,http://localhost:8501").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    @app.exception_handler(ValidationError)
    async def _invalid(_: Request, exc: ValidationError):  # internal model validation -> 422, never 500
        return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_INPUT",
                                                                 "message": exc.errors(include_url=False)}})

    return app


app = create_app()
